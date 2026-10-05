"""A list edited from two panels keeps each change, and its subscriptions follow what is saved.

Broke as: a list was saved whole, from the copy each panel read when it opened,
with its items found by position, and the subscription of an item ran before
the save. With two panels open on one server, a streamer added on the first was
lost when the second removed another one, and a streamer removed on the first
came back when the second added or edited one, with no Twitch subscription
behind it; an edit whose save failed had already unsubscribed the youtuber it
replaced. The popular websites of block links were saved the same way, so a
website turned on in one panel was turned off again by the next click in the
other. Once a change was refused, the exceptions screen of block links showed
raw copy keys and the old list again, an addition after another panel's
Disable subscribed a streamer nobody saved, a Disable whose flag write failed
never let its streamers go, and an unpaused feature never subscribed again
what was saved while it was paused. Round 2 found the refusals reported as
failed commits, which ended the session's story while its panel stayed open;
a website turned on from two screens that both showed it off was turned off
again by the second click; a refusal whose new read failed left the panel
saving forever; the closed answer was a notice that deleted itself, leaving
the card with no word of why; and Pause, Unpause and Edit after another
panel's Disable reported success over no settings, Unpause turning the
feature on.

Shared behaviour: the list and choice commits of `GenericCogFeature` (add, edit
and remove an item, turn an option on or off, disable) and its pause, unpause
and edit, the subscription hooks `SubscriptionFeature` runs around them, the
adapter's new read after a refusal, the manager's answer to a refused commit,
the closed message and the session's story. Exposed by: notifications_twitch,
notifications_youtube_video and block_links, two admins editing the same
server's settings.

Guaranteed: a change is applied to the settings as they are saved when the
admin confirms, never to the copy their panel opened with: an addition is kept
beside any other change, a removed item never comes back, two websites turned
on from two panels both stay on, and a link is never listed twice. A change
that can no longer apply (the item changed or went away meanwhile) saves
nothing and redraws the screen with the saved list and a short notice; an
addition the saved list already holds says so with the duplicate copy; a
change after the feature was disabled elsewhere says the form is closed and
subscribes nothing. A subscription is taken before the item is saved, so a
failed lookup still saves nothing, and is let go again when the save then
fails; an unsubscribe is sent only once the removal, the replacement or the
Disable is saved, even when recording the flag fails, and an unpause
subscribes every saved item before the feature is on again. A refusal that
keeps the screen is not a failed commit: the story goes on after the
out-of-date answer or a duplicate on a heading's own screen, and ends
discarded once the feature is gone. Each website button asks for the
state its screen showed, so two panels turning one website on leave it on. A
new read that fails keeps the panel answering with what it had. The closed
answer is written inside the message, which loses its rows of buttons, and
Pause, Unpause and Edit after another panel's Disable save nothing and give
that answer too.
"""

import json

import pytest

from app.services import journey
from app.services.utils import ml
from tests.behavioral.golden.paths import block_links
from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths import notifications_youtube_video as youtube
from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
from tests.mocks.discord import create_guild, create_member

pytestmark = [
    pytest.mark.behavioral,
    pytest.mark.regression,
    pytest.mark.shared_contract("manager_form"),
]

LOCALE = "en-us"
LINK_FIELD = "Type the link or website"
ANSWER_FIELD = "Type my answer"
ENTRY_KEYS = {"value", "title", "style", "hidden", "_raw_value"}
HELPER = "556"


@pytest.fixture(autouse=True)
def outside_dev(deps):
    """The subscription hooks run only outside dev."""
    deps.bot.config.is_dev = lambda: False


@pytest.fixture
def story():
    """Every session story the journey published, read live at assert time."""
    seen = {}
    journey.clear()
    journey.set_publisher(lambda item: seen.__setitem__(item.session_id, item))
    journey.install()
    yield seen
    journey.clear()
    journey.set_publisher(None)


def _story_of(story, admin_id):
    return next(item for item in story.values() if str(item.user_id) == admin_id)


def _saved(deps, form):
    return deps.mongo_client.guild[form].find_one({"guild_id": GUILD_ID})


def _streamers(deps):
    document = _saved(deps, twitch.FORM)
    return [item["streamer"]["value"] for item in document["notifications"]["values"]]


def _youtubers(deps):
    document = _saved(deps, youtube.FORM)
    return [item["youtuber"]["value"] for item in document["notifications"]["values"]]


