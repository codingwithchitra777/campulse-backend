import logging
import requests
from fastapi import APIRouter, HTTPException, Depends
from app.schemas.auth import GoogleAuthRequest
from app.repositories.user import UserRepository
from app.api.deps import get_user_repo

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
        expected_client_id = "1048965896991-dirq98278c5cj312k2o0kq3f307e2krf.apps.googleusercontent.com"
        if token_data.get("aud") != expected_client_id:
            raise HTTPException(status_code=400, detail="Token audience mismatch")
            
        google_id = token_data.get("sub")
        name = token_data.get("name", "Google User")
        email = token_data.get("email")
        
        if not google_id:
            raise HTTPException(status_code=400, detail="Missing user ID in token")
            
        # Create or update user in our database
        user_repo.upsert_user(user_id=google_id, user_name=name)
        
        return {
            "success": True,
            "userId": google_id,
            "userName": name,
            "email": email
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in google auth: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
