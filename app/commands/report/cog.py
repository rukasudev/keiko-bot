"""/report: a bug report sent to the team, to Notion and back to its author."""

import random
from typing import Any, List, Optional

import discord
from discord import app_commands
from requests import RequestException, Response

from app import logger
from app.bot import DiscordBot
from app.commands.report.service import ticket_embed
from app.constants import LogTypes as logconstants
from app.core import analytics
from app.core.threads import to_thread
from app.core.utils import parse_valid_locale
from app.decorators import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog
from app.ui.embed import response_embed


async def report_autocomplete(
    interaction: discord.Interaction[DiscordBot],
    current: str,
) -> List[app_commands.Choice[str]]:
    """The commands whose name holds what the author typed."""
    locale = parse_valid_locale(interaction.locale)
    commands = interaction.client.all_cogs.get(locale.value, [])
    return [
        app_commands.Choice(name=command["name"], value=command["key"])
        for command in commands
        if current.lower() in command["name"].lower()
    ]


class Report(Cog, name="report"):
    """The /report slash command."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    @keiko_command(
        name=locale_str("report", type="name", namespace="report.command.slash"),
        description=locale_str(
            "report", type="description", namespace="report.command.slash"
        ),
    )
    @app_commands.autocomplete(command=report_autocomplete)
    async def report(
        self,
        interaction: discord.Interaction[DiscordBot],
        title: str,
        description: str,
        command: str,
        attachment: Optional[discord.Attachment] = None,
    ) -> None:
        """Send the report to the team and to Notion, and a copy to its author."""
        await interaction.response.defer()

        analytics.record(
            "report.submitted",
            guild_id=interaction.guild_id,
            actor=analytics.actor("user", interaction.user.id),
            props={"command": command},
        )

        notion_response = await to_thread(
            self._create_notion_report,
            interaction,
            title,
            description,
            command,
            attachment,
        )
        ticket_id = self._get_ticket_unique_id(notion_response)

        embed = self._create_report_embed(
            interaction, title, description, command, attachment, ticket_id
        )
        self._handle_notion_response(notion_response, embed, interaction)

        log_channel: Any = self.bot.get_channel(
            self.bot.config.ADMIN_REPORTS_CHANNEL_ID
        )
        await log_channel.send(embed=embed)

        response = response_embed(
            "report.ui.responses.sent", interaction.locale, discord.Color.green()
        )
        await interaction.followup.send(embed=response, ephemeral=True)

        await self._send_report_dm(
            interaction, title, description, command, attachment, ticket_id
        )

    def _create_report_embed(
        self,
        interaction: discord.Interaction[DiscordBot],
        title: str,
        description: str,
        command: str,
        attachment: Optional[discord.Attachment],
        ticket_id: str,
    ) -> discord.Embed:
        embed = discord.Embed(
            color=discord.Color.red(), title=f"📩 New ticket created [#{ticket_id}]!"
        )
        embed.add_field(name="Title", value=title, inline=False)
        embed.add_field(name="Description", value=description, inline=False)
        embed.add_field(name="Command", value=command, inline=False)
        embed.add_field(name="Author", value=interaction.user.mention)
        embed.add_field(
            name="Attachment",
            value=attachment.url if attachment else "No attachment",
            inline=False,
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        return embed

    def _create_notion_report(
        self,
        interaction: discord.Interaction[DiscordBot],
        title: str,
        description: str,
        command: str,
        attachment: Optional[discord.Attachment],
    ) -> Optional[Response]:
        try:
            return self.bot.notion.create_report(
                title=title,
                description=description,
                command=command,
                author=str(interaction.user.id),
                attachment_url=attachment.url if attachment else None,
            )
        except RequestException as error:
            logger.error(
                f"Error creating report in Notion: {type(error).__name__}",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )
            return None

    def _handle_notion_response(
        self,
        notion_response: Optional[Response],
        embed: discord.Embed,
        interaction: discord.Interaction[DiscordBot],
    ) -> None:
        if notion_response is None:
            return

        if notion_response.status_code == 200:
            embed.add_field(
                name="Notion Ticket Url",
                value=notion_response.json().get("url"),
                inline=False,
            )
        else:
            logger.error(
                "Error creating report in Notion "
                f"(status_code: {notion_response.status_code}): "
                f"{notion_response.text[:200]}",
                interaction=interaction,
                log_type=logconstants.COMMAND_ERROR_TYPE,
            )

    def _get_ticket_unique_id(self, notion_response: Optional[Response] = None) -> str:
        unique_id_object: dict[str, Any] = {
            "prefix": "KEIKO",
            "number": random.randint(1000, 9999),
        }

        if notion_response:
            unique_id_object = (
                notion_response.json().get("properties").get("ID").get("unique_id")
            )

        prefix = unique_id_object.get("prefix")
        return f"{prefix}-{str(unique_id_object.get('number'))}"

    async def _send_report_dm(
        self,
        interaction: discord.Interaction[DiscordBot],
        title: str,
        description: str,
        command: str,
        attachment: Optional[discord.Attachment],
        ticket_unique_id: str,
    ) -> None:
        commands = interaction.client.all_cogs[interaction.locale.value]
        command_info = next((c for c in commands if c["key"] == command), None)
        command_name = command_info["name"] if command_info else command

        embed = ticket_embed(
            interaction.locale,
            ticket_unique_id,
            title,
            description,
            command_name,
            attachment.url if attachment else None,
        )

        dm_channel = await interaction.user.create_dm()
        await dm_channel.send(embed=embed)


async def setup(bot: DiscordBot) -> None:
    """Load the /report cog."""
    await bot.add_cog(Report(bot))