def _links(deps):
    document = _saved(deps, block_links.FORM)
    return [item["link"]["value"] for item in document["custom_links"]["values"]]


def _subscribed_streamers(deps):
    ids = {
        subscription["user_id"]
        for subscription in deps.twitch.get_subscriptions()["data"]
        if subscription["type"] == "stream.online"
    }
    return {login for login, user in deps.twitch._users.items() if user.id in ids}


def _said(scenario, key):
    copy = ml(key, locale=LOCALE)
    return any(copy in (event.get("content") or "") for event in scenario.outputs)


def _told_it_could_not_be_processed(scenario):
    title = ml("errors.command-generic-error.title", locale=LOCALE)
    return any(
        title in ((event.get("embed") or {}).get("title") or "")
        for event in scenario.outputs
    )


def _told_it_is_already_registered(scenario):
    title = ml("errors.item-already-registered.title", locale=LOCALE)
    return any(
        title in ((event.get("embed") or {}).get("title") or "")
        for event in scenario.outputs
    )


def _never_showed_a_copy_key(scenario):
    shown = json.dumps(scenario.outputs, ensure_ascii=False)
    return "errors.stale" not in shown and "errors.duplicate" not in shown


def _last_screen(scenario):
    drawn = [
        event for event in scenario.outputs
        if event.get("actor") == "bot" and event.get("components_v2")
    ]
    return json.dumps(drawn[-1], ensure_ascii=False)


def _closed_in_place(scenario):
    drawn = [
        event for event in scenario.outputs
        if event.get("actor") == "bot"
        and (event.get("components_v2") or event.get("embed"))
    ]
    last = json.dumps(drawn[-1], ensure_ascii=False)
    closed = ml("commands.form-notices.closed", locale=LOCALE)
    return closed in last and '"actionrow"' not in last


def _recorded(deps, form):
    return [
        event["event"]
        for event in deps.mongo_client.events[form].find({"guild_id": GUILD_ID})
    ]


def _flag(deps, form):
    moderations = deps.mongo_client.guild.moderations.find_one({"guild_id": GUILD_ID})
    return (moderations or {}).get(form)


async def _two_panels(scenario_factory, form):
    guild = create_guild()
    first_admin = create_member(guild, id=555, name="Tester")
    second_admin = create_member(guild, id=556, name="Helper")
    first = await scenario_factory(locale=LOCALE, guild=guild, user=first_admin).start_command(form)
    second = await scenario_factory(locale=LOCALE, guild=guild, user=second_admin).start_command(form)
    return first, second


async def _two_twitch_panels(scenario_factory, deps):
    twitch._known_streamers(deps)
    seed_document(deps, twitch.FORM, twitch.ENABLED)
    for login in ("gaules", "cellbit"):
        deps.twitch.subscribe_to_stream_online_event(deps.twitch._users[login].id)
    return await _two_panels(scenario_factory, twitch.FORM)


async def _two_exceptions_screens(scenario_factory, deps):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    first, second = await _two_panels(scenario_factory, block_links.FORM)
    for panel in (first, second):
        await panel.click("edit:group:exceptions")
    return first, second


async def _add_streamer(scenario, streamer):
    await scenario.click("add")
    await twitch._channel(scenario)
    await twitch._submit_streamer(scenario, streamer)
    await twitch._submit_messages(scenario)
    await scenario.click("done")


async def _remove(scenario, target):
    await scenario.click("remove")
    await scenario.select_option(target)


async def _remove_link(screen, target):
    await screen.click(f"remove_one:{target}")
    await screen.click(f"remove_item:{target}")


async def _add_link(screen, link):
    await screen.click("add:custom_links")
    await screen.submit_modal({LINK_FIELD: link})


async def test_a_streamer_added_on_one_panel_survives_a_removal_on_another(
    scenario_factory, deps,
):
    first, second = await _two_twitch_panels(scenario_factory, deps)

    await _add_streamer(first, "shroud")
    await _remove(second, "notifications$1")

    assert _streamers(deps) == ["gaules", "shroud"]


async def test_a_streamer_removed_on_one_panel_never_comes_back_from_another(
    scenario_factory, deps,
):
    first, second = await _two_twitch_panels(scenario_factory, deps)

    await _remove(first, "notifications$0")
    await _add_streamer(second, "shroud")

    assert _streamers(deps) == ["cellbit", "shroud"]
    assert _subscribed_streamers(deps) == {"cellbit", "shroud"}


