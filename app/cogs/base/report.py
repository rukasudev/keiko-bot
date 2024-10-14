import random
from typing import List

import discord
from discord import app_commands

from app import logger
from app.bot import DiscordBot
from app.components.embed import report_embed, response_embed
from app.constants import LogTypes as logconstants
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog


async def report_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    commands = interaction.client.all_commands.get(interaction.locale.value, [])
    return [
        app_commands.Choice(name=command["name"], value=command["key"])
        for command in commands
        if current.lower() in command["name"].lower()
    ]


class Report(Cog, name="report"):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("report", type="name", namespace="report"),
        description=locale_str("report", type="desc", namespace="report"),
    )
    @app_commands.autocomplete(command=report_autocomplete)
    async def report(
        self,
        interaction: discord.Interaction,
        title: str,
        description: str,
        command: str,
        attachment: discord.Attachment = None,
    ) -> None:
        await interaction.response.defer()

        notion_response = self._create_notion_report(interaction, title, description, command, attachment)
        ticket_id = self._get_ticket_unique_id(notion_response)

        embed = self._create_report_embed(interaction, title, description, command, attachment, ticket_id)
        self._handle_notion_response(notion_response, embed, interaction)

        log_channel = self.bot.get_channel(self.bot.config.ADMIN_REPORTS_CHANNEL_ID)
        await log_channel.send(embed=embed)

        response = response_embed(
            "commands.commands.report.response", interaction.locale, discord.Color.green(), footer=True
        )
        await interaction.followup.send(embed=response, ephemeral=True)

        await self._send_report_dm(interaction, title, description, command, attachment, ticket_id)

    def _create_report_embed(self, interaction, title, description, command, attachment, ticket_id):
        embed = discord.Embed(
            color=discord.Color.red(),
            title=f"📩 Novo ticket criado [#{ticket_id}]!"
        )
        embed.add_field(name="Título", value=title, inline=False)
        embed.add_field(name="Descrição", value=description, inline=False)
        embed.add_field(name="Comando", value=command, inline=False)
        embed.add_field(name="Autor", value=interaction.user.mention)
        embed.add_field(
            name="Anexo", value=attachment.url if attachment else "Sem anexo", inline=False
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        return embed

    def _create_notion_report(self, interaction, title, description, command, attachment):
        return self.bot.notion.create_report(
            title=title,
            description=description,
            command=command,
            author=str(interaction.user.id),
            attachment_url=attachment.url if attachment else None,
        )

    def _handle_notion_response(self, notion_response, embed, interaction):
        if not notion_response:
            return

        if notion_response.status_code == 200:
            embed.add_field(
                name="Notion Ticket Url", value=notion_response.json().get("url"), inline=False
            )
        else:
            logger.error(
                f"Error creating report in Notion (status_code: {notion_response.status_code}): {notion_response.json()}",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )

    def _get_ticket_unique_id(self, notion_response: dict = None):
        unique_id_object = {"prefix": "KEIKO", "number": random.randint(1000, 9999)}

        if notion_response:
            unique_id_object = notion_response.json().get("properties").get("ID").get("unique_id")

        return f"{unique_id_object.get('prefix')}-{str(unique_id_object.get('number'))}"

    async def _send_report_dm(self, interaction, title, description, command, attachment, ticket_unique_id: str):
        commands = interaction.client.all_commands[interaction.locale.value]
        command_info = next((c for c in commands if c["key"] == command), None)

        embed = report_embed(
            "commands.commands.report",
            interaction.locale,
            title,
            description,
            command_info["name"],
            attachment.url if attachment else None,
        )
        embed.title = embed.title + f" [Ticket ID: #{ticket_unique_id}]"

        dm_channel = await interaction.user.create_dm()
        await dm_channel.send(embed=embed)

async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Report(bot))
