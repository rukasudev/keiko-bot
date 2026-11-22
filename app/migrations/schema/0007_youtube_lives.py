"""The servers that follow a YouTube channel's lives, and every message posted about
one subject."""

from app.migrations.helpers import index

STRUCTURE = {
    "guild": {
        "cogs": [
            index(
                "options.notifications.values.youtuber.value",
                partial={"cog": "notifications_youtube_live"},
                name="options.notifications.values.youtuber.value_1_live",
            ),
        ],
        "messages": [index("cog", "subject")],
    },
}
