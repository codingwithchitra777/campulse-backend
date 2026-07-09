import logging
from fastapi import APIRouter, Depends, HTTPException
from app.api.deps import get_user_repo, require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/admin/users")
def list_users(_admin = Depends(require_admin), user_repo = Depends(get_user_repo)):
    try:
        return user_repo.get_all_users()
    except Exception as e:
        logger.error(f"Error in list_users: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
