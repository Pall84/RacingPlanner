"""Per-user symmetric encryption for Garmin credentials.

Replaces the original single-global-key scheme (which also wrote the key to
`.env` at runtime — incompatible with a read-only filesystem like Render).

Design
------
- One master secret (`GARMIN_MASTER_KEY`) set as an env var, never on disk.
- Each athlete gets a random 16-byte salt stored in `athlete.garmin_key_salt`,
  generated on first encryption.
- Per-user Fernet key = HKDF-SHA256(master, salt=salt, info="garmin-cred-v1").
- Compromise of one user's ciphertext does not help attack another user's.

The master key must be at least 16 bytes of entropy (hex-encoded is fine —
`secrets.token_hex(32)` gives a comfortable 256-bit key).
"""
from __future__ import annotations

import base64
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import get_settings
from app.models.schema import Athlete

_HKDF_INFO = b"racingplanner.garmin-cred-v1"
_SALT_LEN = 16


class GarminCryptoError(RuntimeError):
    """Raised when encryption/decryption cannot complete."""


def _master_key_bytes() -> bytes:
    """Normalize the master key (hex or raw) into 32 bytes for HKDF input."""
    raw = get_settings().garmin_master_key
    if not raw:
        raise GarminCryptoError(
            "GARMIN_MASTER_KEY is not configured. Generate one with "
            "`python -c 'import secrets; print(secrets.token_hex(32))'` "
            "and set it as an env var."
        )
    # Accept either hex-encoded or raw string.
    try:
        return bytes.fromhex(raw)
    except ValueError:
        return raw.encode("utf-8")


def _derive_fernet_key(salt: bytes) -> bytes:
    """Derive a 32-byte Fernet key for a given athlete salt."""
    hkdf = HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        info=_HKDF_INFO,
    )
    derived = hkdf.derive(_master_key_bytes())
    return base64.urlsafe_b64encode(derived)


def ensure_salt(athlete: Athlete) -> bytes:
    """Return the athlete's salt, generating one if missing.

    Caller is responsible for committing the session after this mutates the
    athlete row.
    """
    if not athlete.garmin_key_salt:
        athlete.garmin_key_salt = os.urandom(_SALT_LEN)
    return athlete.garmin_key_salt


def encrypt_for_athlete(athlete: Athlete, plaintext: str) -> str:
    """Encrypt `plaintext` under the athlete's derived key. Returns a utf-8 token."""
    salt = ensure_salt(athlete)
    key = _derive_fernet_key(salt)
    token = Fernet(key).encrypt(plaintext.encode("utf-8"))
    return token.decode("utf-8")


def decrypt_for_athlete(athlete: Athlete, token: str) -> str:
    """Decrypt a token previously produced for this athlete. Raises on tamper."""
    if not athlete.garmin_key_salt:
        raise GarminCryptoError("Athlete has no Garmin key salt — nothing was encrypted for them.")
    key = _derive_fernet_key(athlete.garmin_key_salt)
    try:
        return Fernet(key).decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise GarminCryptoError("Ciphertext is invalid or tampered") from e
