"""
Fixtures every test gets: the in-memory Mongo and Redis, the fake bot with the
fake Twitch and YouTube APIs, the default server, banners that never reach the
network, and caches, clocks and observability that never leak between tests.

    def test_example(deps, twitch, guild):
        twitch.add_user("gaules", user_id="123")
        twitch.set_stream_online("gaules", game="CS2")
"""

import asyncio
import os
import threading
import sys

import pytest
import pytest_asyncio
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock

# Os clientes boto3 nunca perguntam ao endereco de metadados da AWS nos testes.
os.environ.setdefault("AWS_EC2_METADATA_DISABLED", "true")

# Early patching happens as side-effect of this import
from tests.mocks.database import FakeAsyncMongoClient, MockRedisClient, MockMongoClient, SettlingLoop
from tests.generators.moderations import recorded_server
from app.data import build as build_data, install as install_data
from app.core import i18n

i18n.load()

from tests.mocks import (
    create_guild,
    create_member,
    MockKickAPI,
    MockTwitchAPI,
    MockYouTubeAPI,
    MockChannel,
    MockInteraction,
)


# ============================================================================
# BANCO DE DADOS - FIXTURES
# ============================================================================

@pytest.fixture(scope="function")
def redis_client():
    """O Redis em memoria."""
    return MockRedisClient()


@pytest.fixture(scope="function")
def mongodb():
    """O store em memoria: os testes semeiam e conferem documentos nele, de forma sync."""
    return MockMongoClient()


# ============================================================================
# FIXTURE PRINCIPAL: Container de Dependencias
# ============================================================================

@pytest.fixture
def deps(mongodb, redis_client):
    """
    Container de dependencias para o teste.

    Exemplo:
        def test_something(deps):
            deps.twitch.add_user("gaules")
            deps.twitch.set_stream_online("gaules", game="CS2")
    """
    ns = SimpleNamespace()

    # Banco de dados: o store sync e os repositorios async sobre ele
    ns.mongo_client = mongodb
    ns.data = build_data(FakeAsyncMongoClient(mongodb))
    ns.redis_client = redis_client

    # APIs mockadas
    ns.twitch = MockTwitchAPI()
    ns.youtube = MockYouTubeAPI()
    ns.kick = MockKickAPI()

    # Discord
    ns.guild = create_guild(
        id=123456789,
        name="Test Server",
        channels=["general", "welcome", "announcements"],
        roles=["Admin", "Moderator", "Member"]
    )

    recorded_server(mongodb, ns.guild.id)

    # Configura bot com dependencias
    ns.bot = MagicMock()
    ns.bot.twitch = ns.twitch
    ns.bot.youtube = ns.youtube
    ns.bot.kick = ns.kick
    ns.bot.get_guild = MagicMock(return_value=ns.guild)
    ns.bot.wait_until_ready = AsyncMock()
    ns.bot.config = MagicMock()
    ns.bot.config.ADMIN_DUMP_CHANNEL_ID = 999999
    ns.bot.config.PREFIX = "ks!"
    mock_dump_channel = MagicMock()
    mock_dump_channel.send = MagicMock()
    ns.bot.get_channel = MagicMock(return_value=mock_dump_channel)

    return ns


# ============================================================================
# AUTO-PATCH: Injeta dependencias automaticamente
# ============================================================================

def _bot_holders():
    """`app` and every app module that took its `bot` at import (`from app import bot`)."""
    import app

    original = app.__dict__.get("bot")
    return ["app"] + sorted(
        name
        for name, module in list(sys.modules.items())
        if name.startswith("app.")
        and module is not None
        and module.__dict__.get("bot") is original
    )


@pytest.fixture(autouse=True)
def auto_inject_dependencies(deps):
    """AUTOUSE: every test runs on the fake bot, the in-memory stores and the
    test loop; a new service holding `bot` needs no line here."""
    patches = [patch(f"{module}.bot", deps.bot) for module in _bot_holders()] + [
        patch('app.integrations.youtube.hub._RENEWALS', new_callable=asyncio.Lock),
        patch('app.core.cache.redis_client', deps.redis_client),
        patch('app.data._own_loop', SettlingLoop),
        patch('app.data._LOOP', None),
        patch('app.data._LOOP_EXPECTED', threading.Event()),
        patch('app.data._LOOP_BOUND', threading.Event()),
        patch(
            'app.integrations.stream_elements.StreamElementsClient.get_channel_info',
            staticmethod(lambda name: {"_id": f"se-{name}"}),
        ),
        patch(
            'app.integrations.stream_elements.StreamElementsClient.get_chat_commands',
            staticmethod(lambda channel_id: [
                {"enabled": True}, {"enabled": True}, {"enabled": False},
            ]),
        ),
    ]

    install_data(deps.data)

    started_patches = []
    for p in patches:
        try:
            p.start()
            started_patches.append(p)
        except Exception:
            pass

    yield

    for p in started_patches:
        p.stop()


