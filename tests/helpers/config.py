"""The production configuration read against a fake SSM that refuses what it is told to."""
from botocore.exceptions import ClientError

from app import config as config_module
from app.config import AppConfig


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
