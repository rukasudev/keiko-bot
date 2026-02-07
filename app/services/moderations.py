from datetime import datetime
from typing import Any, Dict, List, Optional

import discord

from app.components.buttons import HelpButton
from app.components.embed import parse_form_dict_to_embed
from app.constants import Commands as commands_constants
from app.constants import GuildConstants as guild_constants
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.services.cogs import insert_cog_event, update_cog_by_guild
from app.services.utils import (
    get_form_settings_with_database_values,
    ml,
    parse_form_titles_descriptions,
    parse_form_yaml_to_dict,
    parse_locale,
    parse_settings_with_database_values,
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
    if not moderations:
        return None

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


def insert_moderations_by_guild(guild_id: str, data: Dict[str, Any] = None, owner_id: str = None) -> str:
    default_data = parse_default_moderations(guild_id, owner_id=owner_id)

    return moderations_data.insert_moderations_by_guild(data or default_data)


def parse_default_moderations(guild_id: str, owner_id: str = None) -> Dict[str, Any]:
    data = dict(guild_constants.COGS_MODERATIONS_COMMANDS_DEFAULT)
    data["guild_id"] = str(guild_id)
    if owner_id:
        data["owner_id"] = str(owner_id)
    return data


def insert_error_by_command(cog_key: str, error_message: str):
    if not isinstance(error_message, str):
        return None

    data = {"error_message": error_message}
    return cogs_data.insert_error_by_command(cog_key, data)


async def send_command_form_message(interaction: discord.Interaction, key: str):
    from app.views.form import Form

    form_view = Form(command_key=key, locale=parse_locale(interaction.locale))
    embed = form_view.get_form_embed()
    list_titles_descriptions = form_view.get_form_titles_and_descriptions()
    embed.description += parse_form_titles_descriptions(interaction, list_titles_descriptions)

    await interaction.response.send_message(embed=embed, view=form_view, ephemeral=True)


async def send_command_manager_message(
    interaction: discord.Interaction,
    key: str,
    cog_data: Dict[str, str],
    additional_info: str = "",
    additional_buttons: Optional[List[discord.ui.Button]] = None,
):
    from app.views.manager import Manager

    if not additional_buttons:
        additional_buttons = []

    locale = parse_locale(interaction.locale)

    form_steps = list(parse_form_yaml_to_dict(key))
    embed = parse_form_dict_to_embed(form_steps[0], locale, True)
    description = parse_settings_with_database_values(cog_data, form_steps, locale)

    embed.description += get_form_settings_with_database_values(interaction, description)
    view = Manager(key, cog_data, interaction)

    if not cog_data.get(commands_constants.ENABLED_KEY):
        embed.title += f" ({ml('commands.command-events.paused.key', locale=locale)})"

    if additional_info:
        embed.description += f"\n\n{additional_info}"

    additional_buttons.append(HelpButton(locale=locale))

    row = 1
    for button in additional_buttons:
        if len(view.children) % 4 == 0:
            row += 1
        button.row = row
        view.add_item(button)

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
