import logging
import requests
from fastapi import APIRouter, HTTPException, Depends
from app.schemas.auth import GoogleAuthRequest, DemoAuthRequest, TelegramAuthRequest, TelegramWebAppAuthRequest
from app.repositories.user import UserRepository
from app.api.deps import get_user_repo, get_link_repo
from app.core.config import settings
from app.core.security import create_access_token, verify_telegram_auth, verify_telegram_webapp_init_data
from app.services.identity import resolve_primary


def _login_response(user_repo, link_repo, raw_user_id: str, name: str,
                    email: str = None, chat_id: int = None) -> dict:
    """Upsert the login identity's own row (capturing name/email/chat_id), then
    resolve it to its canonical account and mint the JWT for that account. A
    linked alias (e.g. a Telegram id linked to a Google account) therefore signs
    into the primary account; unlinked identities sign into themselves."""
    user_repo.upsert_user(user_id=raw_user_id, user_name=name, chat_id=chat_id, email=email)
    primary_id = resolve_primary(raw_user_id, link_repo)
    primary = user_repo.get_user(primary_id) or user_repo.get_user(raw_user_id)
    token = create_access_token(user_id=primary_id, role=primary["role"])
    return {
        "success": True,
        "token": token,
        "userId": primary_id,
        "userName": primary["userName"],
        "email": primary.get("email"),
        "role": primary["role"],
        "marketOverviewEnabled": primary.get("market_overview_enabled", True),
    }


def _telegram_login_response(user_repo, link_repo, tg_id: int, first_name: str, last_name=None) -> dict:
    name = first_name + (f" {last_name}" if last_name else "")
    return _login_response(user_repo, link_repo, str(tg_id), name, chat_id=tg_id)

logger = logging.getLogger(__name__)
router = APIRouter()

@router.post("/auth/google")
def auth_google(payload: GoogleAuthRequest, user_repo = Depends(get_user_repo), link_repo = Depends(get_link_repo)):
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

        # Google is the canonical account; resolve_primary is a no-op unless this
        # Google id was itself linked as an alias into another account.
        return _login_response(user_repo, link_repo, google_id, name, email=email)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in google auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/telegram")
def auth_telegram(payload: TelegramAuthRequest, user_repo = Depends(get_user_repo), link_repo = Depends(get_link_repo)):
    try:
        if not verify_telegram_auth(payload.model_dump(), settings.telegram_token):
            raise HTTPException(status_code=401, detail="Invalid Telegram login data")
        return _telegram_login_response(user_repo, link_repo, payload.id, payload.first_name, payload.last_name)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in telegram auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/auth/telegram-webapp")
def auth_telegram_webapp(payload: TelegramWebAppAuthRequest, user_repo = Depends(get_user_repo), link_repo = Depends(get_link_repo)):
    try:
        user = verify_telegram_webapp_init_data(payload.initData, settings.telegram_token)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid Telegram Mini App data")
        return _telegram_login_response(
            user_repo, link_repo, int(user["id"]), user.get("first_name", "Telegram User"), user.get("last_name")
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
            "role": user["role"],
            "marketOverviewEnabled": user.get("market_overview_enabled", True)
        }
    except Exception as e:
        logger.error(f"Error in demo auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