async def test_an_edit_of_a_streamer_another_panel_removed_redraws_the_saved_list(
    scenario_factory, deps, story,
):
    first, second = await _two_twitch_panels(scenario_factory, deps)

    await _remove(first, "notifications$1")
    await second.click("section:notifications$1")
    await twitch._channel(second, "announcements")
    await second.click("done")

    assert _streamers(deps) == ["gaules"]
    assert _said(second, "commands.form-notices.stale")
    assert "cellbit" not in _last_screen(second) and "gaules" in _last_screen(second)
    told = _story_of(story, HELPER)
    assert not told.finished_at, "a refused change is not the end of the session"

    await _add_streamer(second, "shroud")

    assert _streamers(deps) == ["gaules", "shroud"], "the panel now edits the saved list"
    assert told.last_action == "added", "the story goes on with what the admin did next"


async def test_an_addition_after_another_panel_disabled_the_feature_subscribes_nothing(
    scenario_factory, deps, story,
):
    first, second = await _two_twitch_panels(scenario_factory, deps)

    await first.click("disable")
    await first.submit_confirmation()
    await _add_streamer(second, "shroud")

    assert _saved(deps, twitch.FORM) is None
    assert "shroud" not in _subscribed_streamers(deps)
    told = _story_of(story, HELPER)
    assert told.result == "discarded"
    assert not [line for line in told.lines if line["message"].startswith("❌")]
    assert _closed_in_place(second), "the message itself says the form is closed"


async def test_a_save_that_fails_after_the_subscription_lets_the_new_streamer_go(
    scenario_factory, deps,
):
    twitch._known_streamers(deps)
    seed_document(deps, twitch.FORM, twitch.ENABLED)
    scenario = await scenario_factory(locale=LOCALE).start_command(twitch.FORM)
    collection = deps.mongo_client.guild[twitch.FORM]

    def unavailable(*args, **kwargs):
        raise RuntimeError("the database did not answer")

    collection.update_one = unavailable
    await _add_streamer(scenario, "shroud")
    del collection.update_one

    assert _streamers(deps) == ["gaules", "cellbit"]
    assert "shroud" not in _subscribed_streamers(deps)
    assert _told_it_could_not_be_processed(scenario)


async def test_a_link_removed_on_both_exceptions_screens_redraws_the_saved_list(
    scenario_factory, deps,
):
    first, second = await _two_exceptions_screens(scenario_factory, deps)

    await _remove_link(first, "custom_links$0")
    await _remove_link(second, "custom_links$0")

    assert _links(deps) == ["docs.example.org"]
    assert _never_showed_a_copy_key(second)
    assert _said(second, "commands.form-notices.stale")
    assert "meusite.com.br" not in _last_screen(second)
    assert "docs.example.org" in _last_screen(second)


async def test_a_link_added_on_both_exceptions_screens_is_already_registered(
    scenario_factory, deps, story,
):
    first, second = await _two_exceptions_screens(scenario_factory, deps)

    await _add_link(first, "spam.example.com")
    await _add_link(second, "spam.example.com")

    assert _links(deps) == ["meusite.com.br", "docs.example.org", "spam.example.com"]
    assert _never_showed_a_copy_key(second)
    assert _told_it_is_already_registered(second)
    assert "spam.example.com" in _last_screen(second)
    assert not _story_of(story, HELPER).finished_at, "the screen stays, so does its story"


async def test_a_link_edited_onto_a_link_already_listed_is_refused(
    scenario_factory, deps,
):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    scenario = await scenario_factory(locale=LOCALE).start_command(block_links.FORM)
    await scenario.click("edit:group:exceptions")

    await scenario.click("edit:custom_links$1")
    await scenario.submit_modal({LINK_FIELD: "meusite.com.br"})

    assert _links(deps) == ["meusite.com.br", "docs.example.org"]
    assert _told_it_is_already_registered(scenario)


async def test_two_websites_turned_on_from_two_panels_both_stay_on(
    scenario_factory, deps,
):
    first, second = await _two_exceptions_screens(scenario_factory, deps)

    await first.click("Spotify")
    await second.click("Twitter")

    saved = _saved(deps, block_links.FORM)["allowed_links"]
    assert saved == {
        "style": "bullet",
        "values": ["youtube.com", "twitch.tv", "spotify.com", "twitter.com"],
    }


