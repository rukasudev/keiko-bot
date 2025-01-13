from typing import Any, Dict, List

import discord

from app.services.utils import get_command_by_key, ml, parse_valid_locale


def parse_history_data(
    data: List[Dict[str, Any]], interaction: discord.Interaction, guild: discord.Guild=None, with_cog: bool = None
) -> Dict[str, Any]:
    system_desc = ml(
        "commands.command-events.system.description", locale=interaction.locale
    )

    response = {}
    guild = guild or interaction.guild
    for item in data:
        key = item["datetime"].strftime("%Y-%m-%d %H:%M:%S") + " UTC"
        user = guild.get_member(int(item["user_id"]))

        command = "short-description" if not with_cog else "short-description-with-cog"
        desc = ml(
            f"commands.command-events.{item['event']}.{command}",
            locale=interaction.locale,
        ).replace("$user", user.mention).replace("$cog_name", item["cog_key"])

        if user.id == interaction.client.user.id:
            desc += f" _({system_desc})_"

        if key not in response:
            response[key] = []

        response[key].append(desc)

    return parse_history_data_list(response)


def parse_history_desc(interaction: discord.Interaction, cog_key: str) -> str:
    command = get_command_by_key(interaction.client, cog_key)
    locale = parse_valid_locale(interaction.locale)
    command_name = command.extras[locale.value].get("locale_qualified_name")
    return ml("buttons.changes-history.desc", locale=interaction.locale).replace(
        "$cog_key", command_name
    )


def parse_history_data_list(data: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in data:
        data[item] = "\n".join(data[item])
    return data
