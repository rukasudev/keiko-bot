from json import load
from pathlib import Path
from re import findall

import discord


def parse_json_to_dict(key: str, locale: str, file: str) -> dict[str, str]:
    """Return a key from form.json file"""
    file = Path.joinpath(Path().absolute(), "app", "resources", str(locale).lower(), key, file)
    with open(file, "r") as f:
        return load(f)


def get_guild_text_channels_id(guild, channels: list) -> list:
    return [
        str(discord.utils.get(guild.channels, name=channel).id) for channel in channels
    ]


def get_text_channels_by_guild(guild: discord.Guild) -> dict[str, str]:
    channels = dict()

    for channel in guild.text_channels:
        channels[channel.name] = str(channel.id)

    return channels


def get_roles_by_guild(guild: discord.Guild) -> dict[str, str]:
    roles = dict()

    for role in guild.roles:
        roles[role.name] = str(role.id)

    return roles


def list_roles_id(roles: list[discord.Role]) -> list[int]:
    return [str(role.id) for role in roles]


def check_two_lists_intersection(x: list, y: list) -> bool:
    return bool(set(x).intersection(y))


def check_message_has_link(message: str, allowed_links: list[str]) -> list[str]:
    links = findall(
        "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
        message.content.lower(),
    )

    for allowed in allowed_links:
        for index, link in enumerate(links):
            if allowed.lower() in link.lower():
                links.pop(index)

    return links


def check_answer_message(ctx, message):
    return message.author == ctx.author and message.channel == ctx.channel
