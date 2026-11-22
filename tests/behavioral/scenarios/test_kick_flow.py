"""Behavioral scenarios: /notificações kick lives, from the setup to the panel.

Guaranteed: the setup subscribes on Kick the streamer it saves (typed with or without
`@`, in any capitals) and keeps the broadcaster and the subscription Kick gave, with
the default live message filled for that streamer; a name Kick does not know is
refused and nothing is subscribed; the preview shows the live announcement drawn for
the streamer; an edit subscribes the new streamer and lets the old one go; removing
the only streamer asks and turns Kick lives off; turning them off lets Kick's
subscription go only once no server Keiko is still in follows the streamer; turning
them on again subscribes the streamer anew only when Kick no longer holds its
subscription; and a Kick that does not answer the lookup shows the generic error.

Runs with the production dev gate open (`config.is_dev()` false), so the
subscription hooks run against the recording MockKickAPI; only Kick's client is fake.
"""

import json

import pytest

from app.core.utils import ml
from app.integrations.kick import KickAPIError
from app.data import data, wait
from tests.behavioral.flows import kick
from tests.behavioral.flows.common import GUILD_ID, open_manager, seed_document
from tests.behavioral.harness.copy_keys import copy_text
from tests.generators.moderations import recorded_server

pytestmark = pytest.mark.behavioral

LOCALE = "pt-br"
OTHER_GUILD = "987654321"


def _items():
    saved = wait(data().guild_cogs.find_cog_by_guild_id(GUILD_ID, kick.FORM))
    return saved["notifications"]["values"]


def _state(streamer):
    return wait(data().keiko_subscriptions.subscription_state("kick", streamer))


def _sent(scenario):
    return [
        json.dumps(event, ensure_ascii=False)
        for event in scenario.outputs
        if event.get("kind") == "followup_send"
    ]


def _subscribed(deps, streamer):
    """`streamer` subscribed on Kick the way a server adding it left it."""
    subscription_id = deps.kick.subscribe_live_status(kick.BROADCASTERS[streamer])
    wait(data().keiko_subscriptions.update_subscription_state(
        "kick", streamer, {
            "broadcaster_user_id": kick.BROADCASTERS[streamer],
            "subscription_id": subscription_id,
        },
    ))


def _seed_subscribed(deps):
    kick.seed(deps)
    _subscribed(deps, "xqcow")


async def test_the_setup_subscribes_the_streamer_and_saves_what_the_notifier_reads(
    scenario_factory, deps, outside_dev
):
    scenario = await kick.open_card(scenario_factory, deps, LOCALE)
    await kick.pick_channel(scenario)
    await kick.submit_streamer(scenario, " @xQcOW ")
    await scenario.click("done")
    await scenario.confirm()

    assert deps.kick.subscribed() == {kick.BROADCASTERS["xqcow"]}
    assert _state("xqcow") == {
        "broadcaster_user_id": kick.BROADCASTERS["xqcow"], "subscription_id": "sub-1",
    }
    [item] = _items()
    assert item["streamer"]["value"] == "xqcow"
    assert item["mention"]["_raw_value"] == "everyone"
    assert item["notification_messages"]["value"] == (
        "xqcow está ao vivo! https://kick.com/xqcow"
    ), "the streamer and its live link are written in"
    await scenario.finish()


async def test_a_name_kick_does_not_know_is_refused_and_nothing_is_subscribed(
    scenario_factory, deps, outside_dev
):
    scenario = await kick.open_card(scenario_factory, deps, LOCALE)
    await kick.pick_channel(scenario)

    await kick.submit_streamer(scenario, "naoexiste")

    scenario.expect_error(ml("errors.streamer_not_found.message", locale=LOCALE))
    assert deps.kick.subscribe_calls == []
    scenario.expect_not_persisted("guild", kick.FORM, {"guild_id": GUILD_ID})
    await scenario.finish()


async def test_the_preview_shows_the_live_announcement_drawn_for_the_streamer(
    scenario_factory, deps
):
    kick.seed(deps)
    scenario = await scenario_factory(locale=LOCALE).start_command(kick.FORM)

    await scenario.click("aside:preview:notifications$0")

    preview = _sent(scenario)[-1]
    title = ml("notifications_kick.ui.announcement.samples.title", LOCALE)
    assert title in preview, "nobody streams, so the example live is drawn"
    assert "https://kick.com/xqcow" in preview
    await scenario.finish()


async def test_editing_the_streamer_subscribes_the_new_one_and_lets_the_old_go(
    scenario_factory, deps, outside_dev
):
    scenario = await open_manager(
        scenario_factory, deps, LOCALE, kick.FORM, _seed_subscribed
    )

    await scenario.click("section:notifications$0")
    await kick.submit_streamer(scenario, "adinross")
    await scenario.click("done")

    assert [item["streamer"]["value"] for item in _items()] == ["adinross"]
    assert deps.kick.subscribed() == {kick.BROADCASTERS["adinross"]}
    assert _state("xqcow")["subscription_id"] is None
    await scenario.finish()


