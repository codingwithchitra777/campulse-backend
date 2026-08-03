import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

from app.schemas.grid import GridPlanBody
from app.api.deps import get_current_user, get_grid_repo

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/grid")
def list_grids(current_user=Depends(get_current_user), repo=Depends(get_grid_repo)):
    """All of the caller's grid plans (most-recently-updated first)."""
    try:
        plans = repo.list_for(current_user.user_id)
        return {"success": True, "plans": plans}
    except Exception as e:
        logger.error(f"Error in list_grids: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/grid")
def upsert_grid(
    body: GridPlanBody,
    current_user=Depends(get_current_user),
    repo=Depends(get_grid_repo),
):
    """Create or update one grid plan (keyed by body.id).

    This only stores the ladder + running fill log. Recording a fill is a
    normal POST /trades/confirm from the client, so there is no execution here.
    A user may keep several grids at once (e.g. one per ticker).
    """
    try:
        plan = body.model_dump()
        # Server-stamp the mutation time; keep the client-sent createdAt.
        plan["updatedAt"] = datetime.now(timezone.utc).isoformat()
        repo.upsert(body.id, current_user.user_id, plan)
        return {"success": True, "plan": plan}
    except Exception as e:
        logger.error(f"Error in upsert_grid: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/grid/{grid_id}")
def delete_grid(
    grid_id: str,
    current_user=Depends(get_current_user),
    repo=Depends(get_grid_repo),
):
    """Discard one of the caller's grid plans (recorded trades are untouched)."""
    try:
        deleted = repo.delete(grid_id, current_user.user_id)
        return {"success": True, "deleted": deleted}
    except Exception as e:
        logger.error(f"Error in delete_grid: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))
