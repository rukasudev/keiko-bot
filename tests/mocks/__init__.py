"""
Mocks reutilizaveis para testes.

Uso:
    from tests.mocks import create_guild, create_member, MockTwitchAPI
"""

from tests.mocks.discord import (
    MockRole,
    MockChannel,
    MockMember,
    MockGuild,
    MockMessage,
    MockInteraction,
    create_guild,
    create_member,
    create_message,
)

from tests.mocks.twitch import (
    MockTwitchAPI,
    StreamInfo,
    UserInfo,
)

from tests.mocks.youtube import (
    MockYouTubeAPI,
    VideoInfo,
    ChannelInfo,
)

from tests.mocks.stream_elements import (
    MockStreamElementsAPI,
)

from tests.mocks.database import (
    MockRedisClient,
    MockMongoClient,
    MockMongoDatabase,
    MockMongoCollection,
    MockCursor,
)

__all__ = [
    # Discord
    "MockRole",
    "MockChannel",
    "MockMember",
    "MockGuild",
    "MockMessage",
    "MockInteraction",
    "create_guild",
    "create_member",
    "create_message",
    # Twitch
    "MockTwitchAPI",
    "StreamInfo",
    "UserInfo",
    # YouTube
    "MockYouTubeAPI",
    "VideoInfo",
    "ChannelInfo",
    # StreamElements
    "MockStreamElementsAPI",
    # Database
    "MockRedisClient",
    "MockMongoClient",
    "MockMongoDatabase",
    "MockMongoCollection",
    "MockCursor",
]
