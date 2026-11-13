"""Give every server Keiko recorded its settings document: language, time zone and
prefix, and its owner and whether Keiko is there."""

import hashlib
from zoneinfo import available_timezones

from app.migrations.helpers import begin, insert_missing

DEFAULT_LANGUAGE = "pt-br"

LANGUAGES = ("en-us", "pt-br")

ZONES = {"pt-br": "America/Sao_Paulo", "en-us": "UTC"}

LANGUAGES_BY_GUILD = {
    "00047cb675d92cab": "en-us",
    "0021758d88844477": "pt-br",
    "063cebd8030b259d": "pt-br",
    "07017a7388385662": "en-us",
    "079231727f0e6c51": "en-us",
    "093f7d2dff4e366a": "en-us",
    "0b0f603357ab9d55": "en-us",
    "0bd2af785ff6df53": "pt-br",
    "0ce919c77878dd2a": "pt-br",
    "0cf20061c3cf8cff": "en-us",
    "143dd4a52d0daec7": "pt-br",
    "1485ba17e0fc78cd": "pt-br",
    "174dd5fe5e9041db": "pt-br",
    "2196b16ab308cc16": "pt-br",
    "21b24b85e87c6eec": "pt-br",
    "2232b022531e41d1": "en-us",
    "22401506f66fcef3": "pt-br",
    "29bf27087b04167e": "en-us",
    "2a064184da4ce4c3": "pt-br",
    "2a0c4354c97abff4": "en-us",
    "2a32d909eb4d0fe0": "pt-br",
    "2ed8016fc24d0b6d": "pt-br",
    "2edeb3fcfd3a6dbd": "en-us",
    "2ef5e537050b7a06": "pt-br",
    "2f7ecc67d52c5432": "pt-br",
    "2fae72edeebeb980": "pt-br",
    "300fb69b98d64bf9": "pt-br",
    "33369db1720b20d1": "en-us",
    "3501b349976f4721": "en-us",
    "369754fd76ce06e0": "pt-br",
    "38aa3c7b01b1bb4e": "pt-br",
    "3991617584faf337": "pt-br",
    "3b38051e609d943d": "pt-br",
    "3b58434cd6df76dd": "pt-br",
    "3c0095a13983efc3": "en-us",
    "3dfb2eba105b046c": "en-us",
    "46c01347e6890bdb": "pt-br",
    "488a1662fcef75a1": "pt-br",
    "49abfa59ba19ca7d": "en-us",
    "4b31d518577a04eb": "pt-br",
    "4cce0c837f4c923f": "pt-br",
    "4d4e4571d01a75de": "en-us",
    "4f9ece23f1c0f5c5": "en-us",
    "4fbce9e5d971936e": "pt-br",
    "4fc7b57560c9bd62": "pt-br",
    "504db954d9311144": "en-us",
    "527ac119604dfca1": "pt-br",
    "53814c245361832d": "en-us",
    "538dfcad11186a55": "pt-br",
    "54363bd50b8ba3b0": "en-us",
    "548a32a4187df69f": "en-us",
    "56cdfd9b6f4f9cf8": "pt-br",
    "57ba995bf9a3115e": "pt-br",
    "581c8785a4745ced": "pt-br",
    "5e55915c962530cc": "pt-br",
    "66f39d2ec1c1761f": "en-us",
    "681bbab5a6848f90": "pt-br",
    "68defa7332ae571f": "en-us",
    "69b02af3acea9dec": "pt-br",
    "6b579e753bd947c0": "pt-br",
    "6cbb8c05c756c679": "pt-br",
    "6cc96bd3f48e66bf": "pt-br",
    "719c57b8bcdfd476": "en-us",
    "736d7ab75ee37bd5": "pt-br",
    "77b526464aa761c6": "en-us",
    "7820bbe532886518": "pt-br",
    "78784d1539440ba3": "en-us",
    "7abdbc53b81757ca": "pt-br",
    "7ad9e16a2d301764": "en-us",
    "7ed644cb301fa0a1": "en-us",
    "7f98f717fd74be40": "pt-br",
    "814b96a43e75ec5c": "pt-br",
    "827ec06f1e067609": "pt-br",
    "8312b2e79d0c9c69": "en-us",
    "84aa3a8f276fb775": "en-us",
    "85ead188e938f99e": "pt-br",
    "8655dcceddf01a79": "pt-br",
    "877bfbadbf156ce9": "en-us",
    "8793c01cec2782ae": "en-us",
    "8d2c40cc63b273bf": "pt-br",
    "8f59276db776d24f": "pt-br",
    "90ccc0fa71243cd5": "en-us",
    "90ced82c3ceeb413": "pt-br",
    "93123ccad3bb4ab0": "en-us",
    "98431d747211a5eb": "en-us",
    "9a2f536d9dba8168": "pt-br",
    "9b76aa024ff642ee": "pt-br",
    "9dcace69de17f92d": "pt-br",
    "a06916467b194939": "pt-br",
    "a19c000eb29dc41d": "en-us",
    "a328f6071ba0b8c4": "en-us",
    "a398d8e01383dd26": "en-us",
    "a66c25866547f81e": "pt-br",
    "a830c4947fa09794": "pt-br",
    "aa4a2b02184a1fe2": "en-us",
    "ace5e61c880fccd5": "pt-br",
    "b3904288e85369eb": "en-us",
    "b4917179e6cd8115": "en-us",
    "b5a6553865a8bd5a": "pt-br",
    "b6d218b14b5506ae": "pt-br",
    "b7e3de6846c6b01b": "pt-br",
    "b8a53ed0b32e3812": "en-us",
    "b9d362d730ab572f": "pt-br",
    "bb491152257af78b": "pt-br",
    "bcf0e72299808aa3": "pt-br",
    "bd654e5ab72d8eee": "en-us",
    "be742c2b0c5e43c7": "en-us",
    "bedb454bdf1fe624": "en-us",
    "c0294a054b7bd1fc": "en-us",
    "c0ef936317420993": "en-us",
    "c2ca8028c145e785": "en-us",
    "c362d8e49f845949": "en-us",
    "c71b9bc20b0ceba0": "pt-br",
    "c76de5c720901a70": "pt-br",
    "c770da18bd578c37": "pt-br",
    "c9643d63588c432e": "en-us",
    "cd412b4c6a7e0bdf": "en-us",
    "cd50204c21e42bc1": "pt-br",
    "cdeb68c533f4f4ad": "en-us",
    "ce8068a8e0cffd01": "pt-br",
    "cff2fa15e64bfeca": "en-us",
    "d1798fe00000ee0a": "pt-br",
    "d3c3780f97a11660": "pt-br",
    "d776dc30ce800715": "pt-br",
    "d8c02d151d04ae94": "pt-br",
    "d91b0b8cab0f09db": "en-us",
    "da494f3c374cd906": "en-us",
    "e33fa52737a8eb8d": "pt-br",
    "e63d6b46848fddc7": "en-us",
    "e9696bd3ed065d07": "en-us",
    "f05689cfc8a88032": "pt-br",
    "f25945b7c54a408c": "pt-br",
    "f2d353d1514f2df5": "en-us",
    "f41a8c57523c5d93": "en-us",
    "f6b3c1b8c60113ea": "en-us",
    "f6b8c7b155dfa70d": "en-us",
    "fb860a6d83b66289": "pt-br",
}


