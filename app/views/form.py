from typing import Any, Callable, Dict, Generator, List

import discord

from app.components.buttons import CancelButton, ConfirmButton, EditButtom
from app.components.embed import parse_dict_to_embed
from app.components.modals import CustomModal
from app.constants import Commands as commandconstants
from app.constants import FormConstants as constants
from app.services.moderations import insert_cog_by_guild, update_moderations_by_guild
from app.services.utils import (
    get_available_roles_by_guild,
    get_roles_by_guild,
    get_text_channels_by_guild,
    ml,
    parse_command_event_description,
    parse_form_params_result,
    parse_json_to_dict,
)
from app.views.options import OptionsView


class Form(discord.ui.View):
    """
    A custom view to create a form message with questions and
    after save the answers to database.

    Attributes:
        `form_key` -- the key of the form message from form.json file
        `locale` -- the locale of the interaction (ex: pt-br, en-US)
    """

    def __init__(self, form_key: str, locale: str) -> None:
        self.command_key = form_key
        self.locale = locale
        self.questions = self._get_questions()
        super().__init__()
        self.add_item(ConfirmButton(callback=self._callback, locale=locale))
        self.add_item(CancelButton(locale=locale))

    def _get_questions(self) -> Generator[Any, Any, Any]:
        questions = parse_json_to_dict(self.command_key, self.locale, "forms.json")
        yield from questions

    def _update_form_question(func):
        async def update_counter(self, args):
            self._save_question_response()
            try:
                self._question = next(self.questions)
            except StopIteration:
                return await self._after_callback(args)
            self.question_embed = parse_dict_to_embed(self._question)
            await func(self, args)

        return update_counter

    async def _after_callback(self, interaction):
        if not self.after_callback:
            return
        return await self.after_callback(interaction)

    def _set_after_callback(self, after_callback: Callable):
        self.after_callback = after_callback

    def _save_question_response(self):
        if not hasattr(self, "view"):
            return

        if not hasattr(self, "responses"):
            self.responses = []

        self.responses.append(
            {
                "key": self._question["key"],
                "title": self._question["title"],
                "value": self.view.get_response(),
            }
        )

    async def update_resume(self, interaction: discord.Interaction):
        for edited_item in self.edited_form_view.responses:
            for item in self.responses:
                if edited_item["key"] == item["key"]:
                    item["value"] = edited_item["value"]
        return await self.show_resume(interaction)

    def _parse_responses_to_cog(self) -> Dict[str, str]:
        cog_param = {commandconstants.ENABLED_KEY: True}

        for item in self.responses:
            value = item["value"]
            if not isinstance(value, str):
                value = list(value)
            cog_param[item["key"]] = value
        return cog_param

    def filter_questions(self, questions: List[str]):
        self.questions = (
            question
            for question in iter(self._get_questions())
            if question["key"] in questions
        )

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
            locale=self.locale,
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.question_embed, view=self.view
        )

    async def show_channels(self, interaction: discord.Interaction):
        channels = get_text_channels_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=list(channels.keys()),
            callback=self._callback,
            locale=self.locale,
            required=True,
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.question_embed, view=self.view
        )

    async def show_roles(self, interaction: discord.Interaction):
        roles = get_roles_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=list(roles.keys()), callback=self._callback, locale=self.locale
        )
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.question_embed, view=self.view
        )

    async def show_available_roles(self, interaction: discord.Interaction):
        roles = get_available_roles_by_guild(interaction.guild)
        await interaction.response.defer()
        self.view = OptionsView(
            options=list(roles.keys()), callback=self._callback, locale=self.locale
        )

        if not roles:
            error_message = ml(
                "errors.command-default-roles-low-permissions.message",
                locale=self.locale,
            )
            self.question_embed.description = error_message

        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=self.question_embed, view=self.view
        )

    async def show_resume(self, interaction: discord.Interaction):
        embed = self.question_embed.copy()
        embed.description += parse_form_params_result(self.responses)

        self.add_item(EditButtom(after_callback=self.update_resume, locale=self.locale))
        self.add_item(ConfirmButton(callback=self._finish, locale=self.locale))
        self.add_item(CancelButton(locale=self.locale))
        await interaction.response.edit_message(embed=embed, view=self)

    async def _finish(self, interaction: discord.Interaction):
        cog_param = self._parse_responses_to_cog()
        update_moderations_by_guild(
            guild_id=interaction.guild_id, key=self.command_key, value=True
        )
        insert_cog_by_guild(
            guild_id=interaction.guild_id, cog=self.command_key, data=cog_param
        )

        self.clear_items()

        embed = interaction.message.embeds[0]
        embed.title = ml("commands.command-events.enabled.title", locale=self.locale)
        embed.description = parse_command_event_description(
            ml("commands.command-events.enabled.description", locale=self.locale),
            interaction.message.edited_at,
            interaction,
        )
        await interaction.response.send_message(embed=embed, view=self)

    async def get_action_by_type(self, action, interaction) -> None:
        action_dict = {
            constants.MODAL_ACTION_KEY: self.show_modal,
            constants.OPTIONS_ACTION_KEY: self.show_options,
            constants.ROLES_ACTION_KEY: self.show_roles,
            constants.AVAILABLE_ROLES_ACTION_KEY: self.show_available_roles,
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
