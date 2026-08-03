import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.grid import GridPlanBody
from app.api.deps import get_current_user, get_grid_repo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/grid")
def get_grid(current_user=Depends(get_current_user), repo=Depends(get_grid_repo)):
    """The caller's active grid plan (or null if none registered)."""
    try:
        plan = repo.get(current_user.user_id)
        return {"success": True, "plan": plan}
    except Exception as e:
        logger.error(f"Error in get_grid: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/grid")
def upsert_grid(
    body: GridPlanBody,
    current_user=Depends(get_current_user),
    repo=Depends(get_grid_repo),
):
    """Register / update the caller's active grid plan.

    This only stores the ladder + running fill log. Recording a fill is a
    normal POST /trades/confirm from the client, so there is no execution here.
    """
    try:
        plan = body.model_dump()
        # Server-stamp the mutation time; keep the client-sent createdAt.
        plan["updatedAt"] = datetime.now(timezone.utc).isoformat()
        repo.upsert(current_user.user_id, plan)
        return {"success": True, "plan": plan}
    except Exception as e:
        logger.error(f"Error in upsert_grid: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/grid")
def delete_grid(current_user=Depends(get_current_user), repo=Depends(get_grid_repo)):
    """Discard the caller's active grid plan (recorded trades are untouched)."""
    try:
        repo.delete(current_user.user_id)
        return {"success": True}
    except Exception as e:
        logger.error(f"Error in delete_grid: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
