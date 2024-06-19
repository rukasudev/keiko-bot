from typing import List

import discord
from discord import app_commands

from app import logger
from app.bot import DiscordBot
from app.components.embed import report_embed, response_embed
from app.services.utils import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog
from app.constants import LogTypes as logconstants


async def report_autocomplete(
    interaction: discord.Interaction,
    current: str,
) -> List[app_commands.Choice[str]]:
    commands = interaction.client.all_commands[interaction.locale.value]
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
        embed = self._create_report_embed(interaction, title, description, command, attachment)
        notion_response = self._create_notion_report(interaction, title, description, command, attachment)
        self._handle_notion_response(notion_response, embed, interaction)

        log_channel = self.bot.get_channel(self.bot.config.ADMIN_REPORTS_CHANNEL_ID)
        await log_channel.send(embed=embed)

        response = response_embed(
            "commands.commands.report.response", interaction.locale, discord.Color.green(), footer=True
        )
        await interaction.response.send_message(embed=response, ephemeral=True)

        await self._send_report_dm(interaction, title, description, command, attachment, notion_response)

    def _create_report_embed(self, interaction, title, description, command, attachment):
        embed = discord.Embed(
            color=discord.Color.red(),
            title="📩 Novo ticket criado!"
        )
        embed.add_field(name="Título", value=title, inline=False)
        embed.add_field(name="Descrição", value=description, inline=False)
        embed.add_field(name="Comando", value=command, inline=False)
        embed.add_field(name="Autor", value=interaction.user.mention)
        embed.add_field(
            name="Anexo", value=attachment.url if attachment else "Sem anexo", inline=False
        )
        embed.set_thumbnail(url=interaction.user.avatar.url)
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

    async def _send_report_dm(self, interaction, title, description, command, attachment, notion_response):
        unique_id_object = notion_response.json().get("properties").get("ID").get("unique_id")
        ticket_unique_id = f"{unique_id_object.get('prefix')}-{str(unique_id_object.get('number'))}"

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
