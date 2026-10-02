"""What the configuration reads, and what the bot can boot without.

The heartbeat URL is optional: a deploy that never created the parameter, or
whose role may not read it, must still start, with the heartbeat off. The
version comes from the environment the deploy sets, and reads `dev` anywhere
else.
"""
import pytest
from botocore.exceptions import ClientError

from app import config as config_module
from app.config import AppConfig, app_version

pytestmark = pytest.mark.unit

HEARTBEAT = "/keiko/heartbeat/url"


def refusal(code, name):
    return ClientError({"Error": {"Code": code, "Message": name}}, "GetParameter")


class FakeSSM:
    """Answers every parameter but the ones it refuses, the way SSM refuses them."""

    def __init__(self, missing=(), denied=()):
        self.missing = set(missing)
        self.denied = set(denied)
        self.asked = []

    def get_parameter(self, Name, WithDecryption=False):
        self.asked.append(Name)
        if Name in self.missing:
            raise refusal("ParameterNotFound", Name)
        if Name in self.denied:
            raise refusal("AccessDeniedException", Name)
        return {"Parameter": {"Value": f"value of {Name}"}}


def production_config(monkeypatch, missing=(), denied=()):
    ssm = FakeSSM(missing, denied)
    monkeypatch.setattr(config_module.boto3, "client", lambda *args, **kwargs: ssm)
    config = AppConfig.__new__(AppConfig)
    config.get_ssm_configs()
    return config, ssm


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

