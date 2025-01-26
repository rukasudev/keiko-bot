import random
import re
from typing import Any, Dict, List

import discord

from app.constants import Commands as constants
from app.constants import KeikoIcons as icons_constants
from app.constants import Style as style_constants
from app.integrations.stream_elements import StreamElementsClient
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)
from app.views.pagination_without_interaction import PaginationWithoutInteractionView


async def manager(interaction: discord.Interaction, guild_id: str):
    cogs = cache.get_cog_data_or_populate(guild_id, constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY, manager=True)

    if cogs == None:
        return await send_command_form_message(interaction, constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY)

    await send_command_manager_message(
        interaction, constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY, cogs
    )

async def check_message(guild_id: str, message: discord.Message, prefix: str) -> None:
    cogs = cache.get_cog_data_or_populate(guild_id, constants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY)

    if not cogs:
        return

    streamer = cogs.get("streamer")
    if not streamer:
        return

    command = message.content.split(" ")[0].replace(prefix, "")
    channel_id = cogs.get("channel_id")

    if command == "commands":
        view = parse_command_list_view(channel_id, message, streamer)
        return await view.send(message)

    reply = get_reply_in_cache_or_populate(channel_id, command, message.author)
    if not reply:
        return

    await message.reply(embed=create_response_embed(command, reply, message.author, streamer))

def parse_command_list_view(channel_id: str, message: discord.Message, streamer: str) -> discord.ui.View:
    from app import bot
    commands_list = get_commands_in_cache_or_populate(channel_id, message.author)
    if not commands_list:
        return []

    title = "StreamElements Commands"
    description = f"Here is a list of all the StreamElements commands available in {streamer}'s channel"
    icon = bot.twitch.get_user_info(streamer).get("profile_image_url")
    view = PaginationWithoutInteractionView(title, description, commands_list, message, thumbnail=icon, sep=4)
    return view


def create_response_embed(command: str, reply: str, user: discord.User, streamer: str) -> discord.Embed:
    embed = discord.Embed(
        description=reply,
        color=(int(style_constants.BACKGROUND_COLOR, base=16)),
    )
    embed.set_author(name=f"!{command}", icon_url=user.avatar.url)
    embed.set_footer(text=f"• See all {streamer}'s StreamElements commands with ks!commands")

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
