"""The embed /report sends its author by DM."""

from typing import Any, Optional

import discord

from app.core.facts import Facts, facts
from app.ui.embed import response_embed


def command_facts(**values: Any) -> Facts:
    """The facts this command's code passes its copy, each under its address."""
    return facts("report", **values)


def ticket_embed(
    locale: str | discord.Locale,
    ticket: str,
    title: str,
    description: str,
    command: str,
    attachment: Optional[str] = None,
) -> discord.Embed:
    """The report as its author receives it, with the attachment's link if any."""
    return response_embed(
        "report.ui.responses.ticket",
        locale,
        image=True,
        inline=True,
        facts=command_facts(ticket=ticket),
        fields={
            "title": title,
            "command": command,
            "attachment": attachment,
            "description": description,
        },
    )
