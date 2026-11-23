"""The messages Keiko posted in a channel and keeps in `guild.messages`, by command and
subject, to edit or delete them later."""

from typing import Any

import discord

from app.data import data


async def posted_message(
    key: str, guild: Any, channel: Any, subject: str
) -> discord.Message | None:
    """The message the command `key` posted about `subject` in a channel, None once
    gone."""
    saved = await data().guild_messages.find_message(guild.id, channel.id, key, subject)
    if not saved:
        return None

    try:
        message: discord.Message | None = await channel.fetch_message(
            saved["message_id"]
        )
    except (discord.NotFound, discord.Forbidden):
        return None
    return message


async def delete_posted(key: str, guild: Any, channel_id: Any, subject: str) -> None:
    """Delete the message the command `key` posted about `subject` in a channel, and
    forget it, the channel gone or not."""
    channel = guild.get_channel(int(channel_id)) if str(channel_id).isdigit() else None
    message = await posted_message(key, guild, channel, subject) if channel else None
    if message is not None:
        try:
            await message.delete()
        except discord.NotFound:
            pass

    await data().guild_messages.delete_message(guild.id, channel_id, key, subject)
