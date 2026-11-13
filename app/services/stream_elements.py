import asyncio
import random
import re
from typing import Dict, Optional

import discord

from app import logger
from app.constants import Commands as constants
from app.constants import LogTypes as logconstants
from app.constants import Style as style_constants
from app.constants import ViewConstants as view_constants
from app.components.embed import base_embed, response_error_embed
from app.data.guild import cogs as guild_cogs
from app.exceptions import ErrorContext, StreamElementsUnavailable
from app.integrations.stream_elements import StreamElementsClient
from app.settings import open_feature
from app.core import analytics, cache
from app.core.servers import server_language
from app.core.trace import outside_any_trace
from app.core.utils import fill, ml
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

    context = ErrorContext.from_message(
        flow="stream_elements",
        message=message,
        command_length=len(command),
        streamer=streamer,
    )

    try:
        locale = await server_language(guild_id)
        channel_id = await channel_of(
            guild_id, streamer, (cogs.get("state") or {}).get("channel_id")
        )

        if command == "commands":
            view = await parse_command_list_view(channel_id, message, streamer, locale)
            if not view:
                return
            return await view.send(message)

        reply = await asyncio.to_thread(
            get_reply_in_cache_or_populate, channel_id, command, message.author
        )
        if not reply:
            return

        await message.reply(
            embed=create_response_embed(
                command, reply, message.author, streamer, prefix, locale
            )
        )
        analytics.record_value(
            guild_id, constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY
        )
    except StreamElementsUnavailable as unavailable:
        await asyncio.to_thread(warn_unavailable, streamer, unavailable)
        if command == "commands":
            await message.reply(
                embed=response_error_embed("stream-elements-unavailable", locale)
            )
    except Exception as e:
        logger.error(
            f"Failed in stream_elements: {type(e).__name__}: {e}",
            log_type=logconstants.COMMAND_ERROR_TYPE,
            context=context,
            exc_info=True,
        )
        raise


async def channel_of(guild_id: str, streamer: str, channel_id: Optional[str]) -> str:
    """The streamer's StreamElements channel id; one saved without it is found and kept."""
    if channel_id:
        return str(channel_id)

    found = await asyncio.to_thread(_channel_or_recent_failure, streamer)
    await keep_channel(guild_id, found)
    return found


async def keep_channel(guild_id: str, channel_id: str) -> None:
    """Keep the server's StreamElements channel id as what Keiko learned, then drop its
    cache."""
    key = constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY
    await guild_cogs.set_cog_state_async(guild_id, key, {"channel_id": channel_id})
    await asyncio.to_thread(cache.remove_cog_cache_by_guild, guild_id, key)


def _channel_or_recent_failure(streamer: str) -> str:
    failed_key = constants.REDIS_STREAM_ELEMENTS_CHANNEL_FAILED.format(streamer=streamer)
    failed = cache.get_data_from_redis(failed_key)
    if failed:
        raise StreamElementsUnavailable(str(failed.get("error")))

    try:
        return find_channel(streamer)
    except StreamElementsUnavailable as unavailable:
        cache.set_data_in_redis_with_expiration(
            failed_key,
            {"error": str(unavailable)},
            constants.STREAM_ELEMENTS_CHANNEL_FAILED_SECONDS,
        )
        raise


def find_channel(streamer: str) -> str:
    """The StreamElements channel id of a streamer's username."""
    try:
        info = StreamElementsClient.get_channel_info(streamer)
    except Exception as error:
        raise StreamElementsUnavailable(
            f"channel of {streamer}: {type(error).__name__}: {error}"
        ) from error

    channel_id = info.get("_id") if isinstance(info, dict) else None
    if not channel_id:
        raise StreamElementsUnavailable(f"no channel for {streamer}: {_status(info)}")
    return str(channel_id)


def warn_unavailable(streamer: str, error: StreamElementsUnavailable) -> None:
    """Tell the log channel StreamElements failed, once per streamer while its Redis mark lives."""
    claimed = cache.claim_redis_key(
        constants.REDIS_STREAM_ELEMENTS_WARNED.format(streamer=streamer),
        constants.STREAM_ELEMENTS_FAILURE_SECONDS,
    )
    if not claimed:
        return

    with outside_any_trace():
        logger.warn(
            f"StreamElements unavailable for {streamer}: {error}",
            log_type=logconstants.COMMAND_WARN_TYPE,
        )


def _status(answer: object) -> str:
    if isinstance(answer, dict):
        return f"answered {answer.get('statusCode')} {answer.get('error') or ''}".strip()
    return f"answered {type(answer).__name__}"


