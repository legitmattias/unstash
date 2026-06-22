"""API token generation, hashing, and parsing helpers.

The token format is::

    uns_<env>_<random>

Where ``<env>`` is ``live``, ``staging``, ``dev``, or ``test``, and
``<random>`` is the URL-safe base64 encoding of 32 random bytes drawn
from ``secrets.token_urlsafe``. The random portion carries roughly 256
bits of entropy.

Storage and verification use SHA-256 of the full token string. Because
the secret is high-entropy, a slow KDF (Argon2/bcrypt/scrypt) would add
latency without adding security — brute-forcing a 256-bit random is
infeasible regardless of hash speed. See ADR 0006 for the rationale.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass

_TOKEN_PREFIX = "uns"  # noqa: S105 — token brand prefix, not a credential
_RANDOM_BYTES = 32
_TOKEN_HASH_LENGTH = 32  # SHA-256 digest size in bytes.
_TOKEN_PARTS = 3  # prefix, env, random

_ALLOWED_ENVS = frozenset({"live", "staging", "dev", "test"})


@dataclass(frozen=True, slots=True)
class GeneratedToken:
    """A freshly generated token and its storage-ready hash.

    The plaintext is returned exactly once: the operator must capture it
    at creation time. Only the hash is persisted.
    """

    plaintext: str
    token_hash: bytes


def env_for_environment(environment: str) -> str:
    """Map the application environment string to the token env prefix.

    Production maps to ``live`` because that's the conventional name in
    API key UIs (Stripe, GitHub all use ``live``). Anything unrecognised
    falls back to ``dev``.
    """
    mapping = {
        "production": "live",
        "staging": "staging",
        "development": "dev",
        "test": "test",
    }
    return mapping.get(environment, "dev")


def generate_token(env: str) -> GeneratedToken:
    """Create a new token and its SHA-256 hash.

    Raises:
        ValueError: if ``env`` is not a recognised environment label.
    """
    if env not in _ALLOWED_ENVS:
        msg = f"Unknown token env {env!r}; expected one of {sorted(_ALLOWED_ENVS)}."
        raise ValueError(msg)
    random_part = secrets.token_urlsafe(_RANDOM_BYTES)
    plaintext = f"{_TOKEN_PREFIX}_{env}_{random_part}"
    return GeneratedToken(plaintext=plaintext, token_hash=hash_token(plaintext))


def hash_token(plaintext: str) -> bytes:
    """Return the SHA-256 digest of the token plaintext (32 bytes)."""
    return hashlib.sha256(plaintext.encode("utf-8")).digest()


def looks_like_unstash_token(value: str) -> bool:
    """Cheap shape check used to filter Bearer headers before hashing.

    A real token has at least three underscore-separated segments and
    starts with ``uns_``. The check is intentionally minimal — its job
    is to short-circuit obviously-not-our-token values (e.g. a JWT) so
    we do not waste a DB roundtrip looking them up.
    """
    if not value.startswith(f"{_TOKEN_PREFIX}_"):
        return False
    parts = value.split("_", 2)
    return len(parts) == _TOKEN_PARTS and parts[1] in _ALLOWED_ENVS and bool(parts[2])


def constant_time_equals(a: bytes, b: bytes) -> bool:
    """Constant-time comparison of two byte strings.

    Wraps ``hmac.compare_digest`` to give a more descriptive call site.
    """
    return hmac.compare_digest(a, b)


__all__ = [
    "GeneratedToken",
    "constant_time_equals",
    "env_for_environment",
    "generate_token",
    "hash_token",
    "looks_like_unstash_token",
]
