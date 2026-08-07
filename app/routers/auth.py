from fastapi import APIRouter, HTTPException

from app import repositories as repo
from app.models import LoginRequest, LoginResponse
from app.security import create_token, verify_password

router = APIRouter(tags=["auth"])


@router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest):
    user = await repo.find_user(body.username.strip())
    if (
        user is None
        or not user.get("passwordHash")  # display-only demo users cannot log in
        or not verify_password(body.password, user["passwordHash"])
    ):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {
        "token": create_token(user["username"], user["role"]),
        "user": {"username": user["username"], "name": user["name"], "role": user["role"]},
    }
