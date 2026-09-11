from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import asyncio

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
    region: str = "kr"

@app.get("/")
@app.get("/store")
@app.get("/api/store")
async def check_status():
    return {"status": "ok", "message": "Valorant Store API is running"}

async def fetch_skin_info(client: httpx.AsyncClient, item_id: str, discount: int = 0):
    try:
        url = f"https://valorant-api.com/v1/weapons/skinlevel/{item_id}?language=ko-KR"
        res = await client.get(url, timeout=5.0)
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
async def get_valorant_store(data: StoreRequest):
    access_token = data.access_token.strip()
    if not access_token:
        raise HTTPException(status_code=400, detail="access_token이 필요합니다.")

    client_platform = "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjogIldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5MDQ1LjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"
    user_agent = "RiotClient/93.0.1.2132.4019 rso-auth (Windows;10;10.0.19045.1.256.64bit)"

    async with httpx.AsyncClient(follow_redirects=True) as client:
        try:
            # 1. 라이엇 최신 클라이언트 버전 동적 파싱
            version_res = await client.get("https://valorant-api.com/v1/version")
            client_version = "release-09.03-shipping-9-2708302"
            if version_res.status_code == 200:
                client_version = version_res.json().get("data", {}).get("riotClientVersion", client_version)

            # 2. Entitlements Token 발급
            ent_res = await client.post(
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
            user_res = await client.get(
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

            # 4. 계정의 실제 서버 지역(PAS Shard) 조회 시도
            detected_shard = None
            try:
                pas_res = await client.put(
                    "https://riot-geo.pas.games.riotgames.com/pas/v1/product/valorant",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "Content-Type": "application/json"
                    },
                    json={"id_token": access_token},
                    timeout=5.0
                )
                if pas_res.status_code == 200:
                    detected_shard = pas_res.json().get("affiliateities", {}).get("valorant", {}).get("shard")
            except Exception:
                pass

            # 탐색할 지역 목록 정리
            regions_to_try = []
            if detected_shard:
                regions_to_try.append(detected_shard.lower())
            regions_to_try.extend([data.region.lower(), "kr", "ap", "na", "eu", "latam", "br"])
            
            # 중복 제거 (순서 유지)
            regions_to_try = list(dict.fromkeys(regions_to_try))

            store_data = None
            last_status = 404
            last_text = ""

            # 5. 각 서버 엔드포인트 순차 호출
            for reg in regions_to_try:
                region_host = f"pd.{reg}"
                store_headers = {
                    "Authorization": f"Bearer {access_token}",
                    "X-Riot-Entitlements-JWT": entitlements_token,
                    "X-Riot-ClientPlatform": client_platform,
                    "X-Riot-ClientVersion": client_version,
                    "User-Agent": user_agent
                }

                store_res = await client.get(
                    f"https://{region_host}.a.pvp.net/store/v2/storefront/{puuid}",
                    headers=store_headers,
                    timeout=10.0
                )

                if store_res.status_code == 200:
                    store_data = store_res.json()
                    break
                else:
                    last_status = store_res.status_code
                    last_text = store_res.text

            if not store_data:
                raise HTTPException(
                    status_code=last_status,
                    detail="발로란트 계정 정보를 찾을 수 없습니다. 해당 라이엇 계정으로 발로란트 게임에 최소 1회 접속한 적이 있는지 확인해 주세요."
                )

            # 6. 스킨 정보 비동기 파싱
            daily_item_ids = store_data.get("SkinsPanelLayout", {}).get("SingleItemOffers", [])
            bonus_store = store_data.get("BonusStore", {}).get("BonusStoreOffers", [])

            daily_tasks = [fetch_skin_info(client, item_id) for item_id in daily_item_ids]
            night_tasks = [
                fetch_skin_info(
                    client,
                    offer.get("Offer", {}).get("OfferID"),
                    offer.get("DiscountPercent", 0)
                )
                for offer in bonus_store
            ]

            daily_results = await asyncio.gather(*daily_tasks)
            night_results = await asyncio.gather(*night_tasks)

            daily_skins = [s for s in daily_results if s is not None]
            night_skins = [s for s in night_results if s is not None]

            return {
                "success": True,
                "daily_store": daily_skins,
                "night_market": night_skins
            }

        except HTTPException as he:
            raise he
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"서버 처리 오류: {str(e)}")
