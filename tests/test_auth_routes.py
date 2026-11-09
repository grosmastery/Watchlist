import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.conftest import fetch_csrf_token
from watchlist.auth.dependencies import SESSION_COOKIE
from watchlist.auth.models import User, UserSession
from watchlist.auth.routes import safe_next

PASSWORD = "secret-pass"


async def register(
    client: AsyncClient, email: str = "user@example.com", username: str = "viewer"
) -> None:
    token = await fetch_csrf_token(client, "/register")
    response = await client.post(
        "/register",
        data={
            "csrf_token": token,
            "email": email,
            "username": username,
            "password": PASSWORD,
            "password_confirm": PASSWORD,
        },
    )
    assert response.status_code == 303


async def logout(client: AsyncClient) -> None:
    token = await fetch_csrf_token(client, "/")
    response = await client.post("/logout", data={"csrf_token": token})
    assert response.status_code == 303


async def test_home_is_public(client: AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    assert "Вхід" in response.text


async def test_login_redirect_keeps_requested_path(client: AsyncClient) -> None:
    response = await client.get("/list?status=watching")

    assert response.status_code == 303
    assert response.headers["location"] == "/login?next=%2Flist%3Fstatus%3Dwatching"


async def test_htmx_request_gets_hx_redirect(client: AsyncClient) -> None:
    response = await client.get("/up-next", headers={"HX-Request": "true"})

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/login?next=%2Fup-next"


async def test_register_logs_user_in(client: AsyncClient, db_session: AsyncSession) -> None:
    await register(client)

    assert client.cookies.get(SESSION_COOKIE)
    home = await client.get("/")
    assert home.status_code == 200
    assert "viewer" in home.text
    count = await db_session.scalar(select(func.count()).select_from(User))
    assert count == 1


async def test_register_shows_validation_errors(client: AsyncClient) -> None:
    token = await fetch_csrf_token(client, "/register")
    response = await client.post(
        "/register",
        data={
            "csrf_token": token,
            "email": "broken",
            "username": "ok_name",
            "password": "short",
            "password_confirm": "short",
        },
    )

    assert response.status_code == 400
    assert "Некоректна адреса пошти" in response.text
    assert "щонайменше 8 символів" in response.text
    assert 'value="ok_name"' in response.text
    assert SESSION_COOKIE not in client.cookies


async def test_register_rejects_taken_email(client: AsyncClient) -> None:
    await register(client)
    await logout(client)
    token = await fetch_csrf_token(client, "/register")

    response = await client.post(
        "/register",
        data={
            "csrf_token": token,
            "email": "USER@example.com",
            "username": "another",
            "password": PASSWORD,
            "password_confirm": PASSWORD,
        },
    )

    assert response.status_code == 400
    assert "Ця пошта вже зареєстрована" in response.text


@pytest.mark.parametrize("login", ["user@example.com", "viewer"])
async def test_login_and_logout(client: AsyncClient, login: str) -> None:
    await register(client)
    await logout(client)
    assert (await client.get("/up-next")).status_code == 303

    token = await fetch_csrf_token(client)
    response = await client.post(
        "/login", data={"csrf_token": token, "login": login, "password": PASSWORD}
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert (await client.get("/up-next")).status_code == 200


async def test_logout_deletes_session(client: AsyncClient, db_session: AsyncSession) -> None:
    await register(client)

    await logout(client)

    assert await db_session.scalar(select(func.count()).select_from(UserSession)) == 0
    assert (await client.get("/up-next")).status_code == 303


async def test_login_with_wrong_password(client: AsyncClient) -> None:
    await register(client)
    await logout(client)
    token = await fetch_csrf_token(client)

    response = await client.post(
        "/login", data={"csrf_token": token, "login": "viewer", "password": "wrong-pass"}
    )

    assert response.status_code == 400
    assert "Неправильний логін або пароль" in response.text
    assert 'value="viewer"' in response.text


async def test_login_redirects_to_next(client: AsyncClient) -> None:
    await register(client)
    await logout(client)
    token = await fetch_csrf_token(client)

    response = await client.post(
        "/login",
        data={
            "csrf_token": token,
            "login": "viewer",
            "password": PASSWORD,
            "next": "/?status=watching",
        },
    )

    assert response.headers["location"] == "/?status=watching"


async def test_logged_in_user_skips_auth_pages(client: AsyncClient) -> None:
    await register(client)

    for path in ("/login", "/register"):
        response = await client.get(path)
        assert response.status_code == 303
        assert response.headers["location"] == "/"


async def test_post_without_csrf_token_is_rejected(client: AsyncClient) -> None:
    await client.get("/login")

    response = await client.post("/login", data={"login": "viewer", "password": PASSWORD})

    assert response.status_code == 403


async def test_post_with_forged_csrf_token_is_rejected(client: AsyncClient) -> None:
    await client.get("/login")

    response = await client.post(
        "/login", data={"csrf_token": "forged", "login": "viewer", "password": PASSWORD}
    )

    assert response.status_code == 403


async def test_csrf_header_is_accepted(client: AsyncClient) -> None:
    await register(client)
    token = await fetch_csrf_token(client, "/")

    response = await client.post("/logout", headers={"X-CSRF-Token": token})

    assert response.status_code == 303


async def test_post_without_csrf_cookie_is_rejected(client: AsyncClient) -> None:
    response = await client.post(
        "/login", data={"csrf_token": "anything", "login": "viewer", "password": PASSWORD}
    )

    assert response.status_code == 403


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("/list", "/list"),
        ("/?a=1", "/?a=1"),
        ("https://evil.example", "/"),
        ("//evil.example", "/"),
        ("/\\evil.example", "/"),
        ("", "/"),
        (None, "/"),
    ],
)
def test_safe_next(value: str | None, expected: str) -> None:
    assert safe_next(value) == expected
