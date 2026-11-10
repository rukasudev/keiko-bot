from typing import Any, Callable, Dict, List, Optional

import discord

from app.services.utils import get_command_display_name, ml


def parse_history_data(
    data: List[Dict[str, Any]],
    interaction: discord.Interaction,
    with_cog: bool = None,
    label_for: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Each audit record as the line the history lists, the actor named by mention."""
    system_desc = ml(
        "commands.command-events.system.description", locale=interaction.locale
    )

    response = {}
    for item in data:
        key = item["datetime"].strftime("%Y-%m-%d %H:%M:%S") + " UTC"
        user_mention = f"<@{item['user_id']}>"
        is_bot_user = str(item["user_id"]) == str(interaction.client.user.id)

        command = "short-description" if not with_cog else "short-description-with-cog"
        desc = ml(
            f"commands.command-events.{item['event']}.{command}",
            locale=interaction.locale,
        ).replace("$user", user_mention).replace("$cog_name", item["cog_key"])

        if is_bot_user:
            desc += f" _({system_desc})_"

        if label_for is not None:
            desc = f"{label_for(item['cog_key'])} · {desc}"

        if key not in response:
            response[key] = []

        response[key].append(desc)

    return parse_history_data_list(response)


def parse_history_desc(interaction: discord.Interaction, cog_key: str) -> str:
    command_name = get_command_display_name(interaction.client, cog_key, interaction.locale)
    return ml("buttons.changes-history.desc", locale=interaction.locale).replace(
        "$cog_key", command_name
    )


def parse_history_data_list(data: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in data:
        data[item] = "\n".join(data[item])
    return data
