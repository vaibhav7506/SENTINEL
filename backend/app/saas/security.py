"""Random opaque credentials and memory-hard password hashes without changing ML locks."""

import hashlib
import hmac
import re
import secrets
from dataclasses import dataclass
from uuid import UUID

from fastapi import HTTPException, Request


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def password_hash(password: str) -> str:
    salt = secrets.token_bytes(16)
    result = hashlib.scrypt(
        password.encode(), salt=salt, n=2**17, r=8, p=1, dklen=64, maxmem=256 * 1024 * 1024
    )
    return "scrypt$131072$8$1$" + salt.hex() + "$" + result.hex()


def verify_password(password: str, encoded: str) -> bool:
    try:
        kind, n, r, p, salt, expected = encoded.split("$")
        if (kind, n, r, p) != ("scrypt", "131072", "8", "1"):
            return False
        actual = hashlib.scrypt(
            password.encode(),
            salt=bytes.fromhex(salt),
            n=2**17,
            r=8,
            p=1,
            dklen=64,
            maxmem=256 * 1024 * 1024,
        )
        return hmac.compare_digest(actual, bytes.fromhex(expected))
    except ValueError:
        return False


def normalized_email(value: str) -> str:
    value = value.strip().casefold()
    if len(value) > 320 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value):
        raise ValueError("Invalid email")
    return value


def raw_secret(prefix: str) -> str:
    return prefix + secrets.token_urlsafe(32)


@dataclass(frozen=True)
class Principal:
    user_id: UUID
    account_id: UUID
    email: str
    role: str
    session_id: UUID
    csrf_hash: str
    account_name: str


def require_role(request: Request, *roles: str) -> Principal:
    principal = getattr(request.state, "principal", None)
    if not isinstance(principal, Principal):
        raise HTTPException(401, "Authentication required")
    if principal.role not in roles:
        raise HTTPException(403, "This role cannot perform that action")
    return principal
