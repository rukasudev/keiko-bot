"""Give each saved notice and birthday config the mention it pinged, never one more."""

import re

from app.migrations.helpers import begin

NOTICE_COGS = ("notifications_twitch", "notifications_youtube_video")
BIRTHDAY_COG = "reminders_birthday"
LANGUAGES = ("en-us", "pt-br")
DEFAULT_LANGUAGE = "pt-br"
LEADING = re.compile(r"^\s*(?P<mention>@everyone|@here|(?:<@&\d+>[\s,]*)+)[!?.,:;]*\s*")
ANY_MENTION = re.compile(r"@everyone|@here|<@[&!]?\d+>")
ROLE = re.compile(r"<@&(\d+)>")
LABELS = {
    "en-us": {
        "none": "Nobody",
        "everyone": "@everyone",
        "here": "@here",
        "roles": "Some roles",
    },
    "pt-br": {
        "none": "Ninguém",
        "everyone": "@everyone",
        "here": "@here",
        "roles": "Alguns cargos",
    },
}
TITLES = {
    "en-us": {"mention": "Mention", "mention_roles": "Roles I mention"},
    "pt-br": {"mention": "Mencionar", "mention_roles": "Cargos que eu menciono"},
}
UNMIGRATED_NOTICES = {
    "cog": {"$in": list(NOTICE_COGS)},
    "options.notifications.values": {"$elemMatch": {"mention": {"$exists": False}}},
}


def check(db):
    """Refuse a notice whose messages are neither absent, a text nor a list of texts."""
    return [
        unsplittable(document, index)
        for document in db.client.guild.cogs.find({"cog": {"$in": list(NOTICE_COGS)}})
        for index, item in enumerate(_items(document))
        if not splittable(_messages(item))
    ]


def up(db):
    """Write each notice's and birthday config's mention once, a notice's taken out of
    the messages it led when they all led with it."""
    with begin(db) as tx:
        for document in tx.guild.cogs.find(UNMIGRATED_NOTICES):
            items = _items(document)
            for index, item in enumerate(items):
                if "mention" not in item and not splittable(_messages(item)):
                    raise ValueError(unsplittable(document, index))

            language = _language(tx, document.get("guild_id"))
            moved = [notice_item(item, language) for item in items]
            tx.guild.cogs.update_one(
                {"_id": document["_id"]},
                {"$set": {"options.notifications.values": [item for item, _ in moved]}},
            )
            for _item, counts in moved:
                for name, amount in counts.items():
                    tx.count(name, amount)
                if counts.get("notices_mixed"):
                    tx.note("notices_mixed", document.get("guild_id"))

        for document in tx.guild.cogs.find(
            {"cog": BIRTHDAY_COG, "options.mention": {"$exists": False}}
        ):
            options, counts = birthday_options(document.get("options") or {})
            tx.guild.cogs.update_one(
                {"_id": document["_id"]},
                {"$set": {f"options.{key}": value for key, value in options.items()}},
            )
            for name, amount in counts.items():
                tx.count(name, amount)

        for member in tx.guild.members.find(
            {f"options.{BIRTHDAY_COG}.message.mode": "custom"}
        ):
            message = member["options"][BIRTHDAY_COG].get("message") or {}
            texts = (message.get("title"), message.get("content"))
            tx.count(
                "birthday_member_texts_with_mention",
                any(ANY_MENTION.search(text or "") for text in texts),
            )


def leading_mention(text):
    """The mention a text opens with, as (kind, roles, the rest), or None."""
    found = LEADING.match(text or "")
    if not found:
        return None

    written = found.group("mention")
    if written == "@everyone":
        return "everyone", (), text[found.end() :]
    if written == "@here":
        return "here", (), text[found.end() :]
    return "roles", tuple(ROLE.findall(written)), text[found.end() :]


def moved_mention(texts):
    """The mention every text opens with and the texts without it; texts that do not
    all open with the same one stay as they are, with none, and say they were mixed."""
    found = [leading_mention(text) for text in texts] or [None]
    opened = {mention and (mention[0], frozenset(mention[1])) for mention in found}
    if len(opened) > 1:
        return "none", (), list(texts), True
    if found[0] is None:
        return "none", (), list(texts), False

    kind, roles, _rest = found[0]
    return kind, roles, [mention[2] for mention in found], False


