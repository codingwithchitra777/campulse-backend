import logging
import requests
from fastapi import APIRouter, HTTPException, Depends
from app.schemas.auth import GoogleAuthRequest, DemoAuthRequest, TelegramAuthRequest, TelegramWebAppAuthRequest
from app.repositories.user import UserRepository
from app.api.deps import get_user_repo
from app.core.config import settings
from app.core.security import create_access_token, verify_telegram_auth, verify_telegram_webapp_init_data


def _telegram_login_response(user_repo, tg_id: int, first_name: str, last_name = None) -> dict:
    """Shared by the Login Widget and Mini App paths: same user_id convention
    as the Telegram bot (str(telegram user id)), so bot accounts and web
    logins are one account."""
    user_id = str(tg_id)
    name = first_name + (f" {last_name}" if last_name else "")
    user_repo.upsert_user(user_id=user_id, user_name=name, chat_id=tg_id)
    user = user_repo.get_user(user_id)
    token = create_access_token(user_id=user_id, role=user["role"])
    return {
        "success": True,
        "token": token,
        "userId": user_id,
        "userName": name,
        "email": None,
        "role": user["role"]
    }

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/auth/google")
def auth_google(payload: GoogleAuthRequest, user_repo = Depends(get_user_repo)):
    try:
        # Call Google tokeninfo API to verify JWT token
        tokeninfo_url = f"https://oauth2.googleapis.com/tokeninfo?id_token={payload.credential}"
        response = requests.get(tokeninfo_url, timeout=10)
        
        if response.status_code != 200:
            raise HTTPException(status_code=400, detail="Invalid Google token")
            
        token_data = response.json()
        
        # Verify audience matches our Client ID
        expected_client_id = settings.google_client_id
        if token_data.get("aud") != expected_client_id:
            raise HTTPException(status_code=400, detail="Token audience mismatch")
            
        google_id = token_data.get("sub")
        name = token_data.get("name", "Google User")
        email = token_data.get("email")
        
        if not google_id:
            raise HTTPException(status_code=400, detail="Missing user ID in token")
            
        # Create or update user in our database
        user_repo.upsert_user(user_id=google_id, user_name=name)
        user = user_repo.get_user(google_id)
        token = create_access_token(user_id=google_id, role=user["role"])

        return {
            "success": True,
            "token": token,
            "userId": google_id,
            "userName": name,
            "email": email,
            "role": user["role"]
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in google auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/telegram")
def auth_telegram(payload: TelegramAuthRequest, user_repo = Depends(get_user_repo)):
    try:
        if not verify_telegram_auth(payload.model_dump(), settings.telegram_token):
            raise HTTPException(status_code=401, detail="Invalid Telegram login data")
        return _telegram_login_response(user_repo, payload.id, payload.first_name, payload.last_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in telegram auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/telegram-webapp")
def auth_telegram_webapp(payload: TelegramWebAppAuthRequest, user_repo = Depends(get_user_repo)):
    try:
        user = verify_telegram_webapp_init_data(payload.initData, settings.telegram_token)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid Telegram Mini App data")
        return _telegram_login_response(
            user_repo, int(user["id"]), user.get("first_name", "Telegram User"), user.get("last_name")
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in telegram webapp auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/demo")
def auth_demo(payload: DemoAuthRequest, user_repo = Depends(get_user_repo)):
    try:
        user_repo.upsert_user(user_id=payload.userId, user_name=payload.userName)
        user = user_repo.get_user(payload.userId)
        token = create_access_token(user_id=payload.userId, role=user["role"])

        return {
            "success": True,
            "token": token,
            "userId": payload.userId,
            "userName": payload.userName,
            "email": None,
            "role": user["role"]
        }
    except Exception as e:
        logger.error(f"Error in demo auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
