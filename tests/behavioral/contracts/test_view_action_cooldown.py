"""Shared contract: informational view actions are rate limited, not removed.

Help, Preview, Stats and their siblings answer with a message of their own, so
they used to remove themselves after one click to prevent spam. Removing the
item was a silent no-op on the manager panel (`BaseView.remove_item` only sees
top-level children) and, where it did work, it cost the user the button.

The replacement is `ActionCooldown` (`app/components/buttons.py`): a click
inside the window gets a cute ephemeral notice that deletes itself, the real
action does not run twice, and the button never leaves the screen. Navigation
buttons (Add, Edit, Remove, lifecycle) have NO cooldown: double-clicking them
is protected behavior (`test_view_lifecycle_regressions.py`).
"""
from types import SimpleNamespace

import pytest

from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

BLOCK_LINKS_COG = {
    "guild_id": GUILD_ID, "enabled": True,
    "mode": "block_all",
    "answer": "Nada de links por aqui!",
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "allowed_chats": {"style": "channel", "values": "100"},
    "custom_links": {
        "style": "composition",
        "values": [
            {"link": {"value": "twitch.tv/jway"},
             "match_type": {"value": "domain", "_raw_value": "domain"}},
        ],
    },
}

HELP = ml("buttons.help.label", locale="pt-br")
ADD = ml("buttons.add.label", locale="pt-br")
STATS = ml("buttons.stats.label", locale="pt-br")
NOTICE_TITLE = ml("buttons.cooldown.title", locale="pt-br")


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def advance(self, seconds: float) -> None:
        self.now += seconds

    def monotonic(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(
        "app.components.buttons.time", SimpleNamespace(monotonic=clock.monotonic)
    )
    return clock


async def _panel(scenario_factory, deps):
    from app.services.block_links import normalize_block_links_config

    deps.mongo_client.guild["block_links"].insert_one(dict(BLOCK_LINKS_COG))
    return await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(dict(BLOCK_LINKS_COG))
    )


def _events_titled(scenario, title: str) -> list:
    return [event for event in scenario.outputs
            if title in ((event.get("embed") or {}).get("title") or "")]


async def test_second_help_click_gets_the_notice_not_a_second_help(
    scenario_factory, deps, clock
):
    scenario = await _panel(scenario_factory, deps)
    help_title = f"🙋 {HELP}"

    await scenario.click(HELP)
    assert len(_events_titled(scenario, help_title)) == 1

    await scenario.click(HELP)

    notice = scenario.expect_message(kind="send", title_contains=NOTICE_TITLE,
                                     ephemeral=True)
    assert notice.get("delete_after"), "the notice cleans itself up"
    assert len(_events_titled(scenario, help_title)) == 1, \
        "the second click within the window must not send help again"


async def test_hammering_the_button_never_stacks_notices(
    scenario_factory, deps, clock
):
    """The notice must not be spammable either: at most one visible notice at
    a time. It deletes itself after 5s, so further hot clicks inside that
    lifetime are acknowledged in silence (a deferred update: Discord shows
    neither a message nor an "interaction failed")."""
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(HELP)
    await scenario.click(HELP)          # notice
    await scenario.click(HELP)          # silence
    await scenario.click(HELP)          # silence

    assert len(_events_titled(scenario, NOTICE_TITLE)) == 1, \
        "one notice on screen at a time, however hard the button is hammered"
    assert len(_events_titled(scenario, f"🙋 {HELP}")) == 1


async def test_a_new_notice_may_appear_once_the_previous_one_expired(
    scenario_factory, deps, clock
):
    """delete_after removed the first notice from the screen; a hot click
    after that must not be answered with silence (that would read as the
    dead-button bug). The action itself stays blocked for the full window."""
    from app.constants import Commands as constants

    scenario = await _panel(scenario_factory, deps)

    await scenario.click(HELP)
    await scenario.click(HELP)                    # first notice
    clock.advance(constants.VIEW_ACTION_NOTICE_SECONDS)   # notice gone; window hot
    await scenario.click(HELP)                    # second notice, no help

    assert len(_events_titled(scenario, NOTICE_TITLE)) == 2
    assert len(_events_titled(scenario, f"🙋 {HELP}")) == 1, \
        "the action stays rate limited for the whole window"


async def test_help_works_again_after_the_cooldown_passes(
    scenario_factory, deps, clock
):
    from app.constants import Commands as constants

    scenario = await _panel(scenario_factory, deps)
    help_title = f"🙋 {HELP}"

    await scenario.click(HELP)
    clock.advance(constants.VIEW_ACTION_COOLDOWN_SECONDS + 1)
    await scenario.click(HELP)

    assert len(_events_titled(scenario, help_title)) == 2, \
        "once the window passes, the button serves again — it never disappears"


async def test_stats_button_is_rate_limited_too(scenario_factory, deps, clock):
    scenario = await _panel(scenario_factory, deps)
    stats_title = ml(
        "commands.commands.commons.block-links-manager.stats.embed.title",
        locale="pt-br",
    )

    await scenario.click(STATS)
    await scenario.click(STATS)

    scenario.expect_message(title_contains=NOTICE_TITLE)
    assert len(_events_titled(scenario, stats_title)) == 1


async def test_navigation_buttons_have_no_cooldown(scenario_factory, deps, clock):
    """Add opens a flow, it does not answer with a message: hammering it must
    keep working (the dead-button regression) and never see the notice."""
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(ADD)
    await scenario.click(ADD)

    scenario.expect_modal()
    assert not _events_titled(scenario, NOTICE_TITLE)


async def test_the_panel_stays_fully_alive_through_a_rate_limited_click(
    scenario_factory, deps, clock
):
    scenario = await _panel(scenario_factory, deps)

    await scenario.click(HELP)
    await scenario.click(HELP)          # rate limited
    await scenario.click(ADD)           # the panel itself never suffers

    scenario.expect_modal()


@pytest.mark.parametrize("locale", ["pt-br", "en-us"])
def test_cooldown_notice_copy_exists_in_both_languages(locale):
    for key in ("buttons.cooldown.title", "buttons.cooldown.message"):
        text = ml(key, locale=locale)
        assert text and not text.startswith("buttons."), f"{key} missing for {locale}"