def notice_item(item, language):
    """A notice with its mention field, its messages without the leading mention, and
    what the move counted; one that has its field already stays as it is."""
    if "mention" in item:
        return dict(item), {}

    stored = _messages(item)
    if stored is None:
        texts = []
    else:
        texts = stored if isinstance(stored, list) else stored.split(";")
    kind, roles, rest, mixed = moved_mention(texts)
    noticed = {**item, **mention_entries(kind, roles, language)}

    if stored is not None:
        noticed["notification_messages"] = _rewritten(item, stored, rest)
    counts = {
        "notices_moved": kind != "none",
        "notices_without_mention": kind == "none" and not mixed,
        "notices_mixed": mixed,
        "messages_left_empty": sum(
            not text.strip() and bool(original.strip())
            for text, original in zip(rest, texts)
        ),
        "messages_mid_text_unpinged": 0
        if mixed
        else sum(bool(unpinged(text, kind, roles)) for text in rest),
    }
    return noticed, counts


def unpinged(text, kind, roles):
    """The mentions left inside a text that stop pinging, since a notice now pings
    only what its field names."""
    still = {"everyone": ("@everyone", "@here"), "here": ("@here",)}.get(kind, ())
    return [
        written
        for written in ANY_MENTION.findall(text or "")
        if written not in still
        and not (
            kind == "roles" and written.startswith("<@&") and written[3:-1] in roles
        )
    ]


def splittable(stored):
    """Whether a notice's messages are absent, a text or a list of texts."""
    return (
        stored is None
        or isinstance(stored, str)
        or (isinstance(stored, list) and all(isinstance(text, str) for text in stored))
    )


def unsplittable(document, index):
    """The problem a notice whose messages cannot be split is reported as."""
    return (
        f"guild.cogs {document['_id']}: notice {index} has messages "
        "that are neither absent, a text nor a list of texts"
    )


def mention_entries(kind, roles, language):
    """The entries a notice's card saves for its mention, in the server's language."""
    entries = {
        "mention": {
            "value": LABELS[language][kind],
            "title": TITLES[language]["mention"],
            "_raw_value": kind,
        }
    }
    if roles:
        entries["mention_roles"] = {
            "value": roles[0] if len(roles) == 1 else list(roles),
            "title": TITLES[language]["mention_roles"],
            "style": "role",
        }
    return entries


def birthday_options(options):
    """The mention a birthday config gets from its old switch, and what it counted."""
    kind = "everyone" if options.get("mention_everyone") is True else "none"
    message = options.get("default_message") or {}
    texts = (
        (message.get("title"), message.get("content"))
        if message.get("mode") == "custom"
        else ()
    )
    counts = {
        "birthday_configs": 1,
        "birthday_texts_with_mention_unchanged": any(
            ANY_MENTION.search(text or "") for text in texts
        ),
    }
    return {"mention": kind, "mention_roles": []}, counts


UNKNOWN = object()


def _messages(item):
    """A notice's messages as the bot reads them (`item_values`): its entry's
    `_raw_value` or `value`, a bare text or list as it is, None when it has none."""
    entry = item.get("notification_messages")
    if not isinstance(entry, dict):
        return entry
    if "_raw_value" in entry:
        return entry["_raw_value"]
    return entry["value"] if "value" in entry else UNKNOWN


def _rewritten(item, stored, rest):
    entry = item.get("notification_messages")
    written = rest if isinstance(stored, list) else ";".join(rest)
    if not isinstance(entry, dict):
        return written
    rewritten = {**entry, "value": written}
    if "_raw_value" in entry:
        rewritten["_raw_value"] = written
    return rewritten


def _items(document):
    notifications = (document.get("options") or {}).get("notifications") or {}
    return list(notifications.get("values") or [])


def _language(tx, guild_id):
    settings = tx.guild.settings.find_one({"_id": str(guild_id)}) or {}
    language = (settings.get("options") or {}).get("language")
    return language if language in LANGUAGES else DEFAULT_LANGUAGE
