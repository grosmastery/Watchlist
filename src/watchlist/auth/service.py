from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.auth.models import User, UserSession
from watchlist.auth.schemas import LoginForm, RegisterForm
from watchlist.auth.security import (
    burn_password_check,
    generate_token,
    hash_password,
    hash_token,
    password_needs_rehash,
    verify_password,
)


class RegistrationError(Exception):
    def __init__(self, errors: dict[str, str]) -> None:
        super().__init__(errors)
        self.errors = errors


async def find_user_by_login(session: AsyncSession, login: str) -> User | None:
    if "@" in login:
        condition = User.email == login.strip().lower()
    else:
        condition = func.lower(User.username) == login.strip().lower()
    return await session.scalar(select(User).where(condition))


async def register_user(session: AsyncSession, form: RegisterForm) -> User:
    errors: dict[str, str] = {}
    if await session.scalar(select(User.id).where(User.email == form.email)) is not None:
        errors["email"] = "Ця пошта вже зареєстрована"
    username_taken = await session.scalar(
        select(User.id).where(func.lower(User.username) == form.username.lower())
    )
    if username_taken is not None:
        errors["username"] = "Це ім'я вже зайняте"
    if errors:
        raise RegistrationError(errors)

    user = User(
        email=form.email,
        username=form.username,
        password_hash=await hash_password(form.password),
    )
    session.add(user)
    try:
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        raise RegistrationError({"__all__": "Пошта або ім'я вже зайняті"}) from None
    return user


async def authenticate(session: AsyncSession, form: LoginForm) -> User | None:
    user = await find_user_by_login(session, form.login)
    if user is None:
        await burn_password_check(form.password)
        return None
    if not await verify_password(user.password_hash, form.password):
        return None
    if password_needs_rehash(user.password_hash):
        user.password_hash = await hash_password(form.password)
    return user


async def create_session(session: AsyncSession, user: User, ttl: timedelta) -> str:
    token = generate_token()
    session.add(
        UserSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=datetime.now(UTC) + ttl,
        )
    )
    await session.flush()
    return token


async def get_user_by_session_token(session: AsyncSession, token: str) -> User | None:
    statement = (
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(UserSession.token_hash == hash_token(token))
        .where(UserSession.expires_at > func.now())
    )
    return await session.scalar(statement)


async def delete_session(session: AsyncSession, token: str) -> None:
    await session.execute(delete(UserSession).where(UserSession.token_hash == hash_token(token)))
