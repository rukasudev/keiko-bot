import discord

from app import redis_client
from app.components.buttons import ConfirmButton, CancelButtom
from app.components.embed import parse_dict_to_embed
from app.components.modals import CustomModal
from app.services.moderations import upsert_cog_by_guild, upsert_parameter_by_guild
from app.services.utils import get_text_channels_by_guild
from app.views.options import OptionsView
from json import load
from pathlib import Path


class Form(discord.ui.View):
    """
    A custom view to create a form message with questions and
    save to database.

    Attributes:
        `command_key` -- the key of the form message from form.json file
    """

    def __init__(self, key: str):
        self._index = 0
        self._form_data = {}
        self.command_key = key
        self.parse_form_to_dict()
        super().__init__()
        self.add_item(ConfirmButton(callback=self._callback))
        self.add_item(CancelButtom())

    @classmethod
    def parse_form_to_dict(cls) -> dict[str, str]:
        """Return a key from form.json file"""
        file = Path.joinpath(Path().absolute(), "app", "resources", "forms.json")
        with open(file, "r") as f:
            form = load(f)
            cls.forms = form

    def parse_question_to_modal(self, question: dict[str, str]) -> discord.ui.Modal:
        """Return a discord modal from form.json dict"""
        return CustomModal(
            title=question["title"],
            label=question["description"],
            max_length=40,
            required=True,
            placeholder=question["placeholder"],
            command_key=self.command_key,
            redis_key=self._question_key,
            callback=self._callback,
            cache=True,
        )

    def _update_form_counter(func):
        async def update_counter(self, args):
            self._index += 1
            self._question_key = list(self.forms.keys())[self._index]
            self._question = self.forms[self._question_key]
            await func(self, args)

        return update_counter

    def get_question_embed_by_key(self, question_key: str) -> discord.Embed:
        """Return a discord embed from form dict"""
        return parse_dict_to_embed(self.forms[question_key])

    # TODO: improve redis usage in this method
    def _parse_redis_description_values(self, guild_id: str, description: str) -> str:
        for key in redis_client.scan_iter(f"{guild_id}@{self.command_key}:*"):
            parsed_list = None
            redis_key = key.split(":")[1].replace("$channels", "")
            key_type = redis_client.type(key)

            if key_type == "list":
                value = redis_client.lrange(key, 0, -1)
                parsed_list = ", ".join(value)
            else:
                value = redis_client.get(key)

            title = self.forms[redis_key]["title"]

            if "channels" in key:
                self._form_data[redis_key] = [
                    index
                    for channel, index in self.guild_channels.items()
                    if channel in value
                ]
            else:
                self._form_data[redis_key] = value

            description += f"\n:flying_disc: {title}: **{parsed_list or value}**"

        return description

    async def _modal(self, interaction: discord.Interaction):
        modal = self.parse_question_to_modal(self._question)

        await interaction.response.send_modal(modal)

    async def _options(
        self, interaction: discord.Interaction, embed: discord.Embed, options: list[str]
    ):
        await interaction.response.defer()

        view = OptionsView(
            command_key=self.command_key,
            redis_key=self._question_key,
            options=options,
            callback=self._callback,
            cache=True,
        )

        await interaction.message.edit(embed=embed, view=view)

    async def _channels(
        self,
        interaction: discord.Interaction,
        embed: discord.Embed,
        channels: dict[str, str],
    ):
        await interaction.response.defer()

        view = OptionsView(
            command_key=self.command_key,
            redis_key="$channels" + self._question_key,
            options=list(channels.keys()),
            callback=self._callback,
            cache=True,
        )

        await interaction.message.edit(embed=embed, view=view)

    async def _resume(self, interaction: discord.Interaction, embed: discord.Embed):
        embed.description = self._parse_redis_description_values(
            interaction.guild.id, embed.description
        )

        self.add_item(ConfirmButton(callback=self._finish))
        self.add_item(CancelButtom())
        await interaction.response.edit_message(embed=embed, view=self)

    async def _finish(self, interaction: discord.Interaction):
        guild_id = interaction.guild.id
        self._form_data["guild_id"] = str(guild_id)

        await upsert_parameter_by_guild(guild_id=guild_id, parameter=self.command_key)
        await upsert_cog_by_guild(guild_id, self.command_key, self._form_data)

        self.clear_items()

        embed = interaction.message.embeds[0]
        embed.title = f"Comando ativado com sucesso!"

        await interaction.response.edit_message(embed=embed, view=self)

    # TODO: transfer this keys to constants
    @_update_form_counter
    async def _callback(self, interaction: discord.Interaction):
        """A callback method called after view buttons has interaction"""

        self.clear_items()

        embed = self.get_question_embed_by_key(self._question_key)
        action = self._question["action"]

        if action == "modal":
            return await self._modal(interaction)

        if action == "options":
            options = self._question["options"]
            return await self._options(interaction, embed, options)

        if action == "channels":
            self.guild_channels = get_text_channels_by_guild(interaction.guild)
            return await self._channels(interaction, embed, self.guild_channels)

        if action == "resume":
            return await self._resume(interaction, embed)
