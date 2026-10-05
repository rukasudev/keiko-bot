"""Files only the holder of Keiko's age private key can open.

The monthly events archive and the daily backup leave the database for a Discord
channel. Lucas decided neither may be readable there: both are encrypted to one
age public key (SSM `/keiko/backup/age_public_key`, `BACKUP_AGE_PUBLIC_KEY`
locally), and only the matching private key, which the bot never holds, opens
them (`age -d -i key.txt`).

Guaranteed: a sealed payload round-trips with the private key and with no other;
a missing, blank or malformed public key reads as no key, so a caller posts
nothing; the largest payload `plaintext_limit` allows still fits the upload limit
once sealed, whatever stanza the age header draws; production reads the key from
SSM and boots without it, and a local run reads it from the environment.
"""
import os

import pyrage
import pytest
from pyrage import x25519

from app.config import AppConfig
from app.services import sealing
from tests.test_config import production_config

pytestmark = pytest.mark.unit

TEN_MB = 10 * 1024 * 1024
IDENTITY = x25519.Identity.generate()
PUBLIC_KEY = str(IDENTITY.to_public())
KEY_PARAMETER = "/keiko/backup/age_public_key"


def test_a_sealed_payload_opens_with_the_private_key_and_no_other():
    sealed = sealing.seal(b"guild documents", sealing.age_recipient(PUBLIC_KEY))

    assert b"guild documents" not in sealed
    assert pyrage.decrypt(sealed, [IDENTITY]) == b"guild documents"
    with pytest.raises(pyrage.DecryptError):
        pyrage.decrypt(sealed, [x25519.Identity.generate()])


@pytest.mark.parametrize("public_key", ["", "   ", "not-an-age-key", None])
def test_a_missing_or_malformed_public_key_reads_as_no_key(public_key):
    assert sealing.age_recipient(public_key) is None


def test_a_public_key_with_spaces_around_it_is_read():
    assert str(sealing.age_recipient(f"  {PUBLIC_KEY}\n")) == PUBLIC_KEY


@pytest.mark.parametrize("limit", [5000, 8 * 1024, 64 * 1024, 64 * 1024 + 1, TEN_MB])
def test_the_largest_payload_still_fits_the_limit_once_sealed(limit):
    """The age header carries a random stanza, so the same size is sealed many times."""
    payload = os.urandom(sealing.plaintext_limit(limit))
    recipient = IDENTITY.to_public()
    tries = 3 if limit == TEN_MB else 50

    sealed = max(len(sealing.seal(payload, recipient)) for _ in range(tries))

    assert sealed <= limit


def test_production_reads_the_public_key_from_ssm(monkeypatch):
    config, ssm = production_config(monkeypatch)

    assert config.BACKUP_AGE_PUBLIC_KEY == f"value of {KEY_PARAMETER}"
    assert KEY_PARAMETER in ssm.asked


def test_a_public_key_never_created_leaves_nothing_to_seal_with(monkeypatch):
    config, _ = production_config(monkeypatch, missing={KEY_PARAMETER})

    assert config.BACKUP_AGE_PUBLIC_KEY == ""
    assert sealing.age_recipient(config.BACKUP_AGE_PUBLIC_KEY) is None


def test_a_local_run_reads_the_public_key_from_the_environment(monkeypatch):
    monkeypatch.setenv("BACKUP_AGE_PUBLIC_KEY", PUBLIC_KEY)
    config = AppConfig.__new__(AppConfig)

    config.get_local_configs()

    assert config.BACKUP_AGE_PUBLIC_KEY == PUBLIC_KEY
