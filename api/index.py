from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import httpx
import asyncio
import json
import re

app = FastAPI()

# CORS 설정
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class LoginRequest(BaseModel):
    username: str
    password: str
    region: str = "kr"

# 헬스체크 (Vercel Rewrites 대응: /, /store, /api/store)
@app.get("/")
@app.get("/store")
@app.get("/api/store")
async def check_status():
    return {"status": "ok", "message": "Valorant Store API is running"}

# 단일 스킨 정보 비동기 파싱
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
async def get_valorant_store(data: LoginRequest):
    # 최신 Riot Client 헤더 및 Client-Platform 명세
    client_platform = "ew0KCSJwbGF0Zm9ybVR5cGUiOiAiUEMiLA0KCSJwbGF0Zm9ybU9TIjogIldpbmRvd3MiLA0KCSJwbGF0Zm9ybU9TVmVyc2lvbiI6ICIxMC4wLjE5MDQ1LjEuMjU2LjY0Yml0IiwNCgkicGxhdGZvcm1DaGlwc2V0IjogIlVua25vd24iDQp9"
    user_agent = "RiotClient/93.0.1.2132.4019 rso-auth (Windows;10;10.0.19045.1.256.64bit)"

    async with httpx.AsyncClient(follow_redirects=True, cookies=httpx.Cookies()) as client:
        headers = {
            'User-Agent': user_agent,
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/plain, */*',
            'X-Riot-ClientPlatform': client_platform,
            'X-Riot-ClientVersion': 'release-09.03-shipping-9-2708302'
        }
        client.headers.update(headers)

        # 1. Auth 세션 초기화 (POST)
        auth_body = {
            "client_id": "play-valorant-web-prod",
            "nonce": "1",
            "redirect_uri": "https://playvalorant.com/opt_in",
            "response_type": "token id_token",
            "scope": "account openid"
        }

        try:
            init_res = await client.post(
                "https://auth.riotgames.com/api/v1/authorization",
                json=auth_body,
                timeout=10.0
            )
            init_res.raise_for_status()

            # 2. 자격 증명 전송 (PUT)
            login_body = {
                "type": "auth",
                "username": data.username.strip(),
                "password": data.password,
                "remember": True
            }

            login_res = await client.put(
                "https://auth.riotgames.com/api/v1/authorization",
                json=login_body,
                timeout=10.0
            )
            login_res.raise_for_status()
            login_json = login_res.json()

            response_type = login_json.get("type")

            # 오류 및 인증 예외 분기
            if response_type == "error":
                err = login_json.get("error")
                if err == "auth_failure":
                    raise HTTPException(
                        status_code=400,
                        detail="아이디 또는 비밀번호가 올바르지 않습니다. (Riot ID 태그가 아닌 '로그인용 계정명'을 입력했는지 확인해 주세요.)"
                    )
                elif err == "rate_limited":
                    raise HTTPException(
                        status_code=429,
                        detail="로그인 시도 횟수가 너무 많습니다. 잠시 후 다시 시도해 주세요."
                    )
                else:
                    raise HTTPException(status_code=400, detail=f"인증 실패: {err}")

            if response_type == "multifactor":
                raise HTTPException(
                    status_code=400,
                    detail="계정에 2단계 인증(MFA)이 설정되어 있습니다. 라이엇 계정 관리에서 2단계 인증을 해제해 주세요."
                )

            if response_type == "captcha":
                raise HTTPException(
                    status_code=400,
                    detail="보안 캡차(Captcha)가 동작 중입니다. 라이엇 공식 웹사이트에서 직접 로그인하여 캡차를 해제해 주세요."
                )

            # 3. Access Token URI 파싱
            response_data = login_json.get("response", {})
            parameters = response_data.get("parameters", {})
            uri = parameters.get("uri", "")

            if not uri:
                # 응답 구조 디버깅용 메시지
                raise HTTPException(
                    status_code=400,
                    detail=f"인증 URI를 취득하지 못했습니다. (라이엇 응답 유형: {response_type})"
                )

            access_token_match = re.search(r'access_token=([^&]+)', uri)
            if not access_token_match:
                raise HTTPException(status_code=400, detail="액세스 토큰 파싱에 실패했습니다.")
            access_token = access_token_match.group(1)

            # 4. Entitlements Token 발급
            ent_res = await client.post(
                "https://entitlements.auth.riotgames.com/api/token/v1",
                headers={"Authorization": f"Bearer {access_token}"},
                json={},
                timeout=10.0
            )
            ent_res.raise_for_status()
            entitlements_token = ent_res.json().get("entitlements_token")

            # 5. PUUID 취득
            user_res = await client.get(
                "https://auth.riotgames.com/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=10.0
            )
            user_res.raise_for_status()
            puuid = user_res.json().get("sub")

            if not entitlements_token or not puuid:
                raise HTTPException(status_code=400, detail="유저 토큰 또는 PUUID 취득 실패")

            # 6. 상점 데이터 조회
            region_map = {
                "kr": "pd.kr",
                "ap": "pd.ap",
                "na": "pd.na",
                "eu": "pd.eu"
            }
            region_host = region_map.get(data.region.lower(), "pd.kr")

            store_res = await client.get(
                f"https://{region_host}.a.pvp.net/store/v2/storefront/{puuid}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Riot-Entitlements-JWT": entitlements_token,
                    "X-Riot-ClientPlatform": client_platform
                },
                timeout=10.0
            )
            store_res.raise_for_status()
            store_data = store_res.json()

            # 7. 일일상점 & 야시장 스킨 비동기 병렬 파싱
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
