"""Fixtures for the behavioral suite.

Builds on top of tests/conftest.py (real i18n, early-patched Mongo/Redis,
autouse dependency injection). Adds: repo-root CWD guard (the form YAML
loader resolves relative to CWD), a FormScenario factory, a two-locale
parametrized fixture, the golden transcript recorder/checker, and the admin
log channels as the real handlers fill them.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.behavioral.harness import golden as golden_module
from tests.behavioral.harness.driver import FormScenario
from tests.mocks.discord import create_guild, create_member

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
GOLDEN_ROOT = Path(__file__).resolve().parent / "golden"


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden", action="store_true", default=False,
        help="rewrite the golden transcripts from the engine under test",
    )


@pytest.fixture(autouse=True)
def _repo_root_cwd(monkeypatch):
    monkeypatch.chdir(REPO_ROOT)


@pytest.fixture(autouse=True)
def _fresh_runtime():
    """Every test starts with no open form session, like a freshly started bot."""
    from app.settings.discord.callbacks import RUNTIME

    RUNTIME.reset()
    yield
    RUNTIME.reset()


@pytest.fixture
def scenario_factory(deps):
    """Build FormScenario instances bound to a fresh mock guild + Mongo."""

    def factory(locale: str = "pt-br", guild=None, user=None) -> FormScenario:
        guild = guild or create_guild()
        user = user or create_member(guild, id=555, name="Tester")
        return FormScenario(guild=guild, user=user, locale=locale,
                            mongo=deps.mongo_client)

    return factory


@pytest.fixture(params=["pt-br", "en-us"])
def each_locale(request):
    return request.param


class GoldenCheck:
    """Resolve `tests/behavioral/golden/<form>/<scenario>.<locale>.json` and
    either record it (`--update-golden`) or assert the scenario matches it."""

    def __init__(self, update: bool):
        self.update = update

    @staticmethod
    def path(form: str, scenario: str, locale: str) -> Path:
        return GOLDEN_ROOT / form / f"{scenario}.{locale}.json"

    def check(self, scenario: FormScenario, form: str, name: str, locale: str,
              allowed=()) -> Path:
        path = self.path(form, name, locale)
        if self.update:
            golden_module.record(scenario, path)
        else:
            golden_module.assert_golden(scenario, path, allowed=allowed)
        return path


@pytest.fixture
def golden(request) -> GoldenCheck:
    return GoldenCheck(update=request.config.getoption("--update-golden", default=False))


class _Sent:
    """What `channel.send` hands back: an awaitable the handler may close unawaited."""

    def __await__(self):
        yield
        return None

    def close(self):
        return None


class LogChannel:
    """One admin log channel, recording each embed the moment it is posted."""

    def __init__(self):
        self.embeds = []

    def send(self, embed=None, **kwargs):
        self.embeds.append(embed)
        return _Sent()


@pytest.fixture
def log_channels(monkeypatch):
    """The handler pair `LoggerHooks.start` installs, posting into fake channels.

    Each admin channel is its own `LogChannel`, so a test says where a message
    went and not only that one was sent; `traces` holds every trace that closed,
    posted or not.
    """
    from app import logger as logger_module
    from app.services import journey as journey_service
    from app.services import trace as trace_service

    channels = {channel_id: LogChannel() for channel_id in (1, 2, 3, 4)}
    bot = SimpleNamespace(
        loop=None,
        config=SimpleNamespace(
            ADMIN_LOGS_CHANNEL_ID=1,
            ADMIN_LOGS_ERROR_CHANNEL_ID=2,
            ADMIN_LOGS_COMMAND_CALL_ID=3,
            ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID=4,
        ),
        get_channel=channels.get,
    )
    closed = []
    monkeypatch.setattr(trace_service, "_sinks", [])
    monkeypatch.setattr(journey_service, "_PUBLISHER", journey_service._PUBLISHER)
    monkeypatch.setattr(
        journey_service, "_RECOVERY_LISTENER", journey_service._RECOVERY_LISTENER
    )
    trace_service.register_sink(closed.append)
    folding = logger_module.TraceFoldingHandler()
    logger_module.logger.addHandler(folding)
    handler = logger_module.DiscordLogsHandler(bot)

    yield SimpleNamespace(
        logs=channels[1],
        errors=channels[2],
        calls=channels[3],
        actions=channels[4],
        traces=closed,
    )

    logger_module.logger.removeHandler(handler)
    logger_module.logger.removeHandler(folding)
