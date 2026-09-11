from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import json
import re

app = FastAPI()

# CORS 설정: 프론트엔드와 백엔드 통신 허용
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
    
    # 라이엇 클라이언트 표준 헤더 (UTF-8 인코딩 명시)
    headers = {
        'User-Agent': 'RiotClient/84.0.1.1328.3242 rso-auth (Windows;10;10.0.19045.1.256.64bit)',
        'Content-Type': 'application/json; charset=utf-8',
        'Accept': 'application/json, text/plain, */*',
        'Cache-Control': 'no-cache',
        'Pragma': 'no-cache'
    }
    session.headers.update(headers)

    # 1. Auth 세션 초기화
    auth_body = {
        "client_id": "play-valorant-web-prod",
        "nonce": "1",
        "redirect_uri": "https://playvalorant.com/opt_in",
        "response_type": "token id_token",
        "scope": "account openid"
    }

    try:
        init_res = session.post(
            "https://auth.riotgames.com/api/v1/authorization", 
            data=json.dumps(auth_body, ensure_ascii=False), 
            timeout=10
        )
        init_res.raise_for_status()

        # 2. 로그인 자격 증명 전송 (특수문자 이스케이프 방지를 위해 json.dumps 사용)
        login_body = {
            "type": "auth",
            "username": data.username.strip(),
            "password": data.password,  # 원본 비밀번호 유지
            "remember": True
        }

        login_res = session.put(
            "https://auth.riotgames.com/api/v1/authorization", 
            data=json.dumps(login_body, ensure_ascii=False), 
            timeout=10
        )
        login_res.raise_for_status()
        login_json = login_res.json()

        response_type = login_json.get("type")

        # 오류 응답 세부 분기
        if response_type == "error":
            err = login_json.get("error")
            if err == "auth_failure":
                raise HTTPException(
                    status_code=400, 
                    detail="아이디 또는 비밀번호가 올바르지 않습니다. (Riot ID가 아닌 '로그인용 계정명'인지 확인해 주세요.)"
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
            raise HTTPException(
                status_code=400, 
                detail="인증 URI를 가져오지 못했습니다. 계정 정보를 확인해 주세요."
            )

        access_token_match = re.search(r'access_token=([^&]+)', uri)
        if not access_token_match:
            raise HTTPException(status_code=400, detail="액세스 토큰 파싱에 실패했습니다.")
        access_token = access_token_match.group(1)

        # 4. Entitlements Token 발급
        ent_res = session.post(
            "https://entitlements.auth.riotgames.com/api/token/v1",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        ent_res.raise_for_status()
        entitlements_token = ent_res.json().get("entitlements_token")

        # 5. PUUID 취득
        user_res = session.get(
            "https://auth.riotgames.com/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10
        )
        user_res.raise_for_status()
        puuid = user_res.json().get("sub")

        if not entitlements_token or not puuid:
            raise HTTPException(status_code=400, detail="유저 토큰 또는 PUUID 취득 실패")

        # 6. 상점 데이터 조회
        region_host = "pd.kr" if data.region == "kr" else f"pd.{data.region}"
        store_res = session.get(
            f"https://{region_host}.a.pvp.net/store/v2/storefront/{puuid}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "X-Riot-Entitlements-JWT": entitlements_token
            },
            timeout=10
        )
        store_res.raise_for_status()
        store_data = store_res.json()

        # 7. 일일상점 스킨 파싱
        daily_item_ids = store_data.get("SkinsPanelLayout", {}).get("SingleItemOffers", [])
        daily_skins = []

        for item_id in daily_item_ids:
            try:
                item_info = requests.get(
                    f"https://valorant-api.com/v1/weapons/skinlevel/{item_id}?language=ko-KR", 
                    timeout=5
                ).json()
                if item_info.get("status") == 200:
                    data_obj = item_info.get("data", {})
                    daily_skins.append({
                        "name": data_obj.get("displayName"),
                        "icon": data_obj.get("displayIcon")
                    })
            except Exception:
                continue

        # 8. 야시장 스킨 파싱
        night_skins = []
        bonus_store = store_data.get("BonusStore", {}).get("BonusStoreOffers", [])
        for offer in bonus_store:
            try:
                offer_id = offer.get("Offer", {}).get("OfferID")
                discount = offer.get("DiscountPercent", 0)
                item_info = requests.get(
                    f"https://valorant-api.com/v1/weapons/skinlevel/{offer_id}?language=ko-KR", 
                    timeout=5
                ).json()
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
        raise HTTPException(status_code=500, detail=f"서버 처리 오류: {str(e)}")