async def parse_command_list_view(
    channel_id: str, message: discord.Message, streamer: str, locale: str
) -> Optional[discord.ui.View]:
    """The command list in `locale`. The view is built here, on the loop; only the two
    lookups go to a thread."""
    from app import bot

    commands_list = await asyncio.to_thread(
        get_commands_in_cache_or_populate, channel_id, message.author
    )
    if not commands_list:
        return None

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
        locale=locale,
    )
    return view


async def send_commands_view(interaction: discord.Interaction) -> None:
    """The paginated command list, opened from the manager panel."""
    guild_id = str(interaction.guild.id)
    cogs = await asyncio.to_thread(
        cache.get_cog_data_or_populate,
        guild_id,
        constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
        manager=True,
    )
    streamer = str((cogs or {}).get("streamer", ""))
    locale = interaction.locale
    try:
        channel_id = await channel_of(guild_id, streamer, (cogs or {}).get("channel_id"))
        commands_list = await asyncio.to_thread(
            get_commands_in_cache_or_populate, channel_id, interaction.user
        )
    except StreamElementsUnavailable as unavailable:
        await asyncio.to_thread(warn_unavailable, streamer, unavailable)
        return await interaction.response.send_message(
            embed=response_error_embed("stream-elements-unavailable", locale),
            ephemeral=True,
        )

    namespace = "commands.commands.commons.stream-elements-manager.commands"
    title = ml(f"{namespace}.embed.title", locale=locale)
    if not commands_list:
        empty = ml(f"{namespace}.empty", locale=locale).replace("$streamer", streamer)
        return await interaction.response.send_message(
            embed=base_embed(title, empty), ephemeral=True
        )

    view = PaginationView(
        interaction,
        title,
        ml(f"{namespace}.embed.desc", locale=locale).replace("$streamer", streamer),
        commands_list,
        sep=view_constants.COMMANDS_PAGE_SIZE,
    )
    await view.send(ephemeral=True)


def create_response_embed(
    command: str,
    reply: str,
    user: discord.User,
    streamer: str,
    prefix: str,
    locale: str,
) -> discord.Embed:
    """A StreamElements reply, signed by whoever asked, pointing at the command list."""
    embed = discord.Embed(
        description=reply,
        color=(int(style_constants.BACKGROUND_COLOR, base=16)),
    )
    embed.set_author(name=f"!{command}", icon_url=user.display_avatar.url)
    footer = fill(
        ml("commands.commands.commons.stream-elements-manager.commands.reply-footer", locale),
        command=f"{prefix}commands",
        streamer=streamer,
    )
    embed.set_footer(text=f"• {footer}")

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


def get_commands_in_cache_or_populate(channel_id: str, user: discord.User) -> Dict[str, str]:
    """The streamer's enabled commands as `!name`, each reply in the asker's words."""
    return {
        f"!{command}": parse_placeholders(reply, user)
        for command, reply in _channel_commands(channel_id).items()
    }


def get_reply_in_cache_or_populate(
    channel_id: str, command: str, user: discord.User
) -> Optional[str]:
    """The reply to one command in the asker's words, None when the streamer has no such command."""
    reply = _channel_commands(channel_id).get(command)
    return parse_placeholders(reply, user) if reply else None


def _channel_commands(channel_id: str) -> Dict[str, str]:
    """The streamer's enabled commands by name, with their replies as written: cached a
    day, a streamer with none for a few minutes, a failure never (it raises)."""
    cache_key = constants.REDIS_STREAM_ELEMENTS_COMMANDS.format(channel_id=channel_id)
    cached = cache.get_data_from_redis(cache_key)
    if cached:
        return cached["commands"]

    try:
        fetched = StreamElementsClient.get_chat_commands(channel_id)
    except Exception as error:
        raise StreamElementsUnavailable(
            f"commands of {channel_id}: {type(error).__name__}: {error}"
        ) from error
    if not isinstance(fetched, list):
        raise StreamElementsUnavailable(f"commands of {channel_id}: {_status(fetched)}")

    commands = {
        command.get("command"): command.get("reply")
        for command in fetched
        if command.get("enabled")
    }
    seconds = (
        constants.STREAM_ELEMENTS_COMMANDS_CACHE_SECONDS
        if commands
        else constants.STREAM_ELEMENTS_NO_COMMANDS_CACHE_SECONDS
    )
    cache.set_data_in_redis_with_expiration(cache_key, {"commands": commands}, seconds)
    return commands
