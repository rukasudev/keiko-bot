"""Contract for the generic report browser.

One command with a menu replaces a handful of sibling commands. The behavior
that has to hold: choosing a section EDITS the message instead of sending
another one (otherwise every look leaves a trail of dead embeds), the sections
are built lazily, and a section that fails does not take the menu down with it.
"""
import discord
import pytest

from app.constants import DiscordLimits as limits
from app.ui.report_browser import ReportBrowser, ReportBrowserView, ReportSection

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("report_browser")]


def embed(title):
    return discord.Embed(title=title, description=f"body of {title}")


def sections(counter=None):
    def build(name):
        async def _build():
            if counter is not None:
                counter.append(name)
            return embed(name)
        return _build

    return [
        {"key": "funnel", "label": "Funnel", "emoji": "📉", "build": build("funnel")},
        {"key": "friction", "label": "Friction", "emoji": "🧱", "build": build("friction")},
        {"key": "churn", "label": "Churn", "emoji": "🚪", "build": build("churn")},
    ]


async def browsing(scenario_factory, built=None):
    """The browser sent as a command's answer: its first section on screen."""
    scenario = scenario_factory()
    await scenario.run(ReportBrowser(sections(built), placeholder="Pick one").send,
                       name="/report")
    return scenario


def bot_events(scenario):
    return [event for event in scenario.outputs if event["actor"] == "bot"]


async def test_it_opens_on_the_first_section(scenario_factory):
    scenario = await browsing(scenario_factory)

    scenario.expect_message(kind="send", title_contains="funnel", ephemeral=True)


async def test_choosing_a_section_edits_the_message_instead_of_sending_another(
    scenario_factory,
):
    scenario = await browsing(scenario_factory)
    message = scenario.current_message

    await scenario.select_option("churn")

    answer = scenario.expect_message(kind="edit", title_contains="churn")
    assert answer["message"] == scenario.alias(message)
    assert [event["kind"] for event in bot_events(scenario)] == ["send", "edit"]


async def test_the_menu_survives_the_swap_so_you_can_keep_browsing(scenario_factory):
    scenario = await browsing(scenario_factory)

    await scenario.select_option("friction")
    await scenario.select_option("churn")

    scenario.expect_message(kind="edit", title_contains="churn")
    assert len(scenario.live_messages) == 1, "one message the whole time"


async def test_only_the_section_being_shown_is_built(scenario_factory):
    built = []
    await browsing(scenario_factory, built)

    assert built == ["funnel"], "an expensive report must not run until asked for"


async def test_an_unknown_selection_is_acknowledged_not_crashed(scenario_factory):
    scenario = await browsing(scenario_factory)

    await scenario.select_option("does-not-exist")

    assert [event["kind"] for event in bot_events(scenario)] == ["send", "defer"]
    scenario.expect_message(kind="send", title_contains="funnel")


async def test_it_uses_followup_when_the_interaction_was_already_deferred(
    scenario_factory,
):
    scenario = scenario_factory()
    browser = ReportBrowser(sections(), placeholder="Pick one")

    async def deferred(interaction):
        await interaction.response.defer(ephemeral=True)
        await browser.send(interaction)

    await scenario.run(deferred, name="/report")

    assert [event["kind"] for event in bot_events(scenario)] == ["defer", "followup_send"]
    scenario.expect_message(kind="send", title_contains="funnel", ephemeral=True)


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
    return [ReportSection(**section) for section in sections()]


def _built(shown):
    async def build():
        return shown
    return build


def _long_section():
    return [ReportSection(
        key="long", label="L" * 200, build=_built(embed("long")),
        description="D" * 200,
    )]
