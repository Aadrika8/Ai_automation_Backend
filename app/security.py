"""Password hashing, JWT issue/verify, and role-based route guards."""
import time

import bcrypt
import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import get_settings

ROLE_RANK: dict[str, int] = {"manager": 0, "qa": 1, "admin": 2}

_bearer = HTTPBearer(auto_error=False)


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_token(username: str, role: str) -> str:
    settings = get_settings()
    payload = {
        "sub": username,
        "role": role,
        "exp": int(time.time()) + settings.jwt_expires_min * 60,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def get_current_user(
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    if creds is None:
        raise HTTPException(status_code=401, detail="Not authenticated")
    try:
        claims = jwt.decode(creds.credentials, get_settings().jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    if claims.get("role") not in ROLE_RANK:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    # ponytail: claims are trusted for the token lifetime; look the user up in
    # the users collection here if revocation/deactivation ever matters
    return {"username": claims["sub"], "role": claims["role"]}


def require_role(min_role: str):
    def check(user: dict = Depends(get_current_user)) -> dict:
        if ROLE_RANK[user["role"]] < ROLE_RANK[min_role]:
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user

    return check
