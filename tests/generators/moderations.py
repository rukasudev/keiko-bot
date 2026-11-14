"""
Generators para dados de moderacao no banco.

Uso:
    from tests.generators import moderations

    # Cria welcome_messages com valores padrao
    await moderations.welcome_messages(db, guild_id="123")

    # Cria com valores customizados
    await moderations.welcome_messages(
        db,
        guild_id="123",
        channel_id="456",
        enabled=False,
        messages=["Custom message {user}!"]
    )
"""

from typing import List, Optional, Any

from app.constants import DBConfigs
from app.core.cache import forget_remembered


async def welcome_messages(
    db,
    guild_id: str,
    *,
    enabled: bool = True,
    channel_id: str = "100",
    title: str = "Welcome!",
    messages: List[str] = None,
    footer: str = "Enjoy your stay!"
) -> dict:
    """
    Insere configuracao de welcome_messages no banco.

    Retorna o documento inserido para assertions.
    """
    if messages is None:
        messages = [
            "Welcome {user} to {server}!",
            "Hey {user}, glad to have you!"
        ]

    doc = {
        "guild_id": guild_id,
        "enabled": enabled,
        "welcome_messages_channel": {
            "style": "channel",
            "values": channel_id
        },
        "welcome_messages_title": title,
        "welcome_messages": {
            "style": "bullet",
            "values": messages
        },
        "welcome_messages_footer": footer
    }

    seed_cog(db, "welcome_messages", doc)

    return doc


async def block_links(
    db,
    guild_id: str,
    *,
    enabled: bool = True,
    allowed_roles: List[str] = None,
    allowed_channels: List[str] = None,
    allowed_links: List[str] = None,
    answer: str = "Links are not allowed here!"
) -> dict:
    """
    Insere configuracao de block_links no banco.
    """
    # Runtime shape (card-first redesign): mode + envelopes + composition.
    doc = {
        "guild_id": guild_id,
        "enabled": enabled,
        "mode": "block_all",
        "allowed_roles": {"style": "role", "values": allowed_roles or []},
        "allowed_chats": {"style": "channel", "values": allowed_channels or []},
        "allowed_links": {
            "style": "bullet",
            "values": allowed_links or ["discord.gg", "youtube.com"],
        },
        "custom_links": {"style": "composition", "values": []},
        "answer": answer,
    }

    seed_cog(db, "block_links", doc)

    return doc


async def default_roles(
    db,
    guild_id: str,
    *,
    enabled: bool = True,
    user_roles: List[str] = None,
    bot_roles: List[str] = None
) -> dict:
    """
    Insere configuracao de default_roles no banco.
    """
    doc = {
        "guild_id": guild_id,
        "enabled": enabled,
        "default_roles": {
            "values": user_roles or []
        },
        "default_roles_bot": {
            "values": bot_roles or []
        }
    }

    seed_cog(db, "default_roles", doc)

    return doc


SERVER_SETTINGS = {
    "language": "pt-br",
    "timezone": "America/Sao_Paulo",
    "prefix": "ks!",
}


def recorded_server(db, guild_id, **settings) -> dict:
    """A server Keiko recorded, as the join and the migration leave it: its
    guild.settings document, its settings memo forgotten as every save does; what was
    already saved stays unless named here."""
    existing = db.guild.settings.find_one({"_id": str(guild_id)}) or {}
    saved = {**SERVER_SETTINGS, **(existing.get("options") or {}), **settings}
    db.guild.settings.update_one(
        {"_id": str(guild_id)},
        {"$set": {"options": saved}, "$setOnInsert": {"state": {"present": True}}},
        upsert=True,
    )
    forget_remembered(DBConfigs.SETTINGS_MEMO_KEY.format(guild_id=guild_id))
    return saved


def stored_cog(cog, document) -> dict:
    """The guild.cogs document of a flat feature document, as the data layer stores it."""
    guild_id = str(document["guild_id"])
    stored = {
        "_id": f"{guild_id}:{cog}",
        "guild_id": guild_id,
        "cog": cog,
        "options": {
            key: value
            for key, value in document.items()
            if key not in ("_id", "guild_id", "enabled", "state", "created_at", "updated_at")
        },
        "state": dict(document.get("state") or {}),
    }
    if "enabled" in document:
        stored["enabled"] = document["enabled"]
    return stored


def seed_cog(db, cog, document) -> dict:
    """Save a server's feature document, given flat as the app reads it."""
    stored = stored_cog(cog, document)
    db.guild.cogs.delete_one({"_id": stored["_id"]})
    db.guild.cogs.insert_one(stored)
    return document


# ============================================================================
# HELPERS
# ============================================================================

async def _insert_or_sync(collection, doc):
    """Insere documento de forma assincrona."""
    try:
        await collection.insert_one(doc)
    except TypeError:
        # Fallback para mock sincrono
        collection.insert_one(doc)


# ============================================================================
# CLEANUP
# ============================================================================

async def cleanup_guild(db, guild_id: str):
    """Remove todos os dados de um guild de teste."""
    db.guild.cogs.delete_many({"guild_id": guild_id})
    db.guild.settings.delete_many({"_id": guild_id})
