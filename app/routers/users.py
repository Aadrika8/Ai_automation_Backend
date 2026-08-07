from fastapi import APIRouter, Depends

from app import repositories as repo
from app.models import ManagedUser
from app.security import require_role

router = APIRouter(tags=["users"], dependencies=[Depends(require_role("admin"))])


@router.get("/users", response_model=list[ManagedUser])
async def get_users():
    return await repo.list_users()
