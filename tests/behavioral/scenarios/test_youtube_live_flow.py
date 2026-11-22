"""Behavioral scenarios: /notificações youtube lives, from the setup to the panel.

Guaranteed: the setup follows the channel it saves on YouTube's hub (typed with or
without `@`) and saves what the notifier reads, with the default live message filled
for that channel; the preview shows the live announcement drawn for the channel; an
edit follows the new channel and lets the old one go; removing the only channel asks
and turns the feature off; and the hub subscription of a channel stays while the
video command still follows it, and goes once no YouTube command does.

Runs with the production dev gate open (`config.is_dev()` false), so the
subscription hooks run against the recording MockYouTubeAPI; only YouTube's HTTP
boundary is fake.
"""

import json

import pytest

from app.core.utils import ml
from app.data import data, wait
from tests.behavioral.flows import youtube, youtube_live
from tests.behavioral.flows.common import GUILD_ID, open_manager, seed_document
from tests.behavioral.harness.copy_keys import copy_text

pytestmark = pytest.mark.behavioral

LOCALE = "pt-br"
PEWDIEPIE = "UC-lHJZR3Gqxm24_Vd_AJ5Yw"
MRBEAST = "UCX6OQ3DkcsbYNE6H8uQQuVA"


def _items():
    saved = wait(data().guild_cogs.find_cog_by_guild_id(GUILD_ID, youtube_live.FORM))
    return saved["notifications"]["values"]


def _sent(scenario):
    return [
        json.dumps(event, ensure_ascii=False)
        for event in scenario.outputs
        if event.get("kind") == "followup_send"
    ]


def _videos_follow_pewdiepie(deps):
    seed_document(deps, youtube.FORM, youtube.ENABLED)


async def test_the_setup_follows_the_channel_and_saves_what_the_notifier_reads(
    scenario_factory, deps, outside_dev
):
    scenario = await youtube_live.open_card(scenario_factory, deps, LOCALE)
    await youtube_live.pick_channel(scenario)
    await youtube_live.submit_youtuber(scenario, " @PewDiePie ")
    await scenario.click("done")
    await scenario.confirm()

    assert PEWDIEPIE in deps.youtube.subscribe_calls
    [item] = _items()
    assert item["youtuber"]["value"] == "pewdiepie"
    assert item["mention"]["_raw_value"] == "everyone"
    assert item["notification_messages"]["value"] == (
        "pewdiepie está ao vivo! {stream_link}"
    ), "the channel is written in and the live link waits for the live"
    await scenario.finish()


async def test_the_preview_shows_the_live_announcement_drawn_for_the_channel(
    scenario_factory, deps
):
    youtube_live.seed(deps)
    scenario = await scenario_factory(locale=LOCALE).start_command(youtube_live.FORM)

    await scenario.click("aside:preview:notifications$0")

    preview = _sent(scenario)[-1]
    title = ml("notifications_youtube_live.ui.announcement.samples.title", LOCALE)
    assert title in preview
    assert "https://www.youtube.com/@pewdiepie/live" in preview
    await scenario.finish()


async def test_editing_the_channel_follows_the_new_one_and_lets_the_old_go(
    scenario_factory, deps, outside_dev
):
    scenario = await open_manager(
        scenario_factory, deps, LOCALE, youtube_live.FORM, youtube_live.seed
    )

    await scenario.click("section:notifications$0")
    await youtube_live.submit_youtuber(scenario, "mrbeast")
    await scenario.click("done")

    assert [item["youtuber"]["value"] for item in _items()] == ["mrbeast"]
    assert MRBEAST in deps.youtube.subscribe_calls
    assert deps.youtube.unsubscribe_calls == [PEWDIEPIE]
    await scenario.finish()


async def test_removing_the_only_channel_asks_and_turns_lives_off(
    scenario_factory, deps, outside_dev
):
    scenario = await open_manager(
        scenario_factory, deps, LOCALE, youtube_live.FORM, youtube_live.seed
    )

    await scenario.click("remove_this:notifications$0")
    scenario.expect_message(
        title_contains=copy_text("commands.responses.remove.overrides.1.title", LOCALE)
    )
    await scenario.click("remove_items")

    scenario.expect_not_persisted("guild", youtube_live.FORM, {"guild_id": GUILD_ID})
    scenario.expect_message(
        title_contains=ml("commands.responses.disabled.title", locale=LOCALE)
    )


@pytest.mark.parametrize(
    "videos_follow, unsubscribed",
    [(True, []), (False, [PEWDIEPIE])],
    ids=["videos-still-follow", "no-youtube-command-follows"],
)
async def test_turning_lives_off_lets_the_hub_go_only_once_no_youtube_command_follows(
    scenario_factory, deps, outside_dev, videos_follow, unsubscribed
):
    def seed(deps_):
        youtube_live.seed(deps_)
        if videos_follow:
            _videos_follow_pewdiepie(deps_)

    scenario = await open_manager(scenario_factory, deps, LOCALE, youtube_live.FORM, seed)

    await scenario.click("disable")
    await scenario.click("delete_all")

    scenario.expect_not_persisted("guild", youtube_live.FORM, {"guild_id": GUILD_ID})
    assert deps.youtube.unsubscribe_calls == unsubscribed
