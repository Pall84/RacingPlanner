"""Unit tests for per-user Garmin credential encryption."""
from __future__ import annotations

import pytest

from app.garmin.crypto import (
    GarminCryptoError,
    decrypt_for_athlete,
    encrypt_for_athlete,
    ensure_salt,
)
from app.models.schema import Athlete


def _mk_athlete(id_: int = 1) -> Athlete:
    return Athlete(
        id=id_,
        access_token="x",
        refresh_token="x",
        token_expires=0,
    )


def test_roundtrip():
    a = _mk_athlete()
    ct = encrypt_for_athlete(a, "hunter2")
    assert ct != "hunter2"
    assert decrypt_for_athlete(a, ct) == "hunter2"


def test_salt_is_generated_on_first_encrypt():
    a = _mk_athlete()
    assert a.garmin_key_salt is None
    encrypt_for_athlete(a, "pw")
    assert a.garmin_key_salt is not None
    assert len(a.garmin_key_salt) == 16


def test_salt_is_stable_across_calls():
    a = _mk_athlete()
    encrypt_for_athlete(a, "pw1")
    salt_1 = a.garmin_key_salt
    encrypt_for_athlete(a, "pw2")
    assert a.garmin_key_salt == salt_1


def test_different_athletes_get_different_ciphertexts():
    a = _mk_athlete(1)
    b = _mk_athlete(2)
    ct_a = encrypt_for_athlete(a, "shared_pw")
    ct_b = encrypt_for_athlete(b, "shared_pw")
    assert ct_a != ct_b


def test_b_cannot_decrypt_a():
    a = _mk_athlete(1)
    b = _mk_athlete(2)
    ct_a = encrypt_for_athlete(a, "shared_pw")
    # Give b their own (different) salt by calling ensure_salt
    ensure_salt(b)
    with pytest.raises(GarminCryptoError):
        decrypt_for_athlete(b, ct_a)


def test_decrypt_without_salt_raises():
    a = _mk_athlete()
    with pytest.raises(GarminCryptoError):
        decrypt_for_athlete(a, "gibberish")