async def test_removing_the_only_streamer_asks_and_turns_kick_lives_off(
    scenario_factory, deps, outside_dev
):
    scenario = await open_manager(
        scenario_factory, deps, LOCALE, kick.FORM, _seed_subscribed
    )

    await scenario.click("remove_this:notifications$0")
    scenario.expect_message(
        title_contains=copy_text("commands.responses.remove.overrides.1.title", LOCALE)
    )
    await scenario.click("remove_items")

    scenario.expect_not_persisted("guild", kick.FORM, {"guild_id": GUILD_ID})
    scenario.expect_message(
        title_contains=ml("commands.responses.disabled.title", locale=LOCALE)
    )
    assert deps.kick.subscribed() == set()


@pytest.mark.parametrize(
    "follower, subscribed",
    [
        ("another-server", {kick.BROADCASTERS["xqcow"]}),
        ("a-server-keiko-left", set()),
        (None, set()),
    ],
    ids=["another-server-follows", "a-server-keiko-left-follows", "no-server-follows"],
)
async def test_turning_kick_lives_off_lets_the_subscription_go_only_once_no_server_follows(
    scenario_factory, deps, outside_dev, follower, subscribed
):
    """A server Keiko left still holds its document, but it follows nobody."""
    def seed(deps_):
        _seed_subscribed(deps_)
        if follower:
            seed_document(deps_, kick.FORM, {**kick.ENABLED, "guild_id": OTHER_GUILD})
        if follower == "a-server-keiko-left":
            recorded_server(deps_.mongo_client, OTHER_GUILD)
            wait(data().guild_settings.record_leave(OTHER_GUILD))

    scenario = await open_manager(scenario_factory, deps, LOCALE, kick.FORM, seed)

    await scenario.click("disable")
    await scenario.click("delete_all")

    scenario.expect_not_persisted("guild", kick.FORM, {"guild_id": GUILD_ID})
    assert deps.kick.subscribed() == subscribed


async def test_unpausing_subscribes_the_streamer_again_when_kick_lost_its_subscription(
    scenario_factory, deps, outside_dev
):
    """While every server following a streamer is paused, the daily resync lets its
    subscription go; the one Keiko still keeps is no longer on Kick."""
    def seed(deps_):
        kick.seed_paused(deps_)
        _subscribed(deps_, "xqcow")
        deps_.kick.delete_subscription(_state("xqcow")["subscription_id"])

    scenario = await open_manager(scenario_factory, deps, LOCALE, kick.FORM, seed)

    await scenario.click("unpause")

    assert deps.kick.subscribed() == {kick.BROADCASTERS["xqcow"]}
    held = {subscription.id for subscription in deps.kick.subscriptions()}
    assert _state("xqcow")["subscription_id"] in held
    await scenario.finish()


@pytest.mark.parametrize(
    "kept",
    ["the-one-kick-holds", None, "sub-kick-dropped"],
    ids=["keiko-kept-its-id", "keiko-kept-no-id", "keiko-kept-another-id"],
)
async def test_unpausing_keeps_the_one_subscription_kick_still_holds(
    scenario_factory, deps, outside_dev, kept
):
    """The only server following a streamer turns on again while Kick still holds its
    subscription: Kick keeps that one, Keiko names it, and no copy is made for the
    resync to drop.

    Broke as: Keiko asked Kick only about the id it kept, so a subscription Kick held
    under another id (a subscribe whose answer was lost) was subscribed again, and
    Kick sent every event of the streamer twice until the daily resync."""
    def seed(deps_):
        kick.seed_paused(deps_)
        _subscribed(deps_, "xqcow")
        if kept != "the-one-kick-holds":
            wait(data().keiko_subscriptions.update_subscription_state(
                "kick", "xqcow", {"subscription_id": kept}
            ))

    scenario = await open_manager(scenario_factory, deps, LOCALE, kick.FORM, seed)
    [held] = [subscription.id for subscription in deps.kick.subscriptions()]

    await scenario.click("unpause")

    assert [subscription.id for subscription in deps.kick.subscriptions()] == [held]
    assert _state("xqcow")["subscription_id"] == held
    await scenario.finish()


async def test_a_kick_outage_while_the_streamer_is_looked_up_shows_the_generic_error(
    scenario_factory, deps, outside_dev, monkeypatch
):
    """A Kick that does not answer is not a name it does not know: the card shows the
    generic error and the admin can try again, and nothing is subscribed."""
    scenario = await kick.open_card(scenario_factory, deps, LOCALE)
    await kick.pick_channel(scenario)

    def kick_is_down(slugs):
        raise KickAPIError("Kick's channels answered 500", status=500)

    monkeypatch.setattr(deps.kick, "channels", kick_is_down)
    await kick.submit_streamer(scenario, "xqcow")

    scenario.expect_error(ml("errors.generic.message", locale=LOCALE))
    assert deps.kick.subscribe_calls == []
    await scenario.finish()
