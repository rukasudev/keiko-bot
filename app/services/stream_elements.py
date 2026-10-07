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
from app.exceptions import ErrorContext
from app.integrations.stream_elements import StreamElementsClient
from app.settings import open_feature
from app.services import analytics, cache
from app.services.utils import fill, ml
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
        command_length=len(command),
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

        locale = message.guild.preferred_locale if message.guild else None
        await message.reply(
            embed=create_response_embed(
                command, reply, message.author, streamer, prefix, locale
            )
        )
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


def create_response_embed(
    command: str,
    reply: str,
    user: discord.User,
    streamer: str,
    prefix: str,
    locale: Optional[discord.Locale],
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
    day, a streamer with none for a few minutes, a failure never."""
    cache_key = constants.REDIS_STREAM_ELEMENTS_COMMANDS.format(channel_id=channel_id)
    cached = cache.get_data_from_redis(cache_key)
    if cached:
        return cached["commands"]

    fetched = StreamElementsClient.get_chat_commands(channel_id)
    if not isinstance(fetched, list):
        return {}

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
