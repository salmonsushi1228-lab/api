from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re

# ⚠️ Vercel이 인지할 수 있도록 반드시 최상단(Top-level)에 'app'이라는 이름으로 선언해야 합니다.
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

# 연결 상태 점검용 GET 엔드포인트
@app.get("/api/store")
def check_status():
    return {"status": "ok", "message": "Valorant Store API is running"}

# 라이엇 상점 조회 POST 엔드포인트
@app.post("/api/store")
def get_valorant_store(data: LoginRequest):
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'RiotClient/63.0.9.4909983.4789131 rso-auth (Windows;10;10.0.19045.1.256.64bit)',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*'
    })

    # 1. Auth 토큰 초기화 요청
    auth_body = {
        "client_id": "play-valorant-web-prod",
        "nonce": "1",
        "redirect_uri": "https://playvalorant.com/opt_in",
        "response_type": "token id_token",
        "scope": "account openid"
    }

    try:
        init_res = session.post("https://auth.riotgames.com/api/v1/authorization", json=auth_body)
        init_res.raise_for_status()

        # 2. 자격 증명(ID/PW) 전송
        login_body = {
            "type": "auth",
            "username": data.username,
            "password": data.password,
            "remember": True
        }

        login_res = session.put("https://auth.riotgames.com/api/v1/authorization", json=login_body)
        login_json = login_res.json()

        # 인증 에러 예외 처리
        if login_json.get("type") == "error":
            err = login_json.get("error")
            if err == "auth_failure":
                raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
            elif err == "rate_limited":
                raise HTTPException(status_code=429, detail="요청이 너무 많습니다. 잠시 후 다시 시도해주세요.")
            else:
                raise HTTPException(status_code=400, detail=f"로그인 오류: {err}")

        if login_json.get("type") == "multifactor":
            raise HTTPException(status_code=400, detail="2단계 인증(MFA)이 설정된 계정입니다. 계정 설정에서 2단계 인증을 해제 후 시도하세요.")

        # 3. 토큰 파싱
        if "response" in login_json and "parameters" in login_json["response"]:
            uri = login_json["response"]["parameters"]["uri"]
            access_token = re.search(r'access_token=([^&]+)', uri).group(1)
            id_token = re.search(r'id_token=([^&]+)', uri).group(1)

            # Entitlements Token 발급
            ent_res = session.post(
                "https://entitlements.auth.riotgames.com/api/token/v1",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            entitlements_token = ent_res.json().get("entitlements_token")

            # User ID (PUUID) 추출
            user_res = session.get(
                "https://auth.riotgames.com/userinfo",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            puuid = user_res.json().get("sub")

            # 4. 상점 데이터 요청
            region_host = "pd.kr" if data.region == "kr" else f"pd.{data.region}"
            store_res = session.get(
                f"https://{region_host}.a.pvp.net/store/v2/storefront/{puuid}",
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "X-Riot-Entitlements-JWT": entitlements_token
                }
            )
            store_data = store_res.json()

            # 일일상점 스킨 ID 추출
            daily_item_ids = store_data.get("SkinsPanelLayout", {}).get("SingleItemOffers", [])
            daily_skins = []

            for item_id in daily_item_ids:
                # 라이엇 공식 오픈 API에서 스킨 상세 정보(이름, 이미지) 매핑
                item_info = requests.get(f"https://valorant-api.com/v1/weapons/skinlevel/{item_id}?language=ko-KR").json()
                if item_info.get("status") == 200:
                    data_obj = item_info.get("data", {})
                    daily_skins.append({
                        "name": data_obj.get("displayName"),
                        "icon": data_obj.get("displayIcon")
                    })

            # 야시장 데이터 추출
            night_skins = []
            bonus_store = store_data.get("BonusStore", {}).get("BonusStoreOffers", [])
            for offer in bonus_store:
                offer_id = offer.get("Offer", {}).get("OfferID")
                discount = offer.get("DiscountPercent", 0)
                item_info = requests.get(f"https://valorant-api.com/v1/weapons/skinlevel/{offer_id}?language=ko-KR").json()
                if item_info.get("status") == 200:
                    data_obj = item_info.get("data", {})
                    night_skins.append({
                        "name": data_obj.get("displayName"),
                        "icon": data_obj.get("displayIcon"),
                        "discountPercent": discount
                    })

            return {
                "success": True,
                "daily_store": daily_skins,
                "night_market": night_skins
            }

        raise HTTPException(status_code=400, detail="인증 토큰 추출 실패")

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"통신 에러: {str(e)}")    # 1. OAuth2 쿠키 및 인증 초기화
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
            json=auth_body,
            headers=headers,
            timeout=10
        )
        if init_res.status_code != 200:
            raise HTTPException(status_code=500, detail=f"라이엇 초기화 실패 (코드: {init_res.status_code})")

        # 2. 아이디/비밀번호 인증 전송
        login_body = {
            "type": "auth",
            "username": data.username,
            "password": data.password,
            "remember": "true"
        }

        login_res = session.put(
            "https://auth.riotgames.com/api/v1/authorization",
            json=login_body,
            headers=headers,
            timeout=10
        )
        login_json = login_res.json()

        # 오류 검증
        if login_json.get("type") == "error":
            error_code = login_json.get("error")
            if error_code == "auth_failure":
                raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
            elif error_code == "rate_limited":
                raise HTTPException(status_code=429, detail="시도 횟수가 너무 많습니다. 잠시 후 다시 시도해 주세요.")
            else:
                raise HTTPException(status_code=400, detail=f"로그인 에러: {error_code}")

        if login_json.get("type") == "multifactor":
            raise HTTPException(status_code=400, detail="계정에 2단계 인증(MFA)이 설정되어 있습니다. 2단계 인증을 해제한 후 시도해 주세요.")

        # 3. Access Token 추출
        if "response" in login_json and "parameters" in login_json["response"]:
            uri = login_json["response"]["parameters"]["uri"]
            access_token = re.search(r'access_token=([^&]+)', uri).group(1)
            
            # 여기서 발로란트 Entitlements Token 및 상점 데이터 요청 로직 수행
            return {
                "success": True,
                "message": "인증 성공!",
                "access_token": access_token
            }
        else:
            raise HTTPException(status_code=400, detail="라이엇 토큰을 받아오지 못했습니다.")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"라이엇 서버 통신 에러: {str(e)}")        }
        
        login_res = session.put("https://auth.riotgames.com/api/v1/authorization", json=login_body)
        login_json = login_res.json()

        # 오류 처리
        if login_json.get("type") == "error":
            error_code = login_json.get("error")
            if error_code == "auth_failure":
                raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
            elif error_code == "rate_limited":
                raise HTTPException(status_code=429, detail="시도 횟수가 너무 많습니다. 잠시 후 다시 시도해주세요.")
            else:
                raise HTTPException(status_code=400, detail=f"로그인 오류: {error_code}")

        # 2단계 인증 요구 시
        if login_json.get("type") == "multifactor":
            raise HTTPException(status_code=400, detail="계정에 2단계 인증(MFA)이 설정되어 있어 접속할 수 없습니다. 2단계 인증을 해제 후 시도해 주세요.")

        # 3. 액세스 토큰 추출
        if "response" in login_json and "parameters" in login_json["response"]:
            uri = login_json["response"]["parameters"]["uri"]
            # URI 해석 로직 진행...
            return {"success": True, "message": "로그인 성공!"}
        else:
            raise HTTPException(status_code=400, detail="인증 토큰을 취득하지 못했습니다.")

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"라이엇 인증 서버 통신 에러: {str(e)}")        "nonce": "1",
        "redirect_uri": "https://playvalorant.com/opt_in",
        "response_type": "token id_token",
        "scope": "account openid"
    }
    
    try:
        init_res = session.post("https://auth.riotgames.com/api/v1/authorization", json=auth_body)
        init_res.raise_for_status()

        # 2. 자격 증명(ID/PW) 전송
        login_body = {
            "type": "auth",
            "username": data.username,
            "password": data.password,
            "remember": "true"
        }
        
        login_res = session.put("https://auth.riotgames.com/api/v1/authorization", json=login_body)
        login_json = login_res.json()

        # 오류 처리
        if login_json.get("type") == "error":
            error_code = login_json.get("error")
            if error_code == "auth_failure":
                raise HTTPException(status_code=400, detail="아이디 또는 비밀번호가 올바르지 않습니다.")
            elif error_code == "rate_limited":
                raise HTTPException(status_code=429, detail="시도 횟수가 너무 많습니다. 잠시 후 다시 시도해주세요.")
            else:
                raise HTTPException(status_code=400, detail=f"로그인 오류: {error_code}")

        # 2단계 인증 요구 시
        if login_json.get("type") == "multifactor":
            raise HTTPException(status_code=400, detail="계정에 2단계 인증(MFA)이 설정되어 있어 접속할 수 없습니다. 2단계 인증을 해제 후 시도해 주세요.")

        # 3. 액세스 토큰 추출
        if "response" in login_json and "parameters" in login_json["response"]:
            uri = login_json["response"]["parameters"]["uri"]
            # URI 해석 로직 진행...
            return {"success": True, "message": "로그인 성공!"}
        else:
            raise HTTPException(status_code=400, detail="인증 토큰을 취득하지 못했습니다.")

    except requests.exceptions.RequestException as e:
        raise HTTPException(status_code=500, detail=f"라이엇 인증 서버 통신 에러: {str(e)}")
