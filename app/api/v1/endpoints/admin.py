import logging
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.deps import (get_user_repo, get_trade_repo, get_alloc_repo, get_current_user,
                          require_admin, get_manual_price_repo, get_corp_action_repo, get_exchange_rate_repo)
from app.schemas.admin import (RoleUpdateRequest, AdminStats, ManualPriceRequest,
                               CorporateActionCreate, ExchangeRateCreate)
from app.repositories.price_history import PriceHistoryRepository
from app.services import markets

logger = logging.getLogger(__name__)
router = APIRouter()


def serialize_trade(t):
    from datetime import datetime
    if not t:
        return t
    res = dict(t)
    if isinstance(res.get("orderDate"), datetime):
        res["orderDate"] = res["orderDate"].isoformat()
    return res


@router.get("/admin/users")
def list_users(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin = Depends(require_admin),
    user_repo = Depends(get_user_repo)
):
    try:
        items = user_repo.get_all_users(limit=limit, offset=offset)
        return {"items": items, "total": user_repo.count_users(), "limit": limit, "offset": offset}
    except Exception as e:
        logger.error(f"Error in list_users: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/admin/users/{user_id}/role")
def update_user_role(
    user_id: str,
    role_req: RoleUpdateRequest,
    current_user = Depends(get_current_user),
    _admin = Depends(require_admin),
    user_repo = Depends(get_user_repo)
):
    if user_id == current_user.user_id:
        raise HTTPException(status_code=400, detail="Cannot change your own role")
    try:
        updated = user_repo.update_role(user_id, role_req.role)
        if not updated:
            raise HTTPException(status_code=404, detail="User not found")
        return updated
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in update_user_role: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/trades")
def list_all_trades(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _admin = Depends(require_admin),
    trade_repo = Depends(get_trade_repo)
):
    try:
        trades = trade_repo.list_all_trades(limit=limit, offset=offset)
        return {
            "items": [serialize_trade(t) for t in trades],
            "total": trade_repo.count_trades(),
            "limit": limit,
            "offset": offset
        }
    except Exception as e:
        logger.error(f"Error in list_all_trades: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/manual-prices")
def list_manual_prices(
    _admin = Depends(require_admin),
    manual_repo = Depends(get_manual_price_repo)
):
    try:
        return {"items": manual_repo.list_all()}
    except Exception as e:
        logger.error(f"Error in list_manual_prices: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/admin/manual-price")
def set_manual_price(
    req: ManualPriceRequest,
    current_user = Depends(get_current_user),
    _admin = Depends(require_admin),
    manual_repo = Depends(get_manual_price_repo)
):
    """Set the admin board price for a manually-priced instrument (local gold).
    Also snapshots today's price so the equity chart can value the holding."""
    try:
        market = markets.normalize_market(req.market)
        if market not in markets.MANUAL_MARKETS:
            raise HTTPException(status_code=400, detail=f"{market} is not an admin-priced market")
        if req.price <= 0:
            raise HTTPException(status_code=400, detail="Price must be positive")
        symbol = req.symbol.upper()
        manual_repo.upsert(
            market, symbol, req.price, currency=req.currency.upper(),
            change=req.change, updated_by=current_user.user_id,
            bid_price=req.bidPrice, ask_price=req.askPrice
        )
        # Daily snapshot for the equity series (best-effort).
        try:
            PriceHistoryRepository().upsert_snapshot(
                symbol, datetime.utcnow().date(), req.price, market=market,
                bid_price=req.bidPrice, ask_price=req.askPrice
            )
        except Exception as snap_err:
            logger.warning(f"manual-price snapshot failed for {symbol}: {snap_err}")
        return {"success": True, "market": market, "symbol": symbol,
                "price": req.price, "currency": req.currency.upper()}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in set_manual_price: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/stats", response_model=AdminStats)
def get_admin_stats(
    _admin = Depends(require_admin),
    user_repo = Depends(get_user_repo),
    trade_repo = Depends(get_trade_repo),
    alloc_repo = Depends(get_alloc_repo)
):
    try:
        return AdminStats(
            totalUsers=user_repo.count_users(),
            totalTrades=trade_repo.count_trades(),
            totalRealisedPnl=float(alloc_repo.get_total_realised_pnl())
        )
    except Exception as e:
        logger.error(f"Error in get_admin_stats: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/admin/corporate-actions")
def list_corporate_actions(
    _admin = Depends(require_admin),
    action_repo = Depends(get_corp_action_repo)
):
    try:
        items = action_repo.list_all()
        for a in items:
            a["exDate"] = a["exDate"].isoformat()
            a["createdAt"] = a["createdAt"].isoformat() if a["createdAt"] else None
            a["appliedAt"] = a["appliedAt"].isoformat() if a["appliedAt"] else None
        return {"items": items}
    except Exception as e:
        logger.error(f"Error in list_corporate_actions: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/admin/corporate-actions")
def create_corporate_action(
    req: CorporateActionCreate,
    current_user = Depends(get_current_user),
    _admin = Depends(require_admin),
    action_repo = Depends(get_corp_action_repo)
):
    """Register a bonus/split. The daemon applies it once the ex-date arrives;
    nothing happens to anyone's lots at creation time."""
    try:
        from app.services.corporate_action_service import action_multiplier
        symbol = req.symbol.strip().upper()
        if not symbol:
            raise HTTPException(status_code=400, detail="Symbol is required")
        if req.ratioNew <= 0 or req.ratioHeld <= 0:
            raise HTTPException(status_code=400, detail="Ratios must be positive integers")
        market = markets.normalize_market(req.market)
        probe = {"actionType": req.actionType, "ratioNew": req.ratioNew, "ratioHeld": req.ratioHeld}
        if action_multiplier(probe) <= 1:
            raise HTTPException(status_code=400,
                                detail="Only bonus issues and forward splits (multiplier > 1) are supported")
        created = action_repo.create(market, symbol, req.actionType, req.ratioNew,
                                     req.ratioHeld, req.exDate, req.note, current_user.user_id)
        created["exDate"] = created["exDate"].isoformat()
        created["createdAt"] = created["createdAt"].isoformat() if created["createdAt"] else None
        return {"success": True, "action": created}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in create_corporate_action: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/admin/corporate-actions/{action_id}")
def delete_corporate_action(
    action_id: str,
    _admin = Depends(require_admin),
    action_repo = Depends(get_corp_action_repo)
):
    """Unapplied actions only — an applied one already rewrote lots and must
    remain as the audit record (409)."""
    try:
        existing = action_repo.get(action_id)
        if not existing:
            raise HTTPException(status_code=404, detail="Corporate action not found")
        if existing["appliedAt"] is not None:
            raise HTTPException(status_code=409, detail="Already applied; cannot delete")
        if not action_repo.delete(action_id):
            raise HTTPException(status_code=409, detail="Already applied; cannot delete")
        return {"success": True}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in delete_corporate_action: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/admin/exchange-rates")
def create_exchange_rate(
    req: ExchangeRateCreate,
    current_user = Depends(get_current_user),
    _admin = Depends(require_admin),
    rate_repo = Depends(get_exchange_rate_repo)
):
    try:
        if req.bidRate <= 0 or req.askRate <= 0:
            raise HTTPException(status_code=400, detail="Rates must be positive")
        created = rate_repo.add_rate(
            base_currency=req.baseCurrency,
            target_currency=req.targetCurrency,
            bid_rate=req.bidRate,
            ask_rate=req.askRate,
            effective_date=req.effectiveDate.isoformat(),
            created_by=current_user.user_id
        )
        return {"success": True, "rate": created}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error in create_exchange_rate: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
