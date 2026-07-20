import os
import logging
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import FileResponse, StreamingResponse

from app.services.pricing import PricingService
from app.services.finnhub_provider import FinnhubProvider
from app.repositories.trade import TradeRepository
from app.repositories.allocation import AllocationRepository
from app.services.portfolio import PortfolioService
from app.services.redis_service import RedisService
from app.utils.chart_renderer import ChartRenderer
from app.api.deps import get_pricing_service, get_trade_repo, get_alloc_repo, get_portfolio_service, get_price_router, get_exchange_rate_repo

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get("/market/sparklines")
def get_sparklines(tickers: str = Query(..., description="Comma-separated list of tickers")):
    """Get 60-day historical sparklines from Redis for a list of tickers."""
    try:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        redis_service = RedisService()
        return redis_service.get_sparklines_batch(ticker_list)
    except Exception as e:
        logger.error(f"Error in get_sparklines: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/prices")
def get_prices(pricing_service = Depends(get_pricing_service)):
    try:
        return pricing_service.get_all_prices()
    except Exception as e:
        logger.error(f"Error in get_prices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/market/search")
def search_symbols(q: str = Query(min_length=1)):
    """Symbol lookup for international (US) equities via Finnhub, so the record
    form can validate/pick a symbol. Empty list when no API key is configured."""
    try:
        return {"results": FinnhubProvider().search_symbols(q)}
    except Exception as e:
        logger.error(f"Error in search_symbols: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/market/news/{symbol}")
def get_symbol_news(symbol: str, days: int = Query(default=7, ge=1, le=30)):
    """Recent company news for a US symbol via Finnhub. Empty for CSX/gold (no feed)."""
    try:
        return {"symbol": symbol.upper(), "news": FinnhubProvider().get_company_news(symbol, days)}
    except Exception as e:
        logger.error(f"Error in get_symbol_news: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/market/quote/{symbol}")
def get_market_quote(symbol: str, market: str = Query(default="US"), price_router = Depends(get_price_router)):
    """Market-aware live quote (routes CSX->CSX feed, US->Finnhub)."""
    try:
        res = price_router.get_latest_price(market, symbol.upper())
        if res.price is None:
            raise HTTPException(status_code=404, detail=f"Price not found for {symbol}")
        return {
            "ticker": res.ticker,
            "market": market,
            "price": res.price,
            "change": res.change,
            "changeDirection": res.change_direction,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_market_quote: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/price/{symbol}")
def get_price(symbol: str, pricing_service = Depends(get_pricing_service)):
    try:
        res = pricing_service.get_latest_price(symbol.upper())
        if res.price is None:
            raise HTTPException(status_code=404, detail=f"Price not found for {symbol}")
        return {
            "ticker": res.ticker,
            "price": res.price,
            "change": res.change,
            "changeDirection": res.change_direction
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in get_price: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/chart/{symbol}")
def get_chart(
    symbol: str, 
    userId: str = Query(default="u001"),
    portfolio_service = Depends(get_portfolio_service),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    try:
        # Get position details and raw trades
        pos = portfolio_service.position_detail(userId, symbol.upper())
        trades = trade_repo.list_trades(userId, symbol.upper())
        
        buys = [t for t in trades if t["side"] == "BUY"]
        sells = [t for t in trades if t["side"] == "SELL"]
        
        # Build buys list
        remaining_lots = pos.get("remainingLots", [])
        chart_buys = []
        for lot in remaining_lots:
            chart_buys.append({
                "seq": lot["seq"],
                "qty": lot["qtyOriginal"],
                "price": lot["price"],
                "remaining": lot["qtyOpen"]
            })
        
        # Build sells list
        allocations = alloc_repo.list_allocations(userId, symbol.upper())
        allocs_by_sell = defaultdict(list)
        buy_seq_map = {b["tradeId"]: b["seq"] for b in buys}
        for a in allocations:
            allocs_by_sell[a["sellTradeId"]].append({
                "buySeq": buy_seq_map.get(a["buyTradeId"], "?"),
                "qty": int(a["qtyAllocated"]),
                "price": float(a["buyPrice"])
            })

        chart_sells = []
        for s in sells:
            sell_id = s["tradeId"]
            matched_allocs = allocs_by_sell.get(sell_id, [])
            pnl = sum(float(a["realisedPnl"]) for a in allocations if a["sellTradeId"] == sell_id)
            chart_sells.append({
                "seq": s["seq"],
                "qty": int(s["qty"]),
                "price": float(s["price"]),
                "pnl": pnl,
                "matched": matched_allocs
            })

        chart_sells.sort(key=lambda x: x["seq"], reverse=True)

        # Summary dict
        total_bought = sum(int(b["qty"]) for b in buys)
        total_sold = sum(int(s["qty"]) for s in sells)
        remaining = total_bought - total_sold
        realised_pnl = sum(float(a["realisedPnl"]) for a in allocations)
        
        summary = {
            "totalBought": total_bought,
            "totalSold": total_sold,
            "remaining": remaining,
            "realisedPnl": realised_pnl
        }
        
        renderer = ChartRenderer()
        img_buffer = renderer.stock_detail_card(symbol.upper(), chart_buys, chart_sells, [], summary)
        
        img_buffer.seek(0)
        return StreamingResponse(img_buffer, media_type="image/png")
        
    except Exception as e:
        logger.error(f"Error in get_chart: {e}", exc_info=True)
        chart_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "demo_stock_chart.png"))
        if os.path.exists(chart_path):
            return FileResponse(chart_path, media_type="image/png")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/market/exchange-rates/latest")
def get_latest_exchange_rate(
    baseCurrency: str = Query("USD"),
    targetCurrency: str = Query("KHR"),
    rate_repo = Depends(get_exchange_rate_repo)
):
    try:
        rate = rate_repo.get_latest_rate(baseCurrency, targetCurrency)
        return {"rate": rate}
    except Exception as e:
        logger.error(f"Error in get_latest_exchange_rate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/market/exchange-rates/history")
def get_exchange_rate_history(
    baseCurrency: str = Query("USD"),
    targetCurrency: str = Query("KHR"),
    limit: int = Query(default=100, ge=1, le=1000),
    rate_repo = Depends(get_exchange_rate_repo)
):
    try:
        rates = rate_repo.get_history(baseCurrency, targetCurrency, limit)
        return {"items": rates}
    except Exception as e:
        logger.error(f"Error in get_exchange_rate_history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/market/price-history/{symbol}")
def get_price_history(
    symbol: str,
    days: int = Query(default=30, ge=1, le=365)
):
    try:
        from app.repositories.price_history import PriceHistoryRepository
        hist = PriceHistoryRepository().get_history_days(days)
        # Filter for just this symbol
        symbol = symbol.upper()
        symbol_hist = [h for h in hist if h["ticker"] == symbol]
        return {"items": symbol_hist}
    except Exception as e:
        logger.error(f"Error in get_price_history: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
