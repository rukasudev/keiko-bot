from json import load
from pathlib import Path
from re import findall
from typing import Dict, List
from app.constants import Emojis as constants

import datetime
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
    return {role.name: str(role.id) for role in guild.roles if role.name != "@everyone"}


def list_roles_id(roles: List[discord.Role]) -> List[int]:
    return [str(role.id) for role in roles]


def check_two_lists_intersection(x: list, y: list) -> bool:
    return bool(set(x).intersection(y))


def check_answer_message(ctx, message) -> bool:
    return message.author == ctx.author and message.channel == ctx.channel

def parse_cogs_titles(cogs_list: List[Dict[str, str]]) -> Dict[str, str]:
    response = {}
    for item in cogs_list:
        response[item["key"]] = item["title"]
    return response

def parse_cog_data_to_param_result(cog_data: List[Dict[str, str]], form_json: Dict[str, str]) -> List[Dict[str, str]]:
    response = []
    for cog_key, value in cog_data.items():
        cogs_title = parse_cogs_titles(form_json)
        if not cogs_title.get(cog_key):
            continue

        response.append({
            "title": cogs_title[cog_key],
            "value": value
        })
    return response

def parse_form_params_result(responses: List[Dict[str, str]]) -> str:
    result = ""
    for item in responses:
        values = item["value"]

        if not values:
            values = "-"

        if not isinstance(values, str):
            values = ", ".join(item["value"])

        result += f"\n{constants.FRISBEE_EMOJI} {item['title']}: **{values}**"

    return result

def parse_locale(locale: str) -> str:
    return str(locale).split("-")[0]

def parse_command_event_description(description: str, interaction_date: datetime.datetime, command_name: str, user: str) -> str:
    description = description.replace("$command_name", command_name)
    description = description.replace("$date", interaction_date.strftime("%Y-%m-%d %H:%M:%S"))
    description = description.replace("$user", user)
    return description