async def test_a_website_turned_on_from_two_panels_stays_on(scenario_factory, deps):
    first, second = await _two_exceptions_screens(scenario_factory, deps)

    await first.click("Spotify")
    await second.click("Spotify")

    saved = _saved(deps, block_links.FORM)["allowed_links"]
    assert saved["values"] == ["youtube.com", "twitch.tv", "spotify.com"], (
        "each click asks for the state its screen showed, so both turn it on"
    )


async def test_a_refusal_whose_new_read_fails_keeps_the_panel_answering(
    scenario_factory, deps,
):
    from app.settings.features.notifications_twitch import FEATURE

    first, second = await _two_twitch_panels(scenario_factory, deps)
    await _remove(first, "notifications$1")
    await second.click("section:notifications$1")
    await twitch._channel(second, "announcements")

    async def unreadable(context):
        raise RuntimeError("the database did not answer")

    FEATURE.open = unreadable
    await second.click("done")
    del FEATURE.open

    assert _said(second, "commands.form-notices.stale")
    await _add_streamer(second, "shroud")
    assert _streamers(deps) == ["gaules", "shroud"], "the panel never stays saving"


@pytest.mark.parametrize(
    "seeded, action",
    [(block_links.ENABLED, "pause"), (block_links.PAUSED, "unpause")],
    ids=["pause", "unpause"],
)
async def test_a_pause_after_another_panel_disabled_the_feature_changes_nothing(
    scenario_factory, deps, seeded, action,
):
    seed_document(deps, block_links.FORM, seeded)
    first, second = await _two_panels(scenario_factory, block_links.FORM)

    await first.click("disable")
    await first.submit_confirmation()
    await second.click(action)
    await second.submit_confirmation()

    assert _saved(deps, block_links.FORM) is None
    assert _recorded(deps, block_links.FORM) == ["disabled"]
    assert _flag(deps, block_links.FORM) is False, "no feature turned on with no settings"
    assert _closed_in_place(second)


async def test_an_edit_after_another_panel_disabled_the_feature_changes_nothing(
    scenario_factory, deps,
):
    seed_document(deps, block_links.FORM, block_links.ENABLED)
    first, second = await _two_panels(scenario_factory, block_links.FORM)

    await first.click("disable")
    await first.submit_confirmation()
    await second.click("section:link_settings")
    await second.click("customize:2")
    await second.submit_modal({ANSWER_FIELD: "No links around here!"})
    await second.click("done")

    assert _saved(deps, block_links.FORM) is None
    assert _recorded(deps, block_links.FORM) == ["disabled"]
    assert _closed_in_place(second)


async def test_a_removal_whose_save_fails_never_unsubscribes_the_youtuber(
    scenario_factory, deps,
):
    youtube._known_channels(deps)
    seed_document(deps, youtube.FORM, youtube.TWO_ENTRIES)
    scenario = await scenario_factory(locale=LOCALE).start_command(youtube.FORM)
    collection = deps.mongo_client.guild[youtube.FORM]

    def unavailable(*args, **kwargs):
        raise RuntimeError("the database did not answer")

    collection.update_one = unavailable
    await _remove(scenario, "notifications$0")

    assert deps.youtube.unsubscribe_calls == []
    del collection.update_one
    assert _youtubers(deps) == ["pewdiepie", "mrbeast"]


async def test_an_edit_whose_new_youtuber_fails_never_unsubscribes_the_old_one(
    scenario_factory, deps,
):
    youtube._known_channels(deps)
    seed_document(deps, youtube.FORM, youtube.ENABLED)
    scenario = await scenario_factory(locale=LOCALE).start_command(youtube.FORM)
    await scenario.click("section:notifications$0")
    await youtube._submit_youtuber(scenario, "mrbeast")
    look_up = deps.youtube.get_channel_id_from_username

    def only_the_old_name(username):
        if username.lower().lstrip("@") == "mrbeast":
            raise TimeoutError("YouTube did not answer")
        return look_up(username)

    deps.youtube.get_channel_id_from_username = only_the_old_name
    await scenario.click("done")

    assert _youtubers(deps) == ["pewdiepie"]
    assert deps.youtube.unsubscribe_calls == [], "the youtuber still followed stays subscribed"


