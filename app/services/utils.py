from json import load
from pathlib import Path
from re import findall
from typing import Dict, List

import discord


def check_message_has_link(message: str, allowed_links: List[str]) -> List[str]:
    links = [
        link
        for link in findall(
            "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
            message.content.lower(),
        )
        if not any(allowed.lower() in link.lower() for allowed in allowed_links)
    ]

    return links


def parse_json_to_dict(key: str, locale: str, file: str) -> Dict[str, str]:
    file = Path.joinpath(
        Path().absolute(), "app", "resources", str(locale).lower(), key, file
    )
    with open(file, "r") as f:
        return load(f)


def get_guild_text_channels_id(guild, channels: list) -> List[str]:
    return [
        str(discord.utils.get(guild.channels, name=channel).id) for channel in channels
    ]


def get_text_channels_by_guild(guild: discord.Guild) -> Dict[str, str]:
    return {channel.name: str(channel.id) for channel in guild.text_channels}


def get_roles_by_guild(guild: discord.Guild) -> Dict[str, str]:
    return {role.name: str(role.id) for role in guild.roles if role != "@everyone"}


def list_roles_id(roles: List[discord.Role]) -> List[int]:
    return [str(role.id) for role in roles]


def check_two_lists_intersection(x: list, y: list) -> bool:
    return bool(set(x).intersection(y))


def check_answer_message(ctx, message) -> bool:
    return message.author == ctx.author and message.channel == ctx.channel
