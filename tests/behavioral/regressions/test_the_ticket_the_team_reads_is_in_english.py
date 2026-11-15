"""The ticket /report posts in the team's reports channel is written in English.

Broke as: `Report._create_report_embed` wrote the ticket the team reads in Portuguese
literals ("📩 Novo ticket criado", "Título", "Sem anexo"), while the writing-style
skill keeps what only whoever runs Keiko reads in English, with no `ml()` and no
pt-br counterpart. Shared behaviour: none; the reports channel of the admin server is
an operator surface, and the author's copy of the ticket (the DM) stays in the
author's language. Consumer that exposed it: `/report`
(`app/commands/report/cog.py`). Guaranteed: whatever the author's language, the
ticket is titled "📩 New ticket created [#<id>]!" and its fields read Title,
Description, Command, Author and Attachment, "No attachment" when none was sent.
"""

import re
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import requests

from tests.mocks.discord import MockInteraction, create_member

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]


def timed_out(**_fields):
    raise requests.ReadTimeout("api.notion.com did not answer in 15 seconds")


async def _ticket(guild, locale, monkeypatch, attachment=None):
    from app.commands.report.cog import Report

    reports = MagicMock()
    reports.send = AsyncMock()
    bot = SimpleNamespace(
        notion=SimpleNamespace(create_report=timed_out),
        config=SimpleNamespace(ADMIN_REPORTS_CHANNEL_ID=1),
        get_channel=lambda channel_id: reports,
    )
    monkeypatch.setattr(Report, "_send_report_dm", AsyncMock())
    interaction = MockInteraction(user=create_member(guild), guild=guild, locale=locale)
    interaction.command = SimpleNamespace(qualified_name="report")

    await Report.report.callback(
        Report(bot), interaction, "Broken button", "It does nothing", "block_links",
        attachment,
    )
    return reports.send.await_args.kwargs["embed"]


@pytest.mark.parametrize("locale", ["pt-BR", "en-US"])
async def test_the_ticket_reads_in_english_whatever_the_authors_language(
    guild, locale, monkeypatch
):
    ticket = await _ticket(guild, locale, monkeypatch)

    assert re.fullmatch(r"📩 New ticket created \[#KEIKO-\d{4}\]!", ticket.title)
    assert [field.name for field in ticket.fields] == [
        "Title", "Description", "Command", "Author", "Attachment",
    ]
    assert [field.value for field in ticket.fields][:3] == [
        "Broken button", "It does nothing", "block_links",
    ]
    assert ticket.fields[-1].value == "No attachment"


async def test_an_attached_file_is_linked_on_the_ticket(guild, monkeypatch):
    attachment = SimpleNamespace(url="https://cdn.discordapp.com/attachments/1/2/shot.png")

    ticket = await _ticket(guild, "pt-BR", monkeypatch, attachment)

    assert ticket.fields[-1].name == "Attachment"
    assert ticket.fields[-1].value == attachment.url
