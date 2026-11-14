"""The numbers the analytics sink derives, and the runs the trace sink keeps."""

from app.migrations.helpers import index, ttl

STRUCTURE = {
    "analytics": {
        "counts": [
            index("guild_id", "-month"),
            index("month"),
            ttl("updated_at", days=400),
        ],
        "lifetime": [
            index("removed_at", "last_value_at"),
            ttl("removed_at", days=365),
        ],
        "keiko": [],
    },
    "traces": {
        "runs": [
            ttl("ts", days=30),
            index("guild_id", "-ts"),
        ],
    },
}
