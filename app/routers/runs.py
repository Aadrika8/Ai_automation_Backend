from fastapi import APIRouter, Depends

from app import repositories as repo
from app.models import RunSummary
from app.security import require_role

router = APIRouter(tags=["runs"], dependencies=[Depends(require_role("qa"))])


@router.get("/runs", response_model=list[RunSummary])
async def get_runs():
    return await repo.list_runs()
