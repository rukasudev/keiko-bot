from typing import Any, Callable, Dict, List, Optional

import discord

from app.constants import Commands
from app.core.cogs import lifecycle_key
from app.core.commands import setup_feature
from app.core.utils import command_text, discord_time, fill, ml


def change_label(cog_key: str, locale: Any) -> str:
    """The feature a change happened in, as /setup names it, else its key."""
    if cog_key == Commands.SERVER_SETTINGS_KEY:
        return str(ml("texts.server_settings", locale=locale))
    command = setup_feature(cog_key)
    if command is None:
        return f"`{cog_key}`"
    return command_text(command, "command.setup.label", locale)


def parse_history_data(
    data: List[Dict[str, Any]],
    interaction: discord.Interaction,
    label_for: Optional[Callable[[str], str]] = None,
) -> Dict[str, Any]:
    """Each change as a numbered field: what changed, then who did it and when."""
    locale = interaction.locale
    by = ml("commands.records.history.lines.by", locale=locale)
    system = ml("commands.records.history.lines.system", locale=locale)

    fields = {}
    for number, item in enumerate(data, start=1):
        actor = item.get("actor") or {}
        is_bot_user = actor.get("kind") == "bot" or str(actor.get("id")) == str(
            interaction.client.user.id
        )
        title = ml(
            f"commands.records.history.lines.{lifecycle_key(item['type'])}",
            locale=locale,
        )
        if label_for is not None:
            title = f"{label_for(item['cog'])} · {title}"

        value = fill(by, user=f"<@{actor.get('id')}>", date=discord_time(item["ts"]))
        if is_bot_user:
            value += f"\n-# {system}"
        fields[f"{number}. {title}"] = value

    return fields
