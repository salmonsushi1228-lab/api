from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import requests
import re

app = FastAPI()

# CORS 설정 (패드/브라우저에서 자유롭게 호출 가능)
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

USER_AGENT = "RiotClient/43.0.1.4195386.4190634 rso-auth (Windows;10;10.0.19042.1.256.64bit)"

@app.post("/api/store")
def get_valorant_store(data: LoginRequest):
    session = requests.Session()
    session.headers.update({
        "User-Agent": USER_AGENT,
        "Content-Type": "application/json"
    })

    try:
        # Step 1: Auth 세션 및 쿠키 생성
        auth_payload = {
            "client_id": "play-valorant-web-prod",
            "nonce": "1",
            "redirect_uri": "https://playvalorant.com/opt_in",
            "response_type": "token id_token"
        }
        session.post("https://auth.riotgames.com/api/v1/authorization", json=auth_payload, timeout=10)

        # Step 2: ID/PW 로그인
        login_payload = {
            "type": "auth",
            "username": data.username,
            "password": data.password
        }
        login_res = session.put("https://auth.riotgames.com/api/v1/authorization", json=login_payload, timeout=10).json()

        if "error" in login_res:
            raise HTTPException(status_code=401, detail="로그인 실패: 아이디 또는 비밀번호를 확인하세요.")

        # Access Token 추출
        pattern = re.compile(r'access_token=([^&]+)')
        uri = login_res.get("response", {}).get("parameters", {}).get("uri", "")
        match = pattern.search(uri)
        if not match:
            raise HTTPException(status_code=401, detail="2단계 인증이 설정되어 있거나 인증 토큰을 추출할 수 없습니다.")
        
        access_token = match.group(1)

        # Step 3: Entitlements Token 발급
        ent_headers = {"Authorization": f"Bearer {access_token}"}
        ent_res = session.post("https://entitlements.auth.riotgames.com/api/token/v1", headers=ent_headers, json={}, timeout=10).json()
        entitlements_token = ent_res.get("entitlements_token")

        # Step 4: PUUID (사용자 고유 ID) 조회
        user_info = session.get("https://auth.riotgames.com/userinfo", headers=ent_headers, timeout=10).json()
        puuid = user_info.get("sub")

        # Step 5: 실제 상점 데이터(일일상점 + 야시장) 조회
        store_headers = {
            "Authorization": f"Bearer {access_token}",
            "X-Riot-Entitlements-JWT": entitlements_token
        }
        store_url = f"https://pd.{data.region}.a.pvp.net/store/v2/storefront/{puuid}"
        store_data = session.get(store_url, headers=store_headers, timeout=10).json()

        # 스킨 정보 한글 매핑을 위해 Valorant-API 조회
        skin_db_res = requests.get("https://valorant-api.com/v1/weapons/skinlevels?language=ko-KR", timeout=10).json()
        skin_db = {item["uuid"].lower(): {"name": item["displayName"], "icon": item["displayIcon"]} for item in skin_db_res.get("data", [])}

        # 일일상점 스킨 ID -> 이름/이미지 변환
        daily_offers = store_data.get("SkinsPanelLayout", {}).get("SingleItemOffers", [])
        daily_skins = [skin_db.get(offer_id.lower(), {"name": "알 수 없는 스킨", "icon": ""}) for offer_id in daily_offers]

        # 야시장 데이터 변환 (있을 경우만)
        night_market_skins = []
        bonus_store = store_data.get("BonusStore", {})
        if bonus_store and "BonusStoreOffers" in bonus_store:
            for offer in bonus_store["BonusStoreOffers"]:
                skin_id = offer.get("Offer", {}).get("OfferID", "")
                skin_info = skin_db.get(skin_id.lower(), {"name": "알 수 없는 스킨", "icon": ""})
                night_market_skins.append({
                    "name": skin_info["name"],
                    "icon": skin_info["icon"],
                    "discountPercent": offer.get("DiscountPercent", 0)
                })

        return {
            "success": True,
            "puuid": puuid,
            "daily_store": daily_skins,
            "night_market": night_market_skins
        }

    except requests.RequestException:
        raise HTTPException(status_code=500, detail="라이엇 서버와 통신 중 에러가 발생했습니다.")
