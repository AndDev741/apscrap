"""AC-1: who is asking.

Cloudflare Access sits in front of the app and lets only the emails in its
policy through. Every request it forwards carries a signed JWT in
Cf-Access-Jwt-Assertion; we check the signature against the team's public
keys and the audience tag of the Access application, and take the email from
it. Nothing else identifies a user, so the service refuses to start serving
without that configuration unless APSCRAP_DEV_USER is set for local work.

    CF_ACCESS_TEAM_DOMAIN   e.g. beyouweb.cloudflareaccess.com
    CF_ACCESS_AUD           the Access application's audience tag
    APSCRAP_DEV_USER        local development only: trust this email
"""
from __future__ import annotations

import os
from functools import lru_cache

import jwt

from .db import connect

TEAM = os.getenv("CF_ACCESS_TEAM_DOMAIN", "").strip().removeprefix("https://").rstrip("/")
AUD = os.getenv("CF_ACCESS_AUD", "").strip()
DEV_USER = os.getenv("APSCRAP_DEV_USER", "").strip()


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def configured() -> bool:
    return bool(DEV_USER or (TEAM and AUD))


@lru_cache(maxsize=1)
def _jwks() -> jwt.PyJWKClient:
    return jwt.PyJWKClient(f"https://{TEAM}/cdn-cgi/access/certs", cache_keys=True, lifespan=3600)


def email_from(headers, cookies) -> str:
    if DEV_USER:
        return DEV_USER
    if not (TEAM and AUD):
        raise AuthError(503, "Access is not configured (CF_ACCESS_TEAM_DOMAIN, CF_ACCESS_AUD)")
    token = headers.get("cf-access-jwt-assertion") or cookies.get("CF_Authorization")
    if not token:
        raise AuthError(401, "missing Cloudflare Access token")
    try:
        key = _jwks().get_signing_key_from_jwt(token)
        claims = jwt.decode(token, key.key, algorithms=["RS256"], audience=AUD,
                            issuer=f"https://{TEAM}")
    except jwt.PyJWTError as exc:
        raise AuthError(401, f"invalid Access token: {exc}") from exc
    email = (claims.get("email") or "").strip().lower()
    if not email:
        raise AuthError(401, "Access token without an email")
    return email


def user_for(email: str) -> dict:
    """The app_users row for this email. The first person to sign in takes
    slot a; the second takes slot b, or the slot b row someone prepared in
    Settings without an email yet. A third email gets nothing: Access should
    never let one through, and if it does the app still refuses."""
    email = email.lower()
    with connect() as conn:
        row = conn.execute("SELECT * FROM app_users WHERE lower(email) = %s", (email,)).fetchone()
        if row:
            return row
        free = conn.execute(
            "SELECT * FROM app_users WHERE email IS NULL ORDER BY slot LIMIT 1").fetchone()
        if free:
            row = conn.execute("UPDATE app_users SET email = %s WHERE id = %s RETURNING *",
                               (email, free["id"])).fetchone()
        else:
            taken = {r["slot"] for r in conn.execute("SELECT slot FROM app_users").fetchall()}
            slot = next((s for s in ("a", "b") if s not in taken), None)
            if slot is None:
                raise AuthError(403, "this app is for two people and both places are taken")
            name = email.split("@")[0].split(".")[0].capitalize()
            row = conn.execute("INSERT INTO app_users (slot, email, name) VALUES (%s, %s, %s) RETURNING *",
                               (slot, email, name)).fetchone()
        conn.commit()
        return row
