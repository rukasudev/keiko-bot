"""Copy each server's seven feature documents into guild.cogs, and Keiko's configs into
keiko.settings and keiko.integrations."""

from collections import Counter

from app.migrations.helpers import begin, insert_missing

COGS = (
    "block_links",
    "default_roles",
    "notifications_twitch",
    "notifications_youtube_video",
    "reminders_birthday",
    "stream_elements_commands",
    "welcome_messages",
)

BOOKKEEPING = ("_id", "guild_id", "enabled", "created_at", "updated_at")

STATE = {"stream_elements_commands": ("channel_id",)}

RENAMED = {
    "reminders_birthday": {
        "channel_id": "channel",
        "mention_everyone": "mention_everyone",
        "notification_time": "notification_time",
        "default_message": "default_message",
    }
}

DROPPED = {"reminders_birthday": ("timezone", "locale")}

PRESENCE = ("status", "activity", "description")

BOT_OPTIONS = ("prefix", "owner_id")

OPERATOR_PREFIX = "admin_"


def state_of(cog, document):
    """What Keiko learned on its own and kept in the feature document."""
    names = (*STATE.get(cog, ()), "schema_version")
    return {name: document[name] for name in names if name in document}


def options_of(cog, document):
    """What the server's form saved, under the keys the form names them."""
    if cog in RENAMED:
        return {
            renamed: document[name]
            for name, renamed in RENAMED[cog].items()
            if name in document
        }

    learned = state_of(cog, document)
    return {
        name: value
        for name, value in document.items()
        if name not in BOOKKEEPING and name not in learned
    }


def unmapped(cog, document):
    """The keys of a renamed feature's document this file neither copies nor drops on
    purpose: data that would vanish."""
    if cog not in RENAMED:
        return []

    known = {
        *BOOKKEEPING,
        *RENAMED[cog],
        *DROPPED.get(cog, ()),
        *state_of(cog, document),
    }
    return sorted(name for name in document if name not in known)


def cog_document(cog, document, flags):
    """The guild.cogs document of one feature document; one saved before `enabled`
    existed takes the server's old moderations flag."""
    guild_id = document["guild_id"]
    enabled = document["enabled"] if "enabled" in document else flags.get(cog)
    copied = {
        "_id": f"{guild_id}:{cog}",
        "guild_id": guild_id,
        "cog": cog,
        "enabled": bool(enabled),
        "options": options_of(cog, document),
        "state": state_of(cog, document),
    }
    for stamp in ("created_at", "updated_at"):
        if document.get(stamp) is not None:
            copied[stamp] = document[stamp]
    return copied


def keiko_document(bot, admin):
    """The keiko.settings document: the presence, a new server's prefix, the owner and
    the operator's server and channels."""
    options = {
        "presence": {name: bot[name] for name in PRESENCE if name in bot},
        **{name: bot[name] for name in BOT_OPTIONS if name in bot},
        "operator": {
            name: int(value)
            for name, value in admin.items()
            if name.startswith(OPERATOR_PREFIX)
        },
    }
    return {"_id": "keiko", "options": options}


def check(db):
    """Refuse a feature document whose server id is not text or that holds a key this
    file would drop, an operator id that is not a number, a second admin document and an
    integration with no name or a name used twice."""
    client = db.client
    problems = [
        f"guild.{cog} {document['_id']} has a guild_id that is not text:"
        f" {document.get('guild_id')!r}"
        for cog in COGS
        for document in client.guild[cog]
        .find({"guild_id": {"$not": {"$type": "string"}}}, {"guild_id": 1})
        .limit(5)
    ]

    admins = list(client.configs.admin.find({"admin_guild_id": {"$exists": True}}))
    if len(admins) > 1:
        problems.append(f"configs.admin holds {len(admins)} documents, expected one")
    problems += [
        f"configs.admin {name} is {value!r}, not a Discord id"
        for admin in admins[:1]
        for name, value in admin.items()
        if name.startswith(OPERATOR_PREFIX) and not _is_discord_id(value)
    ]

    problems += [
        f"guild.{cog} {document['_id']} holds {', '.join(unmapped(cog, document))},"
        " which this file would drop"
        for cog in RENAMED
        for document in client.guild[cog].find({})
        if unmapped(cog, document)
    ]

    problems += [
        f"configs.integrations {document['_id']} has no name"
        for document in client.configs.integrations.find(
            {"name": {"$not": {"$type": "string"}}}, {"_id": 1}
        )
    ]
    names = Counter(
        document["name"]
        for document in client.configs.integrations.find(
            {"name": {"$type": "string"}}, {"name": 1}
        )
    )
    problems += [
        f"configs.integrations holds {count} documents named {name!r}, expected one"
        for name, count in sorted(names.items())
        if count > 1
    ]
    return problems


def up(db):
    """Write the guild.cogs, keiko.settings and keiko.integrations documents that are
    missing."""
    with begin(db) as tx:
        flags = {
            server["guild_id"]: server
            for server in tx.guild.moderations.find(
                {"guild_id": {"$type": "string"}},
                {"guild_id": 1, **dict.fromkeys(COGS, 1)},
            )
        }

        documents = [
            cog_document(cog, document, flags.get(document["guild_id"], {}))
            for cog in COGS
            for document in tx.guild[cog].find({"guild_id": {"$type": "string"}})
        ]
        tx.count("cogs_copied", insert_missing(tx.guild.cogs, documents))

        keiko = tx.keiko.settings.find_one({"_id": "keiko"})
        bot = tx.configs.data.find_one({"status": {"$exists": True}})
        if keiko is None and bot is not None:
            admin = tx.configs.admin.find_one({"admin_guild_id": {"$exists": True}})
            tx.keiko.settings.insert_one(keiko_document(bot, admin or {}))
        tx.count("keiko_settings_created", keiko is None and bot is not None)

        integrations = [
            {"_id": document["name"], "options": dict(document.get("configs") or {})}
            for document in tx.configs.integrations.find({"name": {"$type": "string"}})
        ]
        tx.count(
            "integrations_copied", insert_missing(tx.keiko.integrations, integrations)
        )


def _is_discord_id(value):
    return isinstance(value, int) or (isinstance(value, str) and value.isdigit())