async def test_disable_lets_every_streamer_go_even_when_the_flag_write_fails(
    scenario_factory, deps, monkeypatch,
):
    from app.settings.features import feature as feature_module

    twitch._known_streamers(deps)
    seed_document(deps, twitch.FORM, twitch.ENABLED)
    for login in ("gaules", "cellbit"):
        deps.twitch.subscribe_to_stream_online_event(deps.twitch._users[login].id)
    scenario = await scenario_factory(locale=LOCALE).start_command(twitch.FORM)

    def flag_write_fails(*args, **kwargs):
        raise RuntimeError("moderations did not answer")

    monkeypatch.setattr(feature_module, "set_feature_enabled", flag_write_fails)
    await scenario.click("disable")
    await scenario.submit_confirmation()

    assert _saved(deps, twitch.FORM) is None
    assert _subscribed_streamers(deps) == set()


async def test_unpausing_subscribes_every_saved_streamer_again(scenario_factory, deps):
    twitch._known_streamers(deps)
    seed_document(deps, twitch.FORM, twitch.PAUSED)
    deps.twitch.subscribe_to_stream_online_event(deps.twitch._users["gaules"].id)
    scenario = await scenario_factory(locale=LOCALE).start_command(twitch.FORM)

    await scenario.click("unpause")
    await scenario.submit_confirmation()

    assert _saved(deps, twitch.FORM)["enabled"] is True
    assert _subscribed_streamers(deps) == {"gaules", "cellbit"}


async def test_unpausing_subscribes_every_saved_youtuber_again(scenario_factory, deps):
    youtube._known_channels(deps)
    seed_document(deps, youtube.FORM, youtube.PAUSED)
    scenario = await scenario_factory(locale=LOCALE).start_command(youtube.FORM)

    await scenario.click("unpause")
    await scenario.submit_confirmation()

    assert _saved(deps, youtube.FORM)["enabled"] is True
    assert deps.youtube.subscribe_calls == ["UC-lHJZR3Gqxm24_Vd_AJ5Yw"]


async def test_a_list_is_written_only_while_it_is_saved_exactly_as_read(deps):
    """MongoDB compares an embedded document field by field and in order, and
    `{}` matches only an empty one: the compare-and-set must hold under it."""
    from app.data.cogs import update_cog_if_unchanged_async

    seed_document(deps, twitch.FORM, twitch.ENABLED)
    saved = _saved(deps, twitch.FORM)["notifications"]
    emptied = {"style": "composition", "values": []}
    reordered = {"values": saved["values"], "style": saved["style"]}

    assert not await update_cog_if_unchanged_async(
        GUILD_ID, twitch.FORM, "notifications", reordered, emptied
    )
    assert not await update_cog_if_unchanged_async(
        GUILD_ID, twitch.FORM, "notifications", {}, emptied
    )
    assert await update_cog_if_unchanged_async(
        GUILD_ID, twitch.FORM, "notifications", saved, emptied
    )
    assert _saved(deps, twitch.FORM)["notifications"] == emptied


async def test_an_item_change_keeps_the_shape_the_previous_release_reads(
    scenario_factory, deps,
):
    """v0.9.0 reads `{style, values}` and items of `{value, title, style, hidden,
    _raw_value}` entries, and rebuilds the whole list on its own saves: a change
    may add no key it does not know, at any level, or a rollback would lose it."""
    first, second = await _two_twitch_panels(scenario_factory, deps)
    seeded = set(_saved(deps, twitch.FORM))

    await _add_streamer(first, "shroud")
    await second.click("section:notifications$0")
    await twitch._channel(second, "announcements")
    await second.click("done")
    third = await scenario_factory(locale=LOCALE).start_command(twitch.FORM)
    await _remove(third, "notifications$1")

    document = _saved(deps, twitch.FORM)
    assert set(document) == seeded | {"updated_at"}
    assert set(document["notifications"]) == {"style", "values"}
    assert document["notifications"]["style"] == "composition"
    assert [set(item) for item in document["notifications"]["values"]] == [
        {"channel", "streamer", "notification_messages"}
    ] * 2
    assert all(
        set(entry) <= ENTRY_KEYS
        for item in document["notifications"]["values"]
        for entry in item.values()
    )
    assert _streamers(deps) == ["gaules", "shroud"]
