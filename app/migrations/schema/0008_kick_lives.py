"""The servers that follow a Kick streamer's lives."""

from app.migrations.helpers import index

STRUCTURE = {
    "guild": {
        "cogs": [
            index(
                "options.notifications.values.streamer.value",
                partial={"cog": "notifications_kick"},
                name="options.notifications.values.streamer.value_1_kick",
            ),
        ],
    },
}
