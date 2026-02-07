"""
Fixtures globais para testes de integracao.

IMPORTANTE: Estas fixtures abstraem o uso de patch(), tornando
os testes mais limpos e didaticos.

Tipos de testes:
- Testes unitarios: Usam mocks para bancos de dados
- Testes de integracao: Marcados com @pytest.mark.integration, requerem DB real

Uso:
    def test_example(twitch, guild, mongodb):
        twitch.add_user("gaules", user_id="123")
        twitch.set_stream_online("gaules", game="CS2")
        # ... rest of test
"""

import pytest
import pytest_asyncio
import asyncio
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

# Early patching happens as side-effect of this import
from tests.mocks.database import MockRedisClient, MockMongoClient

from tests.mocks import (
    create_guild,
    create_member,
    create_message,
    MockTwitchAPI,
    MockYouTubeAPI,
    MockStreamElementsAPI,
    MockChannel,
    MockInteraction,
)


# ============================================================================
# MARKERS
# ============================================================================

def pytest_configure(config):
    """Registra markers customizados."""
    config.addinivalue_line(
        "markers", "integration: marca testes que requerem banco de dados real"
    )
    config.addinivalue_line(
        "markers", "unit: marca testes unitarios (sem I/O)"
    )


# ============================================================================
# EVENT LOOP
# ============================================================================

