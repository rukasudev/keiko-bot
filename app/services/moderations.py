from datetime import datetime
from typing import Any, Dict, List

import discord

from app.components.buttons import HelpButtom
from app.components.embed import parse_dict_to_embed
from app.constants import Commands as commands_constants
from app.constants import GuildConstants as guild_constants
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.services.cogs import insert_cog_event, update_cog_by_guild
from app.services.utils import (
    ml,
    parse_cog_data_to_param_result,
    parse_form_params_result,
    parse_json_to_dict,
    parse_locale,
)


def update_moderations_by_guild(guild_id: str, key: str, value: str):
    if not guild_id:
        return

    moderations = moderations_data.find_moderations_by_guild(guild_id)
    if not moderations:
        data = parse_default_moderations(guild_id)
        data[key] = value

        return moderations_data.insert_moderations_by_guild(data)

    return moderations_data.update_moderations_by_guild(
        guild_id=guild_id, data=key, value=value
    )


def pause_all_moderations_by_guild(guild_id: str, bot_user_id: str):
    moderations = moderations_data.find_moderations_by_guild(guild_id)
    for key, value in moderations.items():

        if not isinstance(value, bool) or not value:
            continue

        update_moderations_by_guild(guild_id, key, False)
        update_cog_by_guild(
            guild_id=guild_id, cog_key=key, data={commands_constants.ENABLED_KEY: False}
        )

        if key == guild_constants.IS_BOT_ONLINE:
            continue

        insert_cog_event(
            str(guild_id),
            key,
            commands_constants.PAUSED_KEY,
            date=datetime.fromisoformat(datetime.now().isoformat()),
            user_id=bot_user_id,
        )

    return moderations


def pause_moderations_by_guild(guild_id: str, key: str):
    update_moderations_by_guild(guild_id, key, False)
    return update_cog_by_guild(
        guild_id=guild_id, cog_key=key, data={commands_constants.ENABLED_KEY: False}
    )


def unpause_moderations_by_guild(guild_id: str, key: str):
    update_moderations_by_guild(guild_id, key, True)
    return update_cog_by_guild(
        guild_id=guild_id, cog_key=key, data={commands_constants.ENABLED_KEY: True}
    )


def insert_moderations_by_guild(guild_id: str, data: Dict[str, Any] = None) -> str:
    default_data = parse_default_moderations(guild_id)

    return moderations_data.insert_moderations_by_guild(data or default_data)


def parse_default_moderations(guild_id: str) -> Dict[str, Any]:
    data = guild_constants.COGS_MODERATIONS_COMMANDS_DEFAULT
    data["guild_id"] = str(guild_id)
    return data


def insert_error_by_command(cog_key: str, error_message: str):
    if not isinstance(error_message, str):
        return None

    data = {"error_message": error_message}
    return cogs_data.insert_error_by_command(cog_key, data)


async def send_command_form_message(interaction: discord.Interaction, key: str):
    from app.views.form import Form

    form_view = Form(form_key=key, locale=parse_locale(interaction.locale))
    embed = form_view.get_form_embed()

    await interaction.response.send_message(embed=embed, view=form_view, ephemeral=True)


async def send_command_manager_message(
    interaction: discord.Interaction,
    key: str,
    cog_data: Dict[str, str],
    additional_info: str = "",
    additional_buttons: List[discord.ui.Button] = [],
):
    from app.views.manager import Manager

    command_dict = parse_json_to_dict(
        key, parse_locale(interaction.locale), "command.json"
    )
    embed = parse_dict_to_embed(command_dict, True)
    locale = parse_locale(interaction.locale)

    form_json = parse_json_to_dict(key, parse_locale(interaction.locale), "forms.json")
    description = parse_cog_data_to_param_result(cog_data, form_json)

    embed.description += parse_form_params_result(interaction.guild, description)
    view = Manager(key, cog_data, interaction)

    if not cog_data.get(commands_constants.ENABLED_KEY):
        embed.title += f" ({ml('commands.command-events.paused.key', locale=locale)})"

    if additional_info:
        embed.description += f"\n\n{additional_info}"

    additional_buttons.append(HelpButtom(locale=locale))

    row = 1
    for button in additional_buttons:
        if len(view.children) % 4 == 0:
            row += 1
        button.row = row
        view.add_item(button)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
