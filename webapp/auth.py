import hashlib
import hmac
import os

from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy import text

PBKDF2_ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algorithm, iterations, salt_hex, digest_hex = stored_hash.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False

    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, int(iterations))
    return hmac.compare_digest(actual, expected)


def log_in(request: Request, user: dict) -> None:
    request.session["user_id"] = str(user["id"])
    request.session["full_name"] = user["full_name"]
    request.session["role"] = user["role"]


def log_out(request: Request) -> None:
    request.session.clear()


def current_user(request: Request) -> dict | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return {
        "id": user_id,
        "full_name": request.session.get("full_name"),
        "role": request.session.get("role"),
    }


def require_login(request: Request):
    """Devuelve el usuario en sesion, o un RedirectResponse a /login si no
    hay sesion activa. Se usa asi en cada ruta protegida:

        user = require_login(request)
        if isinstance(user, RedirectResponse):
            return user
    """
    user = current_user(request)
    if user is None:
        return RedirectResponse(url="/login", status_code=303)
    return user


def require_admin(request: Request):
    """Igual que require_login, pero ademas exige rol admin."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] != "admin":
        return RedirectResponse(url="/", status_code=303)
    return user


def require_role(request: Request, roles: tuple[str, ...]):
    """Igual que require_login, pero ademas exige que el rol este dentro de
    `roles`. Ej.: require_role(request, ("calidad", "admin"))."""
    user = require_login(request)
    if isinstance(user, RedirectResponse):
        return user
    if user["role"] not in roles:
        return RedirectResponse(url="/", status_code=303)
    return user


def find_user_by_email(conn, email: str) -> dict | None:
    row = conn.execute(
        text(
            "SELECT u.id, u.full_name, u.password_hash, r.code AS role "
            "FROM app.users u "
            "LEFT JOIN app.user_roles ur ON ur.user_id = u.id "
            "LEFT JOIN app.roles r ON r.id = ur.role_id "
            "WHERE u.email = :email"
        ),
        {"email": email},
    ).mappings().one_or_none()
    return dict(row) if row else None
