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
from datetime import datetime, timezone


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

    # Suporta tanto mock quanto motor real
    if hasattr(db, 'guild'):
        await _insert_or_sync(db.guild.welcome_messages, doc)
    else:
        db.guild.welcome_messages.insert_one(doc)

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
    doc = {
        "guild_id": guild_id,
        "enabled": enabled,
        "block_links_allowed_roles": {
            "values": allowed_roles or []
        },
        "block_links_allowed_chats": {
            "values": allowed_channels or []
        },
        "block_links_allowed_links": {
            "values": allowed_links or ["discord.gg", "youtube.com"]
        },
        "block_links_answer": answer
    }

    if hasattr(db, 'guild'):
        await _insert_or_sync(db.guild.block_links, doc)
    else:
        db.guild.block_links.insert_one(doc)

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

    if hasattr(db, 'guild'):
        await _insert_or_sync(db.guild.default_roles, doc)
    else:
        db.guild.default_roles.insert_one(doc)

    return doc


async def moderations_config(
    db,
    guild_id: str,
    *,
    pause_all: bool = False
) -> dict:
    """
    Insere configuracao de moderacoes no banco.
    """
    doc = {
        "guild_id": guild_id,
        "pause_all": pause_all,
        "created_at": datetime.now(timezone.utc).isoformat()
    }

    if hasattr(db, 'guild'):
        await _insert_or_sync(db.guild.moderations, doc)
    else:
        db.guild.moderations.insert_one(doc)

    return doc


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
    collections = [
        "welcome_messages",
        "block_links",
        "default_roles",
        "notifications_twitch",
        "notifications_youtube_video",
        "moderations"
    ]

    for collection_name in collections:
        try:
            if hasattr(db, 'guild'):
                await db.guild[collection_name].delete_many({"guild_id": guild_id})
            else:
                db.guild[collection_name].delete_many({"guild_id": guild_id})
        except Exception:
            pass