@pytest.fixture(scope="session")
def event_loop():
    """Event loop para toda a sessao."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


# ============================================================================
# BANCO DE DADOS - FIXTURES
# ============================================================================

@pytest.fixture(scope="function")
def redis_client(request):
    """
    Cliente Redis para testes.
    - Para testes unitarios: retorna MockRedisClient
    - Para testes de integracao (@pytest.mark.integration): conecta ao Redis real
    """
    if request.node.get_closest_marker("integration"):
        import redis
        client = redis.Redis(host='localhost', port=6379, db=15)
        client.flushdb()
        yield client
        client.flushdb()
        client.close()
    else:
        yield MockRedisClient()


@pytest_asyncio.fixture(scope="function")
async def mongodb(request):
    """
    Database MongoDB para testes.
    - Para testes unitarios: retorna MockMongoClient
    - Para testes de integracao (@pytest.mark.integration): conecta ao MongoDB real
    """
    if request.node.get_closest_marker("integration"):
        from motor.motor_asyncio import AsyncIOMotorClient
        client = AsyncIOMotorClient("mongodb://localhost:27017")
        db = client.keiko_test
        yield db
        await client.drop_database("keiko_test")
        client.close()
    else:
        yield MockMongoClient()


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

    # Banco de dados
    ns.mongo_client = mongodb
    ns.redis_client = redis_client

    # APIs mockadas
    ns.twitch = MockTwitchAPI()
    ns.youtube = MockYouTubeAPI()
    ns.stream_elements = MockStreamElementsAPI()

    # Discord
    ns.guild = create_guild(
        id=123456789,
        name="Test Server",
        channels=["general", "welcome", "announcements"],
        roles=["Admin", "Moderator", "Member"]
    )

    # Configura bot com dependencias
    ns.bot = MagicMock()
    ns.bot.twitch = ns.twitch
    ns.bot.youtube = ns.youtube
    ns.bot.get_guild = MagicMock(return_value=ns.guild)
    ns.bot.config = MagicMock()
    ns.bot.config.ADMIN_DUMP_CHANNEL_ID = 999999
    mock_dump_channel = MagicMock()
    mock_dump_channel.send = MagicMock()
    ns.bot.get_channel = MagicMock(return_value=mock_dump_channel)

    return ns


# ============================================================================
# AUTO-PATCH: Injeta dependencias automaticamente
# ============================================================================

@pytest.fixture(autouse=True)
def auto_inject_dependencies(deps):
    """
    AUTOUSE: Injeta automaticamente as dependencias em todos os testes.

    Esta fixture usa patch() internamente, mas voce nunca precisa
    usar patch() diretamente nos seus testes!
    """
    patches = [
        patch('app.bot', deps.bot),
        patch('app.services.notifications_twitch.bot', deps.bot),
        patch('app.services.notifications_youtube_video.bot', deps.bot),
        patch('app.services.stream_elements.bot', deps.bot),
        patch('app.services.welcome_messages.bot', deps.bot),
        patch('app.services.default_roles.bot', deps.bot),
        patch('app.services.subscriptions.bot', deps.bot),
        patch('app.data.cogs.mongo_client', deps.mongo_client),
        patch('app.data.notifications_twitch.mongo_client', deps.mongo_client),
        patch('app.data.notifications_youtube_video.mongo_client', deps.mongo_client),
        patch('app.data.moderations.mongo_client', deps.mongo_client),
        patch('app.data.reminder.mongo_client', deps.mongo_client),
        patch('app.services.cache.redis_client', deps.redis_client),
        patch('app.services.cache.cogs_data.mongo_client', deps.mongo_client),
    ]

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


@pytest.fixture
def stream_elements(deps) -> MockStreamElementsAPI:
    """Acesso direto ao mock do StreamElements."""
    return deps.stream_elements


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
def admin_member(guild):
    """Member com role Admin."""
    return create_member(guild, id=222, name="AdminUser", roles=["Admin"])


@pytest.fixture
def bot_member(guild):
    """Member que e um bot."""
    return create_member(guild, id=333, name="BotUser", roles=["Member"], bot=True)


@pytest.fixture
def channel(guild) -> MockChannel:
    """Canal padrao (#general)."""
    return guild.text_channels[0]


@pytest.fixture
def welcome_channel(guild) -> MockChannel:
    """Canal de welcome."""
    return guild.get_channel(101)


@pytest.fixture
def interaction(member, guild, channel) -> MockInteraction:
    """Interaction padrao para testes de comandos."""
    return MockInteraction(user=member, guild=guild, channel=channel)


@pytest.fixture
def admin_interaction(admin_member, guild, channel) -> MockInteraction:
    """Interaction de admin para testes de comandos."""
    return MockInteraction(user=admin_member, guild=guild, channel=channel)


# ============================================================================
# AUTO-PATCH FIXTURES
# ============================================================================

@pytest.fixture
def mock_cache():
    """Mock do cache.get_cog_data_or_populate."""
    with patch('app.services.cache.get_cog_data_or_populate') as mock:
        yield mock


@pytest.fixture
def mock_banner():
    """Mock do create_banner em welcome_messages."""
    with patch('app.services.welcome_messages.create_banner') as mock:
        mock.return_value = "https://example.com/banner.png"
        yield mock


@pytest.fixture
def mock_available_roles():
    """Mock do get_available_roles_by_guild em default_roles."""
    with patch('app.services.default_roles.get_available_roles_by_guild') as mock:
        yield mock


@pytest.fixture
def mock_ml():
    """Mock da funcao ml (i18n) em default_roles."""
    with patch('app.services.default_roles.ml') as mock:
        yield mock


@pytest.fixture
def twitch_data():
    """Mocks da camada de dados e helpers de notificacoes Twitch."""
    mocks = SimpleNamespace()
    targets = {
        'find_guilds': 'app.services.notifications_twitch.find_guilds_by_streamer_name',
        'find_last_stream_date': 'app.services.notifications_twitch.find_last_stream_date',
        'save_notification': 'app.services.notifications_twitch.save_stream_notification',
        'update_last_stream_date': 'app.services.notifications_twitch.update_last_stream_date',
        'find_notification': 'app.services.notifications_twitch.find_stream_notification',
        'count_guilds': 'app.services.notifications_twitch.count_streamers_guilds',
        'wait_for_stream_info': 'app.services.notifications_twitch.wait_for_stream_info',
        'is_more_than_one_hour': 'app.services.notifications_twitch.is_more_than_one_hour',
    }

    patches = []
    for attr, target in targets.items():
        p = patch(target)
        try:
            setattr(mocks, attr, p.start())
            patches.append(p)
        except Exception:
            setattr(mocks, attr, MagicMock())

    yield mocks

    for p in patches:
        p.stop()


# ============================================================================
# CLEANUP AUTOMATICO (apenas para testes de integracao)
# ============================================================================

@pytest_asyncio.fixture(autouse=True)
async def cleanup_after_test(mongodb, request):
    """Limpa dados apos cada teste de integracao."""
    yield
    if request.node.get_closest_marker("integration"):
        if hasattr(mongodb, 'list_collection_names'):
            collections = await mongodb.list_collection_names()
            for collection_name in collections:
                await mongodb[collection_name].delete_many({})
