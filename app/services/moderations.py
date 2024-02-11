from typing import Any, Dict

import discord

from app.constants import Commands as constants
from app.components.embed import parse_dict_to_embed
from app.data import cogs as cogs_data
from app.data import moderations as moderations_data
from app.services.utils import (
    parse_json_to_dict,
    parse_form_params_result,
    parse_cog_data_to_param_result,
    parse_locale
)


async def upsert_parameter_by_guild(guild_id: str, parameter: str, value: str):
    if not guild_id:
        print("Check if guild_id is correct to save cog")
        return

    return moderations_data.upsert_parameters_by_guild(
        guild_id=guild_id, parameter=parameter, value=value
    )

async def upsert_cog_by_guild(guild_id: str, cog: str, data: Dict[str, Any]):
    if not data.get("guild_id"):
        data["guild_id"] = str(guild_id)

    return cogs_data.upsert_cog_by_guild_id(guild_id, cog, data)


async def delete_cog_by_guild(guild_id: str, cog: str):
    if guild_id == "":
        print("Check if guild_id is correct to delete cog")
        return

    return cogs_data.delete_cog_by_guild_id(guild_id, cog)


async def send_command_form_message(interaction: discord.Interaction, key: str):
    from app.views.form import Form

    form_view = Form(form_key=key, locale=parse_locale(interaction.locale))
    embed = form_view.get_form_embed()

    await interaction.response.send_message(embed=embed, view=form_view, ephemeral=True)


async def send_command_manager_message(
    interaction: discord.Interaction,
    key: str,
    cog_data: Dict[str, str],
    additional_info: str=""
):
    from app.views.manager import Manager

    command_dict = parse_json_to_dict(key, parse_locale(interaction.locale), "command.json")
    embed = parse_dict_to_embed(command_dict)

    form_json = parse_json_to_dict(key, parse_locale(interaction.locale), "forms.json")
    description = parse_cog_data_to_param_result(cog_data, form_json)

    embed.description += parse_form_params_result(description)
    view = Manager(key, parse_locale(interaction.locale))

    if additional_info:
        embed.description += f"\n\n{additional_info}"

    await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def send_welcome_message():
    pass
    # random_number = random.randint(0, 19)
    # rules_channel = self.bot.get_channel(838125350185074758)
    # channel = self.bot.get_channel(838123186142052442)

    # embed = discord.Embed(
    #     title=random.choice(self.config.welcome_messages_title).replace(
    #         "{person_name}", member.name
    #     ),
    #     description=random.choice(
    #         self.config.welcome_messages_descriptions
    #     ).replace("{channel_mention}", rules_channel.mention),
    #     color=0xFFCFFF,
    # )
    # embed.set_thumbnail(url=member.avatar_url)
    # embed.set_footer(text="")

    # if random_number == 15:
    #     embed.set_image(url="")

    # await channel.send(embed=embed)
