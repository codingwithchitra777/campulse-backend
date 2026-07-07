import uuid
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Header, HTTPException, Depends
from app.schemas.trade import TradeCreate
from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.services.lifo_matcher import LifoMatcherService
from app.api.deps import get_trade_repo, get_alloc_repo, get_portfolio_service

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
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    ticker: Optional[str] = None,
    trade_repo = Depends(get_trade_repo)
):
    try:
        trades = trade_repo.list_trades(x_user_id, ticker.upper() if ticker else None)
        return [serialize_trade(t) for t in trades]
    except Exception as e:
        logger.error(f"Error in get_trades: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/trades")
def add_trade(
    trade_req: TradeCreate,
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    try:
        ticker = trade_req.ticker.upper()
        side = trade_req.side.upper()
        price = trade_req.price
        qty = trade_req.qty

        if side not in ["BUY", "SELL"]:
            raise HTTPException(status_code=400, detail="Side must be BUY or SELL")
        if price <= 0 or qty <= 0:
            raise HTTPException(status_code=400, detail="Price and Quantity must be positive")

        commission = int(price * qty * 0.0047)
        seq = len(trade_repo.list_trades(x_user_id)) + 1

        trade = {
            "tradeId": str(uuid.uuid4()),
            "userId": x_user_id,
            "seq": seq,
            "ticker": ticker,
            "side": side,
            "price": price,
            "qty": qty,
            "commission": commission,
            "orderDate": datetime.utcnow()
        }
        trade_repo.add_trade(trade)

        allocations = []
        realised_pnl = 0
        warning = None

        if side == "SELL":
            lifo_service = LifoMatcherService(trade_repo, alloc_repo)
            try:
                allocs = lifo_service.match_sell_lifo(trade)
                allocations = [serialize_allocation(a) for a in allocs]
                realised_pnl = sum(int(a.get("realisedPnl", 0)) for a in allocs)
            except Exception as e:
                logger.warning(f"LIFO Matching warning: {e}")
                warning = f"LIFO Match failed: {e}"

        return {
            "success": True,
            "trade": serialize_trade(trade),
            "allocations": allocations,
            "realisedPnl": realised_pnl,
            "warning": warning
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in add_trade: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/trades/init")
def init_trade(
    trade_req: TradeCreate,
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo),
    portfolio_service = Depends(get_portfolio_service)
):
    try:
        ticker = trade_req.ticker.upper()
        side = trade_req.side.upper()
        price = trade_req.price
        qty = trade_req.qty

        if side not in ["BUY", "SELL"]:
            raise HTTPException(status_code=400, detail="Side must be BUY or SELL")
        if price <= 0 or qty <= 0:
            raise HTTPException(status_code=400, detail="Price and Quantity must be positive")

        # Get existing position details
        pos = portfolio_service.position_detail(x_user_id, ticker)
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
        lifo_service = LifoMatcherService(trade_repo, alloc_repo)
        sim_res = lifo_service.simulate_sell_lifo(x_user_id, ticker, price, qty)

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
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    return add_trade(trade_req, x_user_id, trade_repo, alloc_repo)

