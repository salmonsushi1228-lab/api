from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re

app = FastAPI()

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

@app.get("/api/store")
def check_status():
    return {"status": "ok", "message": "Valorant Store API is running"}

@app.post("/api/store")
def get_valorant_store(data: LoginRequest):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'RiotClient/63.0.9.4909983.4789131 rso-auth (Windows;10;10.0.19045.1.256.64bit)',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*'
    })

    auth_body = {
        "client_id": "play-valorant-web-prod",
        "nonce": "1",
        "redirect_uri": "https://playvalorant.com/opt_in",
        "response_type": "token id_token",
        "scope": "account openid"
    }

    try:
        # 1. 초기 인증 세션 쿠키 생성
        init_res = session.post("https://auth.riotgames.com/api/v1/authorization", json=auth_body, timeout=10)
        init_res.raise_for_status()

        # 2. 아이디/비밀번호 인증 요청
        login_body = {
            "type": "auth",
            "username": data.username,
            "password": data.password,
            "remember": True
        }

        login_res = session.put("https://auth.riotgames.com/api/v1/authorization", json=login_body, timeout=10)
        login_json = login_res.json()

        # 인증 에러 응답 처리
        if login_json.get("type") == "error":
            err = login_json.get("error")
            if err == "auth_failure":
                raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
            elif err == "rate_limited":
                raise HTTPException(status_code=429, detail="시도 횟수가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
            else:
                raise HTTPException(status_code=400, detail=f"로그인 오류: {err}")

        if login_json.get("type") == "multifactor":
            raise HTTPException(status_code=400, detail="2단계 인증(MFA)이 설정되어 있어 접속할 수 없습니다. 2단계 인증을 해제 후 시도해 주세요.")

        # 3. 토큰 추출 및 예외 방지
        response_data = login_json.get("response", {})
        parameters = response_data.get("parameters", {})
        uri = parameters.get("uri", "")

        if not uri:
            raise HTTPException(status_code=400, detail="인증 URI를 가져오지 못했습니다. 계정 정보를 확인해 주세요.")

        access_token_match = re.search(r'access_token=([^&]+)', uri)
        if not access_token_match:
            raise HTTPException(status_code=400, detail="액세스 토큰 파싱 실패")
        access_token = access_token_match.group(1)

        # 4. Entitlements Token 발급
        ent_res = session.post(
            "https://entitlements.auth.riotgames.com/api/token/v1",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        entitlements_token = ent_res.json().get("entitlements_token")

        # 5. PUUID 취득
        user_res = session.get(
            "https://auth.riotgames.com/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        puuid = user_res.json().get("sub")

        if not entitlements_token or not puuid:
            raise HTTPException(status_code=400, detail="사용자 정보 또는 토큰 발급에 실패했습니다.")

        # 6. 상점 정보 수집
        region_host = "pd.kr" if data.region == "kr" else f"pd.{data.region}"
        store_res = session.get(
            f"https://{region_host}.a.pvp.net/store/v2/storefront/{puuid}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Riot-Entitlements-JWT": entitlements_token
            },
            timeout=10
        )
        store_data = store_res.json()

        # 일일상점 파싱
        daily_item_ids = store_data.get("SkinsPanelLayout", {}).get("SingleItemOffers", [])
        daily_skins = []

        for item_id in daily_item_ids:
            try:
                item_info = requests.get(f"https://valorant-api.com/v1/weapons/skinlevel/{item_id}?language=ko-KR", timeout=5).json()
                if item_info.get("status") == 200:
                    data_obj = item_info.get("data", {})
                    daily_skins.append({
                        "name": data_obj.get("displayName"),
                        "icon": data_obj.get("displayIcon")
                    })
            except Exception:
                continue

        # 야시장 파싱
        night_skins = []
        bonus_store = store_data.get("BonusStore", {}).get("BonusStoreOffers", [])
        for offer in bonus_store:
            try:
                offer_id = offer.get("Offer", {}).get("OfferID")
                discount = offer.get("DiscountPercent", 0)
                item_info = requests.get(f"https://valorant-api.com/v1/weapons/skinlevel/{offer_id}?language=ko-KR", timeout=5).json()
                if item_info.get("status") == 200:
                    data_obj = item_info.get("data", {})
                    night_skins.append({
                        "name": data_obj.get("displayName"),
                        "icon": data_obj.get("displayIcon"),
                        "discountPercent": discount
                    })
            except Exception:
                continue

        return {
            "success": True,
            "daily_store": daily_skins,
            "night_market": night_skins
        }

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"서버 내부 오류: {str(e)}")
