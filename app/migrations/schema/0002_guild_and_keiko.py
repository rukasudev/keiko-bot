"""The server settings, one document per server and cog, and Keiko's own settings."""

from app.migrations.helpers import index

STRUCTURE = {
    "guild": {
        "cogs": [
            index("guild_id"),
            index("cog", "enabled"),
            index(
                "options.notifications.values.streamer.value",
                partial={"cog": "notifications_twitch"},
            ),
            index(
                "options.notifications.values.youtuber.value",
                partial={"cog": "notifications_youtube_video"},
            ),
        ],
        "settings": [index("state.owner_id")],
    },
    "keiko": {
        "integrations": [],
        "settings": [],
    },
}
