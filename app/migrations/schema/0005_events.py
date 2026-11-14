"""The three event collections: changes kept for good, activity and interactions for 90
days."""

from app.migrations.helpers import index, ttl

STRUCTURE = {
    "events": {
        "changes": [
            index("guild_id", "-ts"),
            index("guild_id", "cog", "-ts"),
            index("guild_id", "cog", "type", "-ts"),
        ],
        "activity": [
            ttl("ts", days=90),
            index("guild_id", "-ts"),
            index("guild_id", "cog", "-ts"),
            index("guild_id", "cog", "actor.id", "-ts"),
            index("type", "-ts"),
        ],
        "interactions": [
            ttl("ts", days=90),
            index("guild_id", "-ts"),
            index("type", "-ts"),
            index("type", "cog", "-ts"),
            index("session_id", "ts"),
        ],
    },
}
