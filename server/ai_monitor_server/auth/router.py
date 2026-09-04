import jwt
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from ..models import User
from .deps import CurrentUser, SessionDep, SettingsDep
from .security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str


class LoginResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    user: UserOut


class RefreshRequest(BaseModel):
    refresh_token: str


class RefreshResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, role=user.role)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: SessionDep, settings: SettingsDep):
    user = (
        await session.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials"
        )
    return LoginResponse(
        access_token=create_access_token(
            user.id, user.role, settings.jwt_secret, settings.access_token_minutes
        ),
        refresh_token=create_refresh_token(
            user.id, settings.jwt_secret, settings.refresh_token_days
        ),
        user=_user_out(user),
    )


@router.post("/refresh", response_model=RefreshResponse)
async def refresh(body: RefreshRequest, session: SessionDep, settings: SettingsDep):
    invalid = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token"
    )
    try:
        payload = decode_token(body.refresh_token, settings.jwt_secret)
    except jwt.PyJWTError:
        raise invalid
    if payload.get("typ") != "refresh":
        raise invalid
    try:
        user_id = int(payload.get("sub", ""))
    except ValueError:
        raise invalid
    user = await session.get(User, user_id)
    if user is None:
        raise invalid
    return RefreshResponse(
        access_token=create_access_token(
            user.id, user.role, settings.jwt_secret, settings.access_token_minutes
        )
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser):
    return _user_out(user)
