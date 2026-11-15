from typing import Any, Callable, Dict, List, Optional

import discord

from app.core.cogs import lifecycle_key
from app.core.utils import ml


def parse_history_data(
    data: List[Dict[str, Any]],
    interaction: discord.Interaction,
    with_cog: bool = None,
    label_for: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Each change as the line the history lists, the actor named by mention."""
    system_desc = ml("commands.records.history.lines.system", locale=interaction.locale)

    response = {}
    for item in data:
        key = item["ts"].strftime("%Y-%m-%d %H:%M:%S") + " UTC"
        actor = item.get("actor") or {}
        user_mention = f"<@{actor.get('id')}>"
        is_bot_user = actor.get("kind") == "bot" or str(actor.get("id")) == str(
            interaction.client.user.id
        )

        line = lifecycle_key(item["type"])
        if with_cog:
            line = f"{line}_with_feature"
        desc = ml(
            f"commands.records.history.lines.{line}", locale=interaction.locale
        ).replace("$user", user_mention).replace("$cog_name", item["cog"])

        if is_bot_user:
            desc += f" _({system_desc})_"

        if label_for is not None:
            desc = f"{label_for(item['cog'])} · {desc}"

        if key not in response:
            response[key] = []

        response[key].append(desc)

    return parse_history_data_list(response)


def parse_history_data_list(data: List[Dict[str, Any]]) -> Dict[str, Any]:
    for item in data:
        data[item] = "\n".join(data[item])
    return data