def settings_for(guild_id, birthday, prefix):
    """The settings a server starts with: the language the table keeps under the hash
    of its id, and the zone of its birthday config."""
    guild_key = hashlib.sha256(guild_id.encode("utf-8")).hexdigest()[:16]
    language = LANGUAGES_BY_GUILD.get(guild_key, DEFAULT_LANGUAGE)
    timezone = birthday.get("timezone")
    if not isinstance(timezone, str) or not timezone:
        timezone = ZONES[language]
    return {
        "language": language,
        "timezone": timezone,
        "prefix": prefix,
    }


def options_for(server, birthday, prefix, zones):
    """The settings the server saved, and the guessed ones for those it lacks."""
    saved = server.get("settings") or {}
    guessed = settings_for(server["guild_id"], birthday, prefix)
    kept = {
        "language": saved.get("language") in LANGUAGES,
        "timezone": saved.get("timezone") in zones,
        "prefix": isinstance(saved.get("prefix"), str) and bool(saved.get("prefix")),
    }
    return {key: saved[key] if kept[key] else guessed[key] for key in guessed}


def settings_document(server, birthday, prefix, zones):
    """The guild.settings document of one guild.moderations document."""
    present = server.get("is_bot_online") is not False
    document = {
        "_id": server["guild_id"],
        "options": options_for(server, birthday, prefix, zones),
        "state": {
            "owner_id": server.get("owner_id"),
            "present": present,
            "joined_at": server.get("created_at"),
            "left_at": None if present else server.get("updated_at"),
        },
    }
    for stamp in ("created_at", "updated_at"):
        if server.get(stamp) is not None:
            document[stamp] = server[stamp]
    return document


def check(db):
    """Refuse a server whose id is not text, settings that are not an object, a
    birthday zone zoneinfo lacks, and servers to write with no prefix to give them."""
    guild = db.client.guild
    problems = [
        f"guild.moderations {document['_id']} has a guild_id that is not text:"
        f" {document.get('guild_id')!r}"
        for document in guild.moderations.find(
            {"guild_id": {"$not": {"$type": "string"}}}, {"guild_id": 1}
        ).limit(5)
    ]
    problems += [
        f"guild.moderations {document.get('guild_id')!r} has settings that are not"
        " an object"
        for document in guild.moderations.find(
            {"settings": {"$exists": True, "$not": {"$type": "object"}}},
            {"guild_id": 1},
        ).limit(5)
    ]
    zones = available_timezones()
    problems += [
        f"guild.reminders_birthday {document['guild_id']!r} has the time zone"
        f" {document['timezone']!r}, which zoneinfo does not know"
        for document in guild.reminders_birthday.find(
            {"timezone": {"$type": "string", "$ne": ""}}, {"guild_id": 1, "timezone": 1}
        )
        if document["timezone"] not in zones
    ]
    if guild.moderations.count_documents({}) and not _prefix(db.client):
        problems.append("configs.data has no prefix to give the servers")
    return problems


def up(db):
    """Write the settings document of every recorded server that has none."""
    with begin(db) as tx:
        guild = tx.guild
        servers = list(guild.moderations.find({"guild_id": {"$type": "string"}}))
        birthdays = {
            document["guild_id"]: document
            for document in guild.reminders_birthday.find(
                {"guild_id": {"$in": [server["guild_id"] for server in servers]}},
                {"guild_id": 1, "timezone": 1},
            )
        }

        prefix = _prefix(tx)
        if servers and prefix is None:
            raise ValueError("configs.data has no prefix to give the servers")

        zones = available_timezones()
        documents = [
            settings_document(
                server, birthdays.get(server["guild_id"], {}), prefix, zones
            )
            for server in servers
        ]
        tx.count("settings_created", insert_missing(guild.settings, documents))


def _prefix(client):
    bot = client.configs.data.find_one({"status": {"$exists": True}}, {"prefix": 1})
    prefix = (bot or {}).get("prefix")
    return prefix if isinstance(prefix, str) and prefix else None
