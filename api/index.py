from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from curl_cffi import requests
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
    # Chrome 브라우저의 TLS 핑거프린트를 impersonate 옵션으로 복제
    session = requests.Session(impersonate="chrome110")

    headers = {
        'User-Agent': 'RiotClient/63.0.9.4909983.4789131 rso-auth (Windows;10;10.0.19045.1.256.64bit)',
        'Content-Type': 'application/json',
        'Accept': 'application/json, text/plain, */*'
    }

    # 1. OAuth2 쿠키 및 인증 초기화
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
