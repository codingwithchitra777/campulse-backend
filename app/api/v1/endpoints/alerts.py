import logging
from decimal import Decimal
from fastapi import APIRouter, HTTPException, Depends

from app.schemas.alert import AlertCreate
from app.services.markets import resolve_market_currency
from app.services.alert_service import resolve_chat_id
from app.api.deps import (get_current_user, get_alert_repo, get_user_repo,
                          get_link_repo, get_price_router)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/alerts")
def list_alerts(
    current_user = Depends(get_current_user),
    alert_repo = Depends(get_alert_repo),
    user_repo = Depends(get_user_repo),
    link_repo = Depends(get_link_repo)
):
    """The user's alerts. `deliverable` is false when no linked Telegram exists,
    so the UI can prompt the user to connect one."""
    try:
        deliverable = resolve_chat_id(current_user.user_id, user_repo, link_repo) is not None
        return {"items": alert_repo.list_for_user(current_user.user_id), "deliverable": deliverable}
    except Exception as e:
        logger.error(f"Error in list_alerts: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alerts")
def create_alert(
    req: AlertCreate,
    current_user = Depends(get_current_user),
    alert_repo = Depends(get_alert_repo),
    user_repo = Depends(get_user_repo),
    link_repo = Depends(get_link_repo),
    price_router = Depends(get_price_router)
):
    try:
        symbol = req.symbol.strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="Symbol is required")
        if req.targetPrice <= 0:
            raise HTTPException(status_code=400, detail="Target price must be positive")
        market, currency = resolve_market_currency(req.market, req.currency)

        direction = req.direction
        if direction is None:
            res = price_router.get_latest_price(market, symbol)
            if res.price is not None:
                direction = "above" if req.targetPrice >= Decimal(str(res.price)) else "below"
            else:
                direction = "above"

        created = alert_repo.create(current_user.user_id, market, symbol, currency, req.targetPrice, direction)
        deliverable = resolve_chat_id(current_user.user_id, user_repo, link_repo) is not None
        return {"success": True, "alert": created, "deliverable": deliverable}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in create_alert: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/alerts/{alert_id}")
def delete_alert(
    alert_id: str,
    current_user = Depends(get_current_user),
    alert_repo = Depends(get_alert_repo)
):
    try:
        if not alert_repo.remove(alert_id, current_user.user_id):
            raise HTTPException(status_code=404, detail="Alert not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in delete_alert: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
