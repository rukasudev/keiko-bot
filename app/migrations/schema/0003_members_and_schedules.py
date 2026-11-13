"""The members of each server, and what Keiko set on the reminders API for a time."""

from app.migrations.helpers import index, ttl

STRUCTURE = {
    "guild": {
        "members": [index("guild_id", "options.reminders_birthday.date")],
    },
    "keiko": {
        "schedules": [
            index("kind", "due_at"),
            index("reminder_id"),
            ttl("expires_at"),
        ],
    },
}
