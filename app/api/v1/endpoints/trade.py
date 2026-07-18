import logging
from decimal import Decimal
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, HTTPException, Depends, Query
from app.schemas.trade import TradeCreate, TradeUpdate, JournalUpdate
from app.services.best_profit_matcher import BestProfitMatcherService
from app.services.trade_service import record_trade
from app.services.markets import quantize_money
from app.api.deps import get_trade_repo, get_alloc_repo, get_portfolio_service, get_current_user

logger = logging.getLogger(__name__)
router = APIRouter()

def serialize_trade(t):
    if not t:
        return t
    res = dict(t)
    if isinstance(res.get("orderDate"), datetime):
        res["orderDate"] = res["orderDate"].isoformat()
    return res

def serialize_allocation(a):
    if not a:
        return a
    res = dict(a)
    if isinstance(res.get("createdAt"), datetime):
        res["createdAt"] = res["createdAt"].isoformat()
    return res

@router.get("/trades")
def get_trades(
    current_user = Depends(get_current_user),
    ticker: Optional[str] = None,
    market: Optional[str] = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    trade_repo = Depends(get_trade_repo)
):
    try:
        ticker_filter = ticker.upper() if ticker else None
        trades = trade_repo.list_trades(
            current_user.user_id, ticker_filter, limit=limit, offset=offset, market=market
        )
        return {
            "items": [serialize_trade(t) for t in trades],
            "total": trade_repo.count_trades(current_user.user_id, ticker_filter, market=market),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Error in get_trades: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/trades")
def add_trade(
    trade_req: TradeCreate,
    current_user = Depends(get_current_user),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    try:
        result = record_trade(
            trade_repo, alloc_repo, current_user.user_id,
            trade_req.ticker, trade_req.side, trade_req.price, trade_req.qty,
            commission=trade_req.commission, order_date=trade_req.orderDate,
            market=trade_req.market, currency=trade_req.currency,
        )
        return {
            "success": True,
            "trade": serialize_trade(result["trade"]),
            "allocations": [serialize_allocation(a) for a in result["allocations"]],
            "realisedPnl": result["realisedPnl"],
            "warning": result["warning"]
        }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in add_trade: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/trades/{trade_id}")
def update_trade(
    trade_id: str,
    trade_req: TradeUpdate,
    current_user = Depends(get_current_user),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    try:
        trade = trade_repo.get_trade(trade_id)
        if not trade or trade["userId"] != current_user.user_id:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade["side"] != "BUY":
            raise HTTPException(status_code=409, detail="Only BUY trades can be edited; delete and re-enter SELL trades instead")
        if alloc_repo.has_allocations(trade_id):
            raise HTTPException(status_code=409, detail="Cannot edit a trade that has already been sold from")

        ticker = trade_req.ticker.upper()
        price = trade_req.price
        qty = trade_req.qty
        if price <= 0 or qty <= 0:
            raise HTTPException(status_code=400, detail="Price and Quantity must be positive")
        commission = (trade_req.commission if trade_req.commission is not None
                      else quantize_money(price * qty * Decimal("0.0047"), trade.get("currency", "KHR")))

        # Omitted orderDate keeps the trade's current date (static SQL in the repo).
        order_date = trade_req.orderDate or trade["orderDate"]
        if order_date.date() > datetime.utcnow().date():
            raise HTTPException(status_code=400, detail="Order date cannot be in the future")

        updated = trade_repo.update_trade(trade_id, current_user.user_id, ticker, price, qty, commission, order_date)
        if not updated:
            raise HTTPException(status_code=404, detail="Trade not found")
        return serialize_trade(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_trade: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.patch("/trades/{trade_id}/journal")
def update_trade_journal(
    trade_id: str,
    req: JournalUpdate,
    current_user = Depends(get_current_user),
    trade_repo = Depends(get_trade_repo)
):
    """Set a journal note/tags on any of the user's trades (metadata — allowed
    on SELL and matched trades, unlike price/qty edits)."""
    try:
        trade = trade_repo.get_trade(trade_id)
        if not trade or trade["userId"] != current_user.user_id:
            raise HTTPException(status_code=404, detail="Trade not found")
        note = (req.note or "").strip() or None
        tags = (req.tags or "").strip() or None
        updated = trade_repo.update_journal(trade_id, current_user.user_id, note, tags)
        return serialize_trade(updated)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_trade_journal: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/trades/{trade_id}")
def delete_trade(
    trade_id: str,
    current_user = Depends(get_current_user),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    try:
        trade = trade_repo.get_trade(trade_id)
        if not trade or trade["userId"] != current_user.user_id:
            raise HTTPException(status_code=404, detail="Trade not found")
        if trade["side"] == "BUY" and alloc_repo.has_allocations(trade_id):
            raise HTTPException(status_code=409, detail="Cannot delete a trade that has already been sold from")

        deleted = trade_repo.delete_trade(trade_id, current_user.user_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Trade not found")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in delete_trade: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/trades/init")
def init_trade(
    trade_req: TradeCreate,
    current_user = Depends(get_current_user),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo),
    portfolio_service = Depends(get_portfolio_service)
):
    try:
        x_user_id = current_user.user_id
        ticker = trade_req.ticker.upper()
        side = trade_req.side.upper()
        price = trade_req.price
        qty = trade_req.qty

        if side not in ["BUY", "SELL"]:
            raise HTTPException(status_code=400, detail="Side must be BUY or SELL")
        if price <= 0 or qty <= 0:
            raise HTTPException(status_code=400, detail="Price and Quantity must be positive")

        # Get existing position details
        pos = portfolio_service.position_detail(x_user_id, ticker, market=trade_req.market)
        existing_qty = pos["remainingQty"]

        if side == "BUY":
            return {
                "success": True,
                "valid": True,
                "validationError": None,
                "simulatedPnl": 0,
                "isLoss": False,
                "simulatedLossAmount": 0,
                "existingQty": existing_qty
            }

        # SELL side validation & simulation
        matcher = BestProfitMatcherService(trade_repo, alloc_repo)
        sim_res = matcher.simulate_sell(x_user_id, ticker, price, qty, commission=trade_req.commission, market=trade_req.market)

        return {
            "success": True,
            "valid": sim_res["valid"],
            "validationError": sim_res["validationError"],
            "simulatedPnl": sim_res.get("simulatedPnl", 0),
            "isLoss": sim_res.get("isLoss", False),
            "simulatedLossAmount": sim_res.get("simulatedLossAmount", 0),
            "existingQty": existing_qty
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in init_trade: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/trades/confirm")
def confirm_trade(
    trade_req: TradeCreate,
    current_user = Depends(get_current_user),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    return add_trade(trade_req, current_user, trade_repo, alloc_repo)

