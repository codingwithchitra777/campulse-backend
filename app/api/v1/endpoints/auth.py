import logging
import requests
from fastapi import APIRouter, HTTPException, Depends
from app.schemas.auth import GoogleAuthRequest, DemoAuthRequest
from app.repositories.user import UserRepository
from app.api.deps import get_user_repo
from app.core.config import settings
from app.core.security import create_access_token

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
