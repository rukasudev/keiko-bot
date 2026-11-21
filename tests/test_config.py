"""What the configuration reads, and what the bot can boot without.

The heartbeat URL is optional: a deploy that never created the parameter, or
whose role may not read it, must still start, with the heartbeat off. The
version comes from the environment the deploy sets, and reads `dev` anywhere
else.
"""
import pytest
from botocore.exceptions import ClientError

from app.config import AppConfig, app_version
from tests.helpers.config import production_config

pytestmark = pytest.mark.unit

HEARTBEAT = "/keiko/heartbeat/url"


def test_a_heartbeat_parameter_never_created_leaves_the_heartbeat_off(monkeypatch):
    config, _ = production_config(monkeypatch, missing={HEARTBEAT})

    assert config.HEARTBEAT_URL == ""


def test_a_heartbeat_parameter_the_bot_may_not_read_leaves_the_heartbeat_off(
    monkeypatch,
):
    config, _ = production_config(monkeypatch, denied={HEARTBEAT})

    assert config.HEARTBEAT_URL == ""


def test_production_reads_the_heartbeat_url_from_ssm(monkeypatch):
    config, ssm = production_config(monkeypatch)

    assert config.HEARTBEAT_URL == f"value of {HEARTBEAT}"
    assert HEARTBEAT in ssm.asked


def test_a_mandatory_parameter_that_is_missing_still_stops_the_boot(monkeypatch):
    with pytest.raises(ClientError):
        production_config(monkeypatch, missing={"/keiko/discord/bot_token"})


def test_the_local_heartbeat_url_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    config = AppConfig.__new__(AppConfig)

    config.get_local_configs()

    assert config.HEARTBEAT_URL == ""


def test_the_version_is_the_one_the_deploy_names(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "v1.0.0")

    assert app_version() == "v1.0.0"


def test_the_version_reads_dev_when_the_deploy_names_none(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)

    assert app_version() == "dev"


def test_the_boot_never_asks_ssm_for_an_openai_key(monkeypatch):
    """The integration that read it was imported by nothing, and is gone."""
    config, ssm = production_config(monkeypatch)

    assert "/keiko/openai/api_key" not in ssm.asked
    assert not hasattr(config, "OPENAI_API_KEY")
