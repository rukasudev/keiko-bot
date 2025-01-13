import discord

from app import bot
from app.components.buttons import GenericButton, HistoryButton
from app.constants import Commands as constants
from app.data import moderations as moderations_data
from app.services.cogs import find_cog_events_by_guild
from app.services.manager import parse_history_data
from app.services.utils import ml
from app.views.pagination import PaginationView


class LogInspectionView(discord.ui.View):
    def __init__(self, user: discord.User, guild: discord.Guild):
        self.user = user
        self.guild = guild
        self.actual_page = 0
        super().__init__(timeout=None)
        self.add_item(GenericButton("Next", self.callback, style=discord.ButtonStyle.primary))

    async def get_user_info_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.user.name,
            description=f"User ID: {self.user.id}",
            color=discord.Color.green(),
        )

        embed.add_field(name="Display Name", value=self.user.display_name, inline=False)
        embed.add_field(name="Date Created", value=self.parse_if_time(self.user.created_at), inline=False)
        embed.set_thumbnail(url=self.user.avatar.url)

        return embed

    async def get_guild_info_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.guild.name,
            color=discord.Color.green(),
        )

        embed.add_field(name="guild_id", value=self.guild.id, inline=False)
        embed.add_field(name="guild_created_at", value=self.parse_if_time(self.guild.created_at), inline=False)

        moderations = moderations_data.find_moderations_by_guild(self.guild.id)
        for key, value in moderations.items():
            if not value:
                continue

            if key == "created_at":
                key = "added_at"

            if key in ["guild_id", "_id"]:
                continue

            embed.add_field(name=key, value=self.parse_if_time(value), inline=False)


        integrations = await self.guild.integrations()
        for integration in integrations:
            if not isinstance(integration, discord.BotIntegration):
                continue

            if not integration.application.user.name == bot.user.name:
                continue

            bot_inviter = integration.user
            embed.add_field(name="invited_by", value=bot_inviter.name)

        embed.set_thumbnail(url=self.guild.icon.url)

        return embed

    async def history_callback(self, interaction: discord.Interaction):
        raw_data = []

        for cog_key in constants.COMMANDS_LIST:
            raw_data += find_cog_events_by_guild(self.guild.id, cog_key)

        data = parse_history_data(raw_data, interaction, guild=self.guild, with_cog=True)

        title = ml("buttons.changes-history.label", locale=interaction.locale)
        pagination_view = PaginationView(interaction, title, "", data, sep=4)

        await pagination_view.send(ephemeral=True)

    def parse_if_time(self, value):
        try:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        except AttributeError:
            return value

    async def send(self, interaction: discord.Interaction):
        embed = await self.get_user_info_embed()
        await interaction.response.send_message(embed=embed, view=self, ephemeral=True)

    async def callback(self, interaction: discord.Interaction):
        self.interaction = interaction

        page_index_to_embed = {
            0: self.get_user_info_embed,
            1: self.get_guild_info_embed,
        }
        self.actual_page = (self.actual_page + 1) % len(page_index_to_embed)
        if self.actual_page == 1:
            self.add_item(HistoryButton(callback=self.history_callback, locale=interaction.locale))

        new_embed = await page_index_to_embed[self.actual_page]()

        await interaction.response.edit_message(embed=new_embed, view=self)

