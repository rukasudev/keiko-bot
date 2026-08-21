"""Contract for the generic report browser.

One command with a menu replaces a handful of sibling commands. The behavior
that has to hold: choosing a section EDITS the message instead of sending
another one (otherwise every look leaves a trail of dead embeds), the sections
are built lazily, and a section that fails does not take the menu down with it.
"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from app.constants import DiscordLimits as limits
from app.views.report_browser import ReportBrowser, ReportBrowserView

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("report_browser")]


def embed(title):
    return discord.Embed(title=title, description=f"body of {title}")


def sections(counter=None):
    def build(name):
        def _build():
            if counter is not None:
                counter.append(name)
            return embed(name)
        return _build

    return [
        {"key": "funnel", "label": "Funnel", "emoji": "📉", "build": build("funnel")},
        {"key": "friction", "label": "Friction", "emoji": "🧱", "build": build("friction")},
        {"key": "churn", "label": "Churn", "emoji": "🚪", "build": build("churn")},
    ]


def fake_interaction(done=False):
    return SimpleNamespace(
        response=SimpleNamespace(
            is_done=lambda: done,
            send_message=AsyncMock(),
            edit_message=AsyncMock(),
            defer=AsyncMock(),
        ),
        followup=SimpleNamespace(send=AsyncMock()),
    )


async def test_it_opens_on_the_first_section():
    interaction = fake_interaction()
    await ReportBrowser(sections(), placeholder="Pick one").send(interaction)

    kwargs = interaction.response.send_message.await_args.kwargs
    assert kwargs["embed"].title == "funnel"
    assert kwargs["ephemeral"] is True


async def test_choosing_a_section_edits_the_message_instead_of_sending_another():
    view = ReportBrowserView([*_as_sections()], placeholder="Pick one")
    view.selected_options = ["churn"]
    interaction = fake_interaction()

    await view.show_selected(interaction)

    interaction.response.edit_message.assert_awaited_once()
    assert interaction.response.edit_message.await_args.kwargs["embed"].title == "churn"
    assert not interaction.response.send_message.await_args_list


async def test_the_menu_survives_the_swap_so_you_can_keep_browsing():
    view = ReportBrowserView([*_as_sections()], placeholder="Pick one")
    view.selected_options = ["friction"]
    interaction = fake_interaction()

    await view.show_selected(interaction)

    assert interaction.response.edit_message.await_args.kwargs["view"] is view


async def test_only_the_section_being_shown_is_built():
    built = []
    browser = ReportBrowser(sections(built), placeholder="Pick one")
    await browser.send(fake_interaction())

    assert built == ["funnel"], "an expensive report must not run until asked for"


async def test_an_unknown_selection_is_acknowledged_not_crashed():
    view = ReportBrowserView([*_as_sections()], placeholder="Pick one")
    view.selected_options = ["does-not-exist"]
    interaction = fake_interaction()

    await view.show_selected(interaction)

    interaction.response.defer.assert_awaited_once()
    assert not interaction.response.edit_message.await_args_list


async def test_it_uses_followup_when_the_interaction_was_already_deferred():
    interaction = fake_interaction(done=True)
    await ReportBrowser(sections(), placeholder="Pick one").send(interaction)

    interaction.followup.send.assert_awaited_once()
    assert not interaction.response.send_message.await_args_list


def test_menu_labels_respect_the_discord_select_limit():
    view = ReportBrowserView(
        [*_as_sections(), *_long_section()], placeholder="Pick one"
    )
    select = view.children[0]

    for option in select.options:
        assert len(option.label) <= limits.SELECT_OPTION_TEXT
        if option.description:
            assert len(option.description) <= limits.SELECT_OPTION_TEXT


def test_one_choice_at_a_time():
    view = ReportBrowserView([*_as_sections()], placeholder="Pick one")
    assert view.children[0].max_values == 1


def _as_sections():
    from app.views.report_browser import ReportSection
    return [ReportSection(**section) for section in sections()]


def _long_section():
    from app.views.report_browser import ReportSection
    return [ReportSection(
        key="long", label="L" * 200, build=lambda: embed("long"),
        description="D" * 200,
    )]
