"""The embed /report sends its author by DM."""

from typing import Optional

import discord

from app.ui.embed import response_embed


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
        "report.responses.ticket",
        locale,
        image=True,
        inline=True,
        values={"ticket": ticket},
        fields={
            "title": title,
            "command": command,
            "attachment": attachment,
            "description": description,
        },
    )
