import asyncio
import random
import re
from typing import Any, Dict, List, Optional

import discord

from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import Style as style_constants
from app.constants import ViewConstants as view_constants
from app.exceptions import ErrorContext
from app.integrations.stream_elements import StreamElementsClient
from app.settings import open_feature
from app.services import analytics, cache
from app.services.utils import ml
from app.views.pagination import PaginationView
from app.views.pagination_without_interaction import PaginationWithoutInteractionView


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    """The slash command: the setup form, or the manager of what is saved."""
    await open_feature(interaction, constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY)


async def check_message(guild_id: str, message: discord.Message, prefix: str) -> None:
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate,
        guild_id,
        constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
    )

    if not cogs:
        return

    streamer = cogs.get("streamer")
    if not streamer:
        return

    command = message.content.split(" ")[0].replace(prefix, "")
    channel_id = cogs.get("channel_id")

    context = ErrorContext.from_message(
        flow="stream_elements",
        message=message,
        command=command,
        streamer=streamer,
    )

    try:
        if command == "commands":
            view = await parse_command_list_view(channel_id, message, streamer)
            if not view:
                return
            return await view.send(message)

        reply = await asyncio.to_thread(
            get_reply_in_cache_or_populate, channel_id, command, message.author
        )
        if not reply:
            return

        await message.reply(embed=create_response_embed(command, reply, message.author, streamer))
        analytics.record_value(
            guild_id, constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY
        )
    except Exception as e:
        logger.error(
            f"Failed in stream_elements: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise

async def parse_command_list_view(
    channel_id: str, message: discord.Message, streamer: str
) -> Optional[discord.ui.View]:
    """The command list. The view is built here, on the loop; only the two
    lookups go to a thread."""
    from app import bot

    commands_list = await asyncio.to_thread(
        get_commands_in_cache_or_populate, channel_id, message.author
    )
    if not commands_list:
        return None

    locale = message.guild.preferred_locale if message.guild else None
    namespace = "commands.commands.commons.stream-elements-manager.commands"
    title = ml(f"{namespace}.embed.title", locale=locale)
    description = ml(f"{namespace}.embed.desc", locale=locale).replace(
        "$streamer", streamer
    )
    user_info = await asyncio.to_thread(bot.twitch.get_user_info, streamer)
    icon = (user_info or {}).get("profile_image_url")
    view = PaginationWithoutInteractionView(
        title,
        description,
        commands_list,
        message,
        thumbnail=icon,
        sep=view_constants.COMMANDS_PAGE_SIZE,
    )
    return view


async def send_commands_view(interaction: discord.Interaction) -> None:
    """The paginated command list, opened from the manager panel."""
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate,
        interaction.guild.id,
        constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
    )
    streamer = str((cogs or {}).get("streamer", ""))
    commands_list = await asyncio.to_thread(
        get_commands_in_cache_or_populate,
        str((cogs or {}).get("channel_id", "")),
        interaction.user,
    )
    locale = interaction.locale
    namespace = "commands.commands.commons.stream-elements-manager.commands"
    if not commands_list:
        empty = ml(f"{namespace}.empty", locale=locale).replace("$streamer", streamer)
        return await interaction.response.send_message(empty, ephemeral=True)

    view = PaginationView(
        interaction,
        ml(f"{namespace}.embed.title", locale=locale),
        ml(f"{namespace}.embed.desc", locale=locale).replace("$streamer", streamer),
        commands_list,
        sep=view_constants.COMMANDS_PAGE_SIZE,
    )
    await view.send(ephemeral=True)


def create_response_embed(command: str, reply: str, user: discord.User, streamer: str) -> discord.Embed:
    embed = discord.Embed(
        description=reply,
        color=(int(style_constants.BACKGROUND_COLOR, base=16)),
    )
    embed.set_author(name=f"!{command}", icon_url=user.avatar.url)
    embed.set_footer(text=f"• See all {streamer}'s StreamElements commands with {bot.config.PREFIX}commands")

    return embed

def parse_placeholders(reply: str, user: discord.User) -> str:
    if "$(touser)" in reply:
        reply = reply.replace("$(touser)", user.mention)

    if "${touser}" in reply:
        reply = reply.replace("${touser}", user.mention)

    if "$(count)" in reply:
        reply = reply.replace("$(count)", "X")

    if "${count}" in reply:
        reply = reply.replace("${count}", "X")

    if "$(random" in reply:
        random_match = re.search(r'\$\(random\.(\d+)-(\d+)\)', reply)
        if random_match:
            start = int(random_match.group(1))
            end = int(random_match.group(2))
            random_number = random.randint(start, end)
            reply = reply.replace(random_match.group(0), str(random_number))

    if "${random" in reply:
        random_match = re.search(r'\$\{random\.(\d+)-(\d+)\}', reply)
        if random_match:
            start = int(random_match.group(1))
            end = int(random_match.group(2))
            random_number = random.randint(start, end)
            reply = reply.replace(random_match.group(0), str(random_number))

    if "$(count" in reply:
        count_match = re.search(r'\$\(count\.(\d+)\)', reply)
        if count_match:
            reply = reply.replace(count_match.group(0), "X")

    if "${count" in reply:
        count_match = re.search(r'\$\{count\.(\d+)\}', reply)
        if count_match:
            reply = reply.replace(count_match.group(0), "X")

    if "/me" in reply:
        reply = reply.replace("/me", f"{user.mention}")

    if "$(user)" in reply:
        reply = reply.replace("$(user)", user.mention)

    return reply


def get_commands_in_cache_or_populate(channel_id: str, user: discord.User) -> List[Dict[str, Any]]:
    cache_key = f"streamelements:commands:{channel_id}"

    data = cache.get_data_from_redis(cache_key)
    if data:
        return data

    channel_commands = StreamElementsClient.get_chat_commands(channel_id)
    if not channel_commands:
        return

    cache_batch = {}
    for channel_command in channel_commands:
        if not channel_command.get("enabled"):
            continue

        cache_batch[f"!{channel_command.get('command')}"] = parse_placeholders(channel_command.get("reply"), user)

    day_in_seconds = 60 * 60 * 24
    cache.set_data_in_redis_with_expiration(cache_key, cache_batch, day_in_seconds)

    return cache_batch

def get_reply_in_cache_or_populate(channel_id: str, command: str, user: discord.User) -> str:
    cache_key = f"streamelements:commands:{channel_id}"

    data = cache.get_data_from_redis(cache_key)
    if data.get(command):
        return data.get(command)

    message = None
    cache_batch = {}

    channel_commands = StreamElementsClient.get_chat_commands(channel_id)
    if not channel_commands:
        return

    for channel_command in channel_commands:
        if not channel_command.get("enabled"):
            continue

        reply = parse_placeholders(channel_command.get("reply"), user)

        if channel_command.get("command") == command and channel_command.get("enabled"):
            message = reply

        cache_batch[f"!{channel_command.get('command')}"] = reply

    day_in_seconds = 60 * 60 * 24
    cache.set_data_in_redis_with_expiration(cache_key, cache_batch, day_in_seconds)

    return message
