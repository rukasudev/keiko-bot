"""
Generators para dados de notificacoes.

Uso:
    from tests.generators import notifications

    await notifications.twitch(
        db,
        guild_id="123",
        streamers=[
            {"name": "gaules", "channel_id": "456"},
            {"name": "loud_coringa", "channel_id": "789"}
        ]
    )
"""

from tests.generators.moderations import seed_cog
from typing import List, Dict, Optional
from datetime import datetime, timezone


async def twitch(
    db,
    guild_id: str,
    *,
    enabled: bool = True,
    streamers: List[Dict[str, str]] = None
) -> dict:
    """
    Insere configuracao de notifications_twitch.

    streamers: Lista de {"name": "...", "channel_id": "...", "messages": [...]}
    """
    if streamers is None:
        streamers = [{
            "name": "test_streamer",
            "channel_id": "100",
            "messages": ["{streamer} is live!", "Hey, {streamer} started streaming!"]
        }]

    notifications_values = []
    for s in streamers:
        notifications_values.append({
            "channel": {"value": s["channel_id"]},
            "streamer": {"value": s["name"]},
            "notification_messages": {
                "style": "bullet",
                "value": ";".join(s.get("messages", ["{streamer} is live!"]))
            }
        })

    doc = {
        "guild_id": guild_id,
        "enabled": enabled,
        "notifications": {
            "style": "composition",
            "values": notifications_values
        }
    }

    seed_cog(db, "notifications_twitch", doc)

    return doc


async def twitch_audit(
    db,
    streamer: str,
    *,
    last_stream_date: datetime = None
) -> dict:
    """
    Insere a subscription Twitch com a ultima live anunciada (para evitar duplicados).
    """
    doc = {
        "_id": f"twitch:{streamer.lower()}",
        "platform": "twitch",
        "account": streamer.lower(),
        "state": {"last_live_started_at": last_stream_date or datetime.now(timezone.utc)},
    }

    await _insert_or_sync(db.keiko.subscriptions, doc)

    return doc


async def twitch_notification_message(
    db,
    streamer: str,
    guild_id: str,
    channel_id: str,
    message_id: str
) -> dict:
    """
    Insere referencia de mensagem enviada (para edicao posterior).
    """
    doc = {
        "_id": f"{guild_id}:{channel_id}:notifications_twitch:{streamer.lower()}",
        "guild_id": guild_id,
        "channel_id": channel_id,
        "cog": "notifications_twitch",
        "subject": streamer.lower(),
        "message_id": message_id,
    }

    await _insert_or_sync(db.guild.messages, doc)

    return doc


async def youtube(
    db,
    guild_id: str,
    *,
    enabled: bool = True,
    youtubers: List[Dict[str, str]] = None
) -> dict:
    """
    Insere configuracao de notifications_youtube_video.

    youtubers: Lista de {"name": "...", "channel_id": "...", "messages": [...]}
    """
    if youtubers is None:
        youtubers = [{
            "name": "test_youtuber",
            "channel_id": "100",
            "youtube_channel_id": "UC12345",
            "messages": ["{youtuber} posted a new video!"]
        }]

    notifications_values = []
    for y in youtubers:
        notifications_values.append({
            "channel": {"value": y["channel_id"]},
            "youtuber": {"value": y["name"]},
            "youtube_channel_id": {"value": y.get("youtube_channel_id", "UC12345")},
            "notification_messages": {
                "style": "bullet",
                "value": ";".join(y.get("messages", ["{youtuber} posted a new video!"]))
            }
        })

    doc = {
        "guild_id": guild_id,
        "enabled": enabled,
        "notifications": {
            "style": "composition",
            "values": notifications_values
        }
    }

    seed_cog(db, "notifications_youtube_video", doc)

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


async def _delete_or_sync(collection, query):
    """Remove documentos de forma assincrona."""
    try:
        await collection.delete_many(query)
    except TypeError:
        collection.delete_many(query)


# ============================================================================
# CLEANUP
# ============================================================================

async def cleanup_twitch(db, streamer: str = None, guild_id: str = None):
    """Remove dados de twitch do banco de teste."""
    try:
        if guild_id:
            if hasattr(db, 'guild'):
                await db.guild.notifications_twitch.delete_many({"guild_id": guild_id})
            else:
                db.guild.notifications_twitch.delete_many({"guild_id": guild_id})

        if streamer:
            await _delete_or_sync(db.keiko.subscriptions, {"_id": f"twitch:{streamer.lower()}"})
            await _delete_or_sync(db.guild.messages, {"subject": streamer.lower()})
    except Exception:
        pass


async def cleanup_youtube(db, guild_id: str = None):
    """Remove dados de youtube do banco de teste."""
    try:
        if guild_id:
            if hasattr(db, 'guild'):
                await db.guild.notifications_youtube_video.delete_many({"guild_id": guild_id})
            else:
                db.guild.notifications_youtube_video.delete_many({"guild_id": guild_id})
    except Exception:
        pass
