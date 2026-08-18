from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path

from app import repositories as repo
from app.models import ManagedUser, UserCreate
from app.security import hash_password, require_role

router = APIRouter(tags=["users"], dependencies=[Depends(require_role("admin"))])

Username = Annotated[str, Path(min_length=1, max_length=64)]


@router.get("/users", response_model=list[ManagedUser])
async def get_users():
    return await repo.list_users()


@router.post("/users", response_model=ManagedUser, status_code=201)
async def post_user(body: UserCreate):
    username = body.username.strip().lower()
    if await repo.find_user(username) is not None:
        raise HTTPException(status_code=409, detail=f'A user "{username}" already exists')
    doc = {
        "username": username,
        "name": body.name.strip(),
        "role": body.role,
        "passwordHash": hash_password(body.password),
        "lastActive": None,
    }
    await repo.create_user(doc)
    return {k: v for k, v in doc.items() if k != "passwordHash"}


@router.delete("/users/{username}", status_code=204)
async def delete_user(username: Username, user: dict = Depends(require_role("admin"))):
    if username == user["username"]:
        raise HTTPException(status_code=400, detail="You cannot delete your own account")
    if await repo.find_user(username) is None:
        raise HTTPException(status_code=404, detail="User not found")
    await repo.delete_user(username)
