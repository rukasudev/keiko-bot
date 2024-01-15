from typing import Dict, List

import discord

from app.components.buttons import CancelButton, ConfirmButton
from app.components.embed import parse_dict_to_embed
from app.components.modals import CustomModal
from app.constants import FormConstants as constants
from app.services.moderations import upsert_cog_by_guild, upsert_parameter_by_guild
from app.services.utils import (
    get_roles_by_guild,
    get_text_channels_by_guild,
    parse_json_to_dict,
    parse_form_params_result
)
from app.views.options import OptionsView


class Form(discord.ui.View):
    """
    A custom view to create a form message with questions and
    after save the answers to database.

    Attributes:
        `command_key` -- the key of the form message from form.json file
    """

    def __init__(self, form_key: str, locale: str) -> None:
        self.command_key = form_key
        self.locale = locale
        self.questions = self._get_questions()
        super().__init__()
        self.add_item(ConfirmButton(callback=self._callback, locale=locale))
        self.add_item(CancelButton(locale=locale))

    def _get_questions(self) -> List[Dict[str, str]]:
        questions = parse_json_to_dict(
            self.command_key,
            self.locale,
            "forms.json"
        )
        yield from questions

    def _update_form_question(func):
        async def update_counter(self, args):
            self._save_question_response()
            self._question = next(self.questions)
            self.question_embed = parse_dict_to_embed(self._question)
            await func(self, args)

        return update_counter

    def _save_question_response(self):
        if not hasattr(self, "view"):
            return

        if not hasattr(self, "responses"):
            self.responses = []

        self.responses.append({
            "key": self._question["key"],
            "title": self._question["title"],
            "value": self.view.get_response()
        })

    def _parse_responses_to_cog(self, guild_id: int) -> Dict[str, str]:
        cog_param = {"guild_id": str(guild_id)}
        for item in self.responses:
            cog_param[item["key"]] = item["value"]
            if not isinstance(item["value"], str):
                cog_param[item["key"]] = list(item["value"])
        return cog_param

    def get_form_embed(self) -> discord.Embed:
        return parse_dict_to_embed(next(self.questions))

    async def show_modal(self, interaction: discord.Interaction):
        self.view = CustomModal(self._question, self._callback)
        await interaction.response.send_modal(self.view)

    async def show_options(self, interaction: discord.Interaction):
        await interaction.response.defer()
        self.view = OptionsView(
            options=self._question["options"],
            callback=self._callback,
            locale=self.locale
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=self.question_embed,
            view=self.view
        )

    async def show_channels(self, interaction: discord.Interaction):
        channels = get_text_channels_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=list(channels.keys()),
            callback=self._callback,
            locale=self.locale
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=self.question_embed,
            view=self.view
        )

    async def show_roles(self, interaction: discord.Interaction):
        roles = get_roles_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=list(roles.keys()),
            callback=self._callback,
            locale=self.locale
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id,
            embed=self.question_embed,
            view=self.view
        )

    async def show_resume(self, interaction: discord.Interaction):
        self.question_embed.description += parse_form_params_result(
            self.responses
        )

        self.add_item(ConfirmButton(callback=self._finish, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))
        await interaction.response.edit_message(
            embed=self.question_embed,
            view=self
        )

    async def _finish(self, interaction: discord.Interaction):
        cog_param = self._parse_responses_to_cog(interaction.guild_id)
        await upsert_parameter_by_guild(
            guild_id=interaction.guild_id,
            parameter=self.command_key,
            value=True
        )
        await upsert_cog_by_guild(
            guild_id=interaction.guild_id,
            cog=self.command_key,
            data=cog_param
        )

        self.clear_items()

        # TODO: pass this message to json to allow multilanguage in future
        embed = interaction.message.embeds[0]
        embed.title = f"Comando ativado com sucesso!"
        await interaction.response.edit_message(embed=embed, view=self)

    async def get_action_by_type(self, action, interaction) -> None:
        action_dict = {
            constants.MODAL_ACTION_KEY: self.show_modal,
            constants.OPTIONS_ACTION_KEY: self.show_options,
            constants.ROLES_ACTION_KEY: self.show_roles,
            constants.CHANNELS_ACTION_KEY: self.show_channels,
            constants.RESUME_ACTION_KEY: self.show_resume,
        }

        if action in action_dict:
            return await action_dict[action](interaction)

    @_update_form_question
    async def _callback(self, interaction: discord.Interaction) -> None:
        self.clear_items()

        action = self._question["action"]
        return await self.get_action_by_type(action, interaction)
