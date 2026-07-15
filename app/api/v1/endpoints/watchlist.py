import logging
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query

from app.schemas.watchlist import WatchlistAdd
from app.services.markets import resolve_market_currency
from app.api.deps import get_current_user, get_watchlist_repo, get_price_router

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/watchlist")
def get_watchlist(
    current_user = Depends(get_current_user),
    repo = Depends(get_watchlist_repo),
    price_router = Depends(get_price_router)
):
    """The user's tracked symbols, each with a live quote (routed per market)."""
    try:
        items = repo.list(current_user.user_id)
        for it in items:
            res = price_router.get_latest_price(it["market"], it["symbol"])
            it["price"] = res.price
            it["change"] = res.change
            it["changeDirection"] = res.change_direction
        return {"items": items}
    except Exception as e:
        logger.error(f"Error in get_watchlist: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist")
def add_watchlist(
    req: WatchlistAdd,
    current_user = Depends(get_current_user),
    repo = Depends(get_watchlist_repo)
):
    try:
        symbol = req.symbol.strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="Symbol is required")
        market, currency = resolve_market_currency(req.market, req.currency)
        repo.add(current_user.user_id, market, symbol, currency)
        return {"success": True, "market": market, "symbol": symbol, "currency": currency}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in add_watchlist: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/watchlist/{symbol}")
def remove_watchlist(
    symbol: str,
    market: str = Query(default="CSX"),
    current_user = Depends(get_current_user),
    repo = Depends(get_watchlist_repo)
):
    try:
        removed = repo.remove(current_user.user_id, market, symbol.upper())
        if not removed:
            raise HTTPException(status_code=404, detail="Not on your watchlist")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in remove_watchlist: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
