import logging
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import get_current_user, get_portfolio_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/lots/open")
def open_lots(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    valuationMode: str = Query(default="BID"),
    current_user=Depends(get_current_user),
    portfolio_service=Depends(get_portfolio_service),
):
    """Open buy lots ranked by current unrealised P/L (paginated, per-currency)."""
    try:
        return portfolio_service.profitable_open_lots(
            current_user.user_id, limit=limit, offset=offset, valuation_mode=valuationMode
        )
    except Exception as e:
        logger.error(f"Error in open_lots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/lots/closed")
def closed_lots(
    limit: int = Query(default=10, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    current_user=Depends(get_current_user),
    portfolio_service=Depends(get_portfolio_service),
):
    """Closed buy lots ranked by realised P/L (paginated)."""
    try:
        return portfolio_service.profitable_closed_lots(
            current_user.user_id, limit=limit, offset=offset
        )
    except Exception as e:
        logger.error(f"Error in closed_lots: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
