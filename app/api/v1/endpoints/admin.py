import logging
from fastapi import APIRouter, Depends, HTTPException, Query
from app.api.deps import get_user_repo, get_trade_repo, get_alloc_repo, get_current_user, require_admin
from app.schemas.admin import RoleUpdateRequest, AdminStats

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
