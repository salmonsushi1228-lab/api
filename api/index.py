from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
from concurrent.futures import ThreadPoolExecutor

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class StoreRequest(BaseModel):
    access_token: str
    id_token: str = None  # PAS 서버 지역 감지용 ID 토큰 (선택)
    region: str = "kr"

@app.get("/")
@app.get("/store")
@app.get("/api/store")
def check_status():
    return {"status": "ok", "message": "Valorant Store API is running"}

def fetch_skin_info(item_id: str, discount: int = 0):
    """단일 스킨 정보를 가져오는 독립 함수 (스레드 풀용)"""
    try:
        url = f"https://valorant-api.com/v1/weapons/skinlevel/{item_id}?language=ko-KR"
        res = requests.get(url, timeout=5.0)
        if res.status_code == 200:
            data_obj = res.json().get("data", {})
            skin_data = {
                "name": data_obj.get("displayName"),
                "icon": data_obj.get("displayIcon")
            }
            if discount > 0:
                skin_data["discountPercent"] = discount
            return skin_data
    except Exception:
        pass
    return None

@app.post("/")
@app.post("/store")
@app.post("/api/store")
def get_valorant_store(data: StoreRequest):
    access_token = data.access_token.strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="access_token이 필요합니다.")

    client_platform = "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjogIldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5MDQ1LjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"
    user_agent = "RiotClient/93.0.1.2132.4019 rso-auth (Windows;10;10.0.19045.1.256.64bit)"

    # 라이엇 실제 API 도메인 매핑 (LATAM/BR -> pd.na)
    region_map = {
        "kr": "pd.kr",
        "ap": "pd.ap",
        "na": "pd.na",
        "eu": "pd.eu",
        "latam": "pd.na",
        "br": "pd.na"
    }

    with requests.Session() as session:
        try:
            # 1. 최신 발로란트 클라이언트 버전 파싱
            client_version = "release-09.03-shipping-9-2708302"
            try:
                version_res = session.get("https://valorant-api.com/v1/version", timeout=5.0)
                if version_res.status_code == 200:
                    client_version = version_res.json().get("data", {}).get("riotClientVersion", client_version)
            except Exception:
                pass

            # 2. Entitlements Token 발급
            ent_res = session.post(
                "https://entitlements.auth.riotgames.com/api/token/v1",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": user_agent,
                    "Content-Type": "application/json"
                },
                json={},
                timeout=10.0
            )
            if ent_res.status_code != 200:
                raise HTTPException(status_code=401, detail="유효하지 않거나 만료된 Access Token입니다.")
            
            entitlements_token = ent_res.json().get("entitlements_token")

            # 3. PUUID 취득
            user_res = session.get(
                "https://auth.riotgames.com/userinfo",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "User-Agent": user_agent
                },
                timeout=10.0
            )
            if user_res.status_code != 200:
                raise HTTPException(status_code=401, detail="사용자 정보를 가져올 수 없습니다.")
            
            puuid = user_res.json().get("sub")
            if not puuid:
                raise HTTPException(status_code=400, detail="사용자 PUUID 추출 실패")

            # 4. 계정 서버 지역(PAS Shard) 감지 시도 (id_token 사용)
            detected_shard = None
            token_for_pas = data.id_token.strip() if data.id_token else access_token
            try:
                pas_res = session.put(
                    "https://riot-geo.pas.games.riotgames.com/pas/v1/product/valorant",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json"
                    },
                    json={"id_token": token_for_pas},
                    timeout=5.0
                )
                if pas_res.status_code == 200:
                    detected_shard = pas_res.json().get("affiliateities", {}).get("valorant", {}).get("shard")
            except Exception:
                pass

            # 탐색할 후보 지역 정리
            raw_regions = []
            if detected_shard:
                raw_regions.append(detected_shard.lower())
            raw_regions.extend([data.region.lower(), "kr", "ap", "na", "eu"])
            
            # 중복 제거 및 호스트 변환
            host_candidates = []
            for r in raw_regions:
                host = region_map.get(r, f"pd.{r}")
                if host not in host_candidates:
                    host_candidates.append(host)

            store_data = None
            last_status = 404

            # 5. 상점 엔드포인트 순차 조회
            for region_host in host_candidates:
                store_headers = {
                    "Authorization": f"Bearer {access_token}",
                    "X-Riot-Entitlements-JWT": entitlements_token,
                    "X-Riot-ClientPlatform": client_platform,
                    "X-Riot-ClientVersion": client_version,
                    "User-Agent": user_agent
                }

                try:
                    store_res = session.get(
                        f"https://{region_host}.a.pvp.net/store/v2/storefront/{puuid}",
                        headers=store_headers,
                        timeout=10.0
                    )

                    if store_res.status_code == 200:
                        store_data = store_res.json()
                        break
                    else:
                        last_status = store_res.status_code
                except Exception:
                    continue

            if not store_data:
                raise HTTPException(
                    status_code=last_status,
                    detail="발로란트 계정 정보를 찾을 수 없습니다. 해당 라이엇 계정으로 PC 발로란트에 최소 1회 접속한 적이 있는지 확인해 주세요."
                )

            # 6. 스킨 정보 병렬 파싱 (ThreadPoolExecutor 활용)
            daily_item_ids = store_data.get("SkinsPanelLayout", {}).get("SingleItemOffers", [])
            bonus_store = store_data.get("BonusStore", {}).get("BonusStoreOffers", [])

            daily_skins = []
            night_skins = []

            with ThreadPoolExecutor(max_workers=10) as executor:
                # 일일 상점 병렬 요청
                daily_futures = [executor.submit(fetch_skin_info, item_id) for item_id in daily_item_ids]
                for future in daily_futures:
                    res = future.result()
                    if res:
                        daily_skins.append(res)

                # 야시장 병렬 요청
                night_futures = [
                    executor.submit(
                        fetch_skin_info,
                        offer.get("Offer", {}).get("OfferID"),
                        offer.get("DiscountPercent", 0)
                    )
                    for offer in bonus_store
                ]
                for future in night_futures:
                    res = future.result()
                    if res:
                        night_skins.append(res)

            return {
                "success": True,
                "daily_store": daily_skins,
                "night_market": night_skins
            }

        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"상점 요청 처리 중 오류가 발생했습니다: {str(e)}")
