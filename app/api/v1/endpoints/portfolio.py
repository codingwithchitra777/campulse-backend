import logging
from datetime import datetime
from collections import defaultdict
from fastapi import APIRouter, Header, HTTPException, Depends
from app.services.portfolio import PortfolioService
from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.api.deps import get_portfolio_service, get_trade_repo, get_alloc_repo

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/position/{symbol}")
def get_position(
    symbol: str, 
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    portfolio_service = Depends(get_portfolio_service),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    try:
        pos = portfolio_service.position_detail(x_user_id, symbol.upper())
        trades = trade_repo.list_trades(x_user_id, symbol.upper())
        
        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]
        
        # Buys list
        remaining_lots = pos.get("remainingLots", [])
        chart_buys = []
        for lot in remaining_lots:
            chart_buys.append({
                "seq": lot["seq"],
                "qtyOriginal": lot["qtyOriginal"],
                "price": lot["price"],
                "qtyOpen": lot["qtyOpen"]
            })
        
        # Sells list
        allocations = alloc_repo.list_allocations(x_user_id, symbol.upper())
        allocs_by_sell = defaultdict(list)
        buy_seq_map = {b["tradeId"]: b["seq"] for b in buys}
        for a in allocations:
            allocs_by_sell[a["sellTradeId"]].append({
                "buySeq": buy_seq_map.get(a["buyTradeId"], "?"),
                "qty": int(a["qtyAllocated"]),
                "price": int(a["buyPrice"])
            })
            
        chart_sells = []
        for s in sells:
            sell_id = s["tradeId"]
            matched_allocs = allocs_by_sell.get(sell_id, [])
            pnl = sum(int(a["realisedPnl"]) for a in allocations if a["sellTradeId"] == sell_id)
            chart_sells.append({
                "seq": s["seq"],
                "qty": int(s["qty"]),
                "price": int(s["price"]),
                "pnl": pnl,
                "matched": matched_allocs
            })
        chart_sells.sort(key=lambda x: x["seq"], reverse=True)
        
        # Realised P/L
        realised_pnl = sum(int(a["realisedPnl"]) for a in allocations)
        
        # Format datetime orderDate fields for remainingLots
        serialized_lots = []
        for lot in pos.get("remainingLots", []):
            serialized_lot = dict(lot)
            if isinstance(serialized_lot.get("orderDate"), datetime):
                serialized_lot["orderDate"] = serialized_lot["orderDate"].isoformat()
            serialized_lots.append(serialized_lot)
        
        return {
            "ticker": symbol.upper(),
            "totalBoughtQty": pos["totalBoughtQty"],
            "totalSoldQty": pos["totalSoldQty"],
            "remainingQty": pos["remainingQty"],
            "soldPercent": pos["soldPercent"],
            "realisedPnl": realised_pnl,
            "buys": chart_buys,
            "sells": chart_sells,
            "remainingLots": serialized_lots
        }
    except Exception as e:
        logger.error(f"Error in get_position: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/portfolio")
def get_portfolio(
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    portfolio_service = Depends(get_portfolio_service)
):
    try:
        return portfolio_service.portfolio(x_user_id)
    except Exception as e:
        logger.error(f"Error in get_portfolio: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/top-orders")
def get_top_orders(
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    portfolio_service = Depends(get_portfolio_service)
):
    try:
        return portfolio_service.top_profitable_buy_orders(x_user_id)
    except Exception as e:
        logger.error(f"Error in get_top_orders: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/top-tickers")
def get_top_tickers(
    x_user_id: str = Header(default="u001", alias="X-User-Id"),
    portfolio_service = Depends(get_portfolio_service)
):
    try:
        return portfolio_service.top_profitable_tickers(x_user_id)
    except Exception as e:
        logger.error(f"Error in get_top_tickers: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
