from datetime import timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from watchlist.auth.schemas import LoginForm, RegisterForm, form_errors
from watchlist.auth.service import (
    RegistrationError,
    authenticate,
    create_session,
    delete_session,
    get_user_by_session_token,
    register_user,
)


def make_form(
    email: str = "user@example.com", username: str = "viewer", password: str = "secret-pass"
) -> RegisterForm:
    return RegisterForm(
        email=email, username=username, password=password, password_confirm=password
    )


async def test_register_stores_hashed_password(db_session: AsyncSession) -> None:
    user = await register_user(db_session, make_form(email="User@Example.com"))

    assert user.id is not None
    assert user.email == "user@example.com"
    assert user.password_hash.startswith("$argon2id$")
    assert "secret-pass" not in user.password_hash


async def test_register_rejects_duplicates_case_insensitively(db_session: AsyncSession) -> None:
    await register_user(db_session, make_form())

    with pytest.raises(RegistrationError) as error:
        await register_user(db_session, make_form(email="USER@example.com", username="VIEWER"))

    assert set(error.value.errors) == {"email", "username"}


@pytest.mark.parametrize("login", ["user@example.com", "USER@EXAMPLE.COM", "viewer", "Viewer"])
async def test_authenticate_by_email_or_username(db_session: AsyncSession, login: str) -> None:
    user = await register_user(db_session, make_form())

    result = await authenticate(db_session, LoginForm(login=login, password="secret-pass"))

    assert result is not None
    assert result.id == user.id


async def test_authenticate_rejects_wrong_password(db_session: AsyncSession) -> None:
    await register_user(db_session, make_form())

    result = await authenticate(db_session, LoginForm(login="viewer", password="wrong-pass"))

    assert result is None


async def test_authenticate_unknown_user(db_session: AsyncSession) -> None:
    result = await authenticate(db_session, LoginForm(login="ghost", password="secret-pass"))

    assert result is None


async def test_session_lifecycle(db_session: AsyncSession) -> None:
    user = await register_user(db_session, make_form())
    token = await create_session(db_session, user, timedelta(days=1))

    found = await get_user_by_session_token(db_session, token)
    assert found is not None
    assert found.id == user.id

    await delete_session(db_session, token)
    assert await get_user_by_session_token(db_session, token) is None


async def test_expired_session_is_ignored(db_session: AsyncSession) -> None:
    user = await register_user(db_session, make_form())
    token = await create_session(db_session, user, timedelta(seconds=-1))

    assert await get_user_by_session_token(db_session, token) is None


async def test_unknown_session_token(db_session: AsyncSession) -> None:
    assert await get_user_by_session_token(db_session, "missing") is None


@pytest.mark.parametrize(
    ("data", "field"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"email": ""}, "email"),
        ({"username": "ab"}, "username"),
        ({"username": "bad name"}, "username"),
        ({"password": "short", "password_confirm": "short"}, "password"),
        ({"password_confirm": "different-pass"}, "password_confirm"),
    ],
)
def test_register_form_validation(data: dict[str, str], field: str) -> None:
    values = {
        "email": "user@example.com",
        "username": "viewer",
        "password": "secret-pass",
        "password_confirm": "secret-pass",
        **data,
    }

    with pytest.raises(ValidationError) as error:
        RegisterForm(**values)

    assert field in form_errors(error.value)