@pytest.fixture(autouse=True)
def _no_banner_rendering():
    """AUTOUSE: banners e exemplos passam pela rede; os testes recebem URLs fixas
    e um PNG pronto. Quem precisa do desenho real restaura as funcoes."""
    from io import BytesIO
    from unittest.mock import AsyncMock

    from tests.mocks.web import png

    with patch(
        "app.commands.welcome.service.create_banner",
        new=AsyncMock(return_value="https://cdn.example.com/previews/welcome-preview.png"),
    ), patch(
        "app.commands.welcome.service.draw_banner",
        new=AsyncMock(side_effect=lambda *args, **kwargs: BytesIO(png())),
    ), patch(
        "app.core.images.upload_asset",
        new=AsyncMock(return_value="https://cdn.example.com/previews/welcome-example.gif"),
    ):
        yield


@pytest.fixture(autouse=True)
def observability_isolation():
    """AUTOUSE: nenhum evento nem log vaza de um teste para o proximo."""
    from app.core import analytics, debug_logs, trace

    analytics.reset()
    debug_logs.reset()
    trace.clear_sinks()
    trace.drain_runs()
    yield
    analytics.reset()
    debug_logs.reset()
    trace.clear_sinks()
    trace.drain_runs()


@pytest.fixture(autouse=True)
def birthday_today(monkeypatch):
    """AUTOUSE: the birthday panel counts from 2026-01-01, whatever the real date,
    so its next birthdays and the month it opens on never move with the
    calendar. A test that needs another day sets `FEATURE.today` itself."""
    from datetime import date

    from app.commands.birthdays.service import FEATURE

    monkeypatch.setattr(FEATURE, "today", lambda: date(2026, 1, 1))
    yield


@pytest.fixture(autouse=True)
def config_cache_isolation():
    """AUTOUSE: o que o cache lembrou e as quedas que viu nao passam de um teste ao outro."""
    from app.core import cache, members

    cache.reset()
    members.reset()
    yield
    cache.reset()
    members.reset()


@pytest.fixture
def analytics_events():
    """Grava os eventos emitidos durante o teste, sem tocar o banco."""
    from app.core import analytics

    recorded = analytics.start_recording()
    yield recorded
    analytics.stop_recording()


# ============================================================================
# FIXTURES DE CONVENIENCIA - APIs
# ============================================================================

@pytest.fixture
def twitch(deps) -> MockTwitchAPI:
    """Acesso direto ao mock da Twitch."""
    return deps.twitch


@pytest.fixture
def youtube(deps) -> MockYouTubeAPI:
    """Acesso direto ao mock do YouTube."""
    return deps.youtube


# ============================================================================
# FIXTURES DE CONVENIENCIA - DISCORD
# ============================================================================

@pytest.fixture
def guild(deps):
    """Guild padrao para testes."""
    return deps.guild


@pytest.fixture
def bot(deps):
    """Bot mock para testes."""
    return deps.bot


@pytest.fixture
def member(guild):
    """Member padrao para testes."""
    return create_member(guild, id=111, name="TestUser", roles=["Member"])


@pytest.fixture
def bot_member(guild):
    """Member que e um bot."""
    return create_member(guild, id=333, name="BotUser", roles=["Member"], bot=True)


@pytest.fixture
def channel(guild) -> MockChannel:
    """Canal padrao (#general)."""
    return guild.text_channels[0]


@pytest.fixture
def interaction(member, guild, channel) -> MockInteraction:
    """Interaction padrao para testes de comandos."""
    return MockInteraction(user=member, guild=guild, channel=channel)


# ============================================================================
# AUTO-PATCH FIXTURES
# ============================================================================

@pytest.fixture
def mock_cache():
    """Mock do cache.read_cog_data."""
    with patch("app.core.cache.read_cog_data") as mock:
        yield mock


@pytest.fixture
def mock_banner():
    """Mock do draw_banner em welcome_messages: um PNG pronto, sem baixar nada."""
    from io import BytesIO

    from tests.mocks.web import png

    with patch('app.commands.welcome.service.draw_banner') as mock:
        mock.side_effect = lambda *args, **kwargs: BytesIO(png())
        yield mock


@pytest_asyncio.fixture(autouse=True)
async def event_loop_for_every_test():
    """AUTOUSE: every test, a sync one included, runs with pytest-asyncio's loop
    current, as the code that reads `asyncio.get_event_loop()` expects."""
    yield

