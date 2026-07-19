import logging
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from app.api.deps import get_current_user, get_user_repo

logger = logging.getLogger(__name__)
router = APIRouter()

class MarketOverviewSettings(BaseModel):
    enabled: bool

@router.patch("/settings/market-overview")
def update_market_overview_settings(
    payload: MarketOverviewSettings,
    current_user: dict = Depends(get_current_user),
    user_repo = Depends(get_user_repo)
):
    try:
        user_repo.update_market_overview(current_user["user_id"], payload.enabled)
        return {"success": True, "marketOverviewEnabled": payload.enabled}
    except Exception as e:
        logger.error(f"Error updating settings: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update settings")
