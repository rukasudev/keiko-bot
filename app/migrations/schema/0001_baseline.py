"""Every collection and index the bot used at v1."""

from app.migrations.helpers import index, one_per, ttl

STRUCTURE = {
    "audit": {
        "errors": [],
        "notifications_twitch": [],
        "reminders": [ttl("expires_at")],
    },
    "configs": {
        "admin": [],
        "data": [],
        "integrations": [],
    },
    "events": {
        "block_links": [],
        "default_roles": [],
        "notifications_twitch": [],
        "notifications_youtube_video": [],
        "reminders_birthday": [],
        "stream_elements_commands": [],
        "welcome_messages": [],
    },
    "guild": {
        "analytics_archives": [],
        "analytics_events": [
            ttl("ts", days=90),
            index("guild_id", "-ts"),
            index("event", "-ts"),
            index("event", "feature", "-ts"),
            index("session_id", "ts"),
        ],
        "analytics_guild_month": [
            ttl("updated_at", days=400),
            index("guild_id", "-month"),
        ],
        "analytics_guild_profile": [
            ttl("removed_at", days=365),
            index("-last_value_at"),
        ],
        "block_links": [one_per("guild_id")],
        "block_links_totals": [],
        "blocked_links": [
            ttl("created_at", days=90),
            index("guild_id", "-created_at"),
            index("guild_id", "user_id", "-created_at"),
        ],
        "default_roles": [one_per("guild_id")],
        "logs": [
            ttl("ts", days=30),
            index("level", "-ts"),
            index("guild_id", "-ts"),
            index("session_id", "ts"),
        ],
        "logs_files": [],
        "moderations": [one_per("guild_id")],
        "notifications_twitch": [one_per("guild_id")],
        "notifications_youtube_video": [one_per("guild_id")],
        "reminders_birthday": [one_per("guild_id")],
        "stream_elements_commands": [one_per("guild_id")],
        "welcome_messages": [one_per("guild_id")],
    },
    "notifications": {
        "notifications_twitch": [],
    },
    "reminders": {
        "birthdays": [],
    },
}
