from fastapi import APIRouter, Depends, HTTPException

from app import repositories as repo
from app.models import TestDetail
from app.security import require_role

router = APIRouter(tags=["tests"], dependencies=[Depends(require_role("qa"))])


@router.get("/tests/{test_id}", response_model=TestDetail)
async def get_test(test_id: str):
    detail = await repo.get_test(test_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="Test case not found")
    return detail
