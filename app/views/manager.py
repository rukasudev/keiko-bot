import discord

from app import constants
from app.components.buttons import DisableButtom, ResetButtom
from app.services.moderations import delete_cog_by_guild, upsert_parameter_by_guild
from app.views.form import Form


class Manager(discord.ui.View):
    """
    A custom view to create a form message with questions and
    save to database.

    Attributes:
        `command_key` -- the key of the form message from form.json file
    """

    def __init__(self, key: str):
        self.command_key = key
        super().__init__()
        self.add_item(ResetButtom(callback=self.reset_callback))
        self.add_item(DisableButtom(callback=self.disable_callback))

    async def reset_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)

        await upsert_parameter_by_guild(
            guild_id=guild_id, parameter=self.command_key, value=False
        )

        await delete_cog_by_guild(guild_id, self.command_key)

        form_view = Form(key=self.command_key)
        embed = form_view.get_question_embed_by_key("form")

        await interaction.response.edit_message(embed=embed, view=form_view)

    async def disable_callback(self, interaction: discord.Interaction):
        guild_id = str(interaction.guild.id)
        embed = interaction.message.embeds[0]

        await upsert_parameter_by_guild(
            guild_id=guild_id, parameter=self.command_key, value=False
        )

        await delete_cog_by_guild(guild_id, self.command_key)

        embed.title = "Commando desativado com sucesso!"
        self.clear_items()

        await interaction.message.edit(embed=embed, view=self)
