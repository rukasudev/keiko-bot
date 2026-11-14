"""Regressions: block_links redesign (2026-07-27).
"""
import pytest

from app.core.utils import ml
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from app.constants import Commands
from app.data.events import activity
from app.services import block_links
from app.core import analytics, cache
from tests.behavioral.regressions.test_event_loop_is_never_blocked import (
    blocking,
    MIN_TICKS,
    ticks_while,
)
from tests.mocks import create_message
import discord
from tests.generators.events import insert_blocked_link
from tests.generators.moderations import seed_cog

pytestmark = [pytest.mark.behavioral, pytest.mark.regression]

GUILD_ID = "123456789"


@pytest.mark.shared_contract("manager_form")
async def test_manager_add_works_on_documents_without_the_composition_envelope(
        scenario_factory, deps):
    """What broke (caught before release by the consumer contract): when
    block_links joined COMPOSITION_COMMANDS_LIST, Manager.handle_add_item_button
    did a raw `cogs[composition_key]["values"]` lookup that raised KeyError
    for every legacy/empty document, so the manager panel crashed on open.

    Guaranteed behavior: the manager renders (with the Add button) for any
    composition command whose document lacks the composition envelope."""
    from app.services.block_links import normalize_block_links_config

    legacy = {"guild_id": GUILD_ID, "enabled": True,
              "allowed_links": ["Youtube"], "answer": "x"}
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", normalize_block_links_config(legacy)
    )
    scenario.expect_message(kind="send", ephemeral=True)
    scenario.expect_component(label_or_action=ml("buttons.add.label", locale="pt-br"))

    raw_without_envelope = {"guild_id": "2", "enabled": True}
    bare = await scenario_factory(locale="pt-br").start_manager(
        "block_links", raw_without_envelope
    )
    bare.expect_message(kind="send", ephemeral=True)


@pytest.mark.shared_contract("form_engine")
def test_options_label_mapping_keeps_boolean_gates_untouched():
    """What broke: nothing — this pins the boundary of the label<->raw
    mapping added for styled options steps. Gate steps with `style: boolean`
    (birthday register_now and block_links add_custom when it was written)
    must keep persisting raw True/False, or their conditions and boolean
    rendering would break.

    Since beta.2 the birthday gate is gone (bd-3), so the contract runs on a
    fixture form with the same hidden gate before a list.

    Guaranteed behavior: a boolean-styled gate answer stays raw, the list it
    gates is skipped, and the review never lists the gate."""
    from app.settings.form import events as ev
    from app.settings.form.form import decide
    from app.settings.form.form_state import Origin, Setup, new_session
    from app.settings.form.responses.responses import to_document
    from tests.forms.form.gate_fixtures import gated_list

    definition = gated_list()
    session = new_session(
        (definition.key, definition.version),
        Setup(),
        Origin(GUILD_ID, "555", "pt-br"),
        ttl_seconds=60,
    )
    opened = decide(definition, session, ev.Started("e1"))
    gate = decide(definition, opened.session, ev.Answered("e2", None, "form"))
    later = decide(definition, gate.session, ev.Answered("e3", None, "add_now", "False"))

    answer = later.session.raw("add_now")
    assert answer in (False, "False"), f"boolean gate must persist raw, got {answer!r}"
    assert later.session.cursor == "confirm", "the gated list is skipped"
    document = to_document(definition.steps, later.session.answers, "pt-br")
    assert document["add_now"] == {"style": "boolean", "values": False}


@pytest.mark.shared_contract("form_engine")
async def test_failed_validation_in_first_composition_step_does_not_skip_it(
        scenario_factory):
    """What broke (found by the behavioral suite during the redesign): when
    the FIRST step of a composition failed validation, the on-screen buttons
    still belonged to the OUTER form; clicking them saved an EMPTY
    composition and skipped ahead, abandoning the sub-form and the user's
    retry path. Shared fix in Form._handle_after_step (composition guard).

    Guaranteed behavior: after a failed entry the retry re-opens the entry
    modal, with what was typed, instead of skipping the composition. Since
    beta.2 (tw-10) the retry is the Edit of the card that shows the refusal."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")           # Configurar the exceptions
    await scenario.click("customize:6")           # the list's Add (bl-7)
    await scenario.submit_modal({"Digite o link ou site": "não é link"})

    scenario.expect_step("link")
    await scenario.click("customize:0")           # retry from the refusal
    scenario.expect_modal(title_contains="Link ou Site")
    defaults = [field.default for field in scenario.store.pending_modal.children]
    assert defaults == ["não é link"], defaults
    await scenario.finish()


@pytest.mark.shared_contract("manager_form")
async def test_edit_dropdown_names_a_composition_entry_by_its_configuration_title():
    """What broke (reported from a live Discord test): the Editar dropdown of
    block_links listed a saved link as the option label, formatted through the
    `code` style. Discord never renders markdown inside a select, so the user
    saw literal backticks (`` `twitch.tv/jway` ``) where a configuration name
    belonged.

    Shared behavior affected: the edit picker options
    (app/settings/form/actions/manage.py, edit_options), consumed by every command with
    a composition (block_links today, twitch/youtube/birthday through the
    same screen).

    Guaranteed behavior: the option label is the configuration title with its
    position, the stored value identifies the entry in the option description,
    and no markdown leaks into either."""
    from app.settings.form.form_yaml import registry
    from app.settings.form.responses.summary import edit_options

    cogs = {
        "enabled": True,
        "mode": "block_all",
        "custom_links": {
            "style": "composition",
            "values": [
                {
                    "link": {
                        "value": "twitch.tv/jway",
                        "title": "Link ou Site",
                        "style": "code",
                    },
                    "match_type": {
                        "value": "🌐 Todos os links desse site",
                        "_raw_value": "domain",
                    },
                }
            ],
        },
    }

    options = edit_options(registry.get("block_links"), cogs, "pt-br")
    entry = next(
        option for option in options if option.value.startswith("custom_links$")
    )

    assert "`" not in entry.label, (
        f"markdown must never reach a select option label, got {entry.label!r}"
    )
    assert entry.label == "Links personalizados permitidos #1", (
        f"the label must name the configuration, got {entry.label!r}"
    )
    assert entry.description == "twitch.tv/jway", (
        "the stored value must still identify the entry, in the description"
    )


@pytest.mark.shared_contract("form_engine")
async def test_two_changes_in_a_row_on_the_same_screen_both_save(
        scenario_factory, deps):
    """What broke (reported from a live Discord test, 2026-09-16): on the
    Exceptions screen the first popular website turned on, and every click
    after it did nothing at all — the screen then refused even Voltar.

    The adapter reported every commit back to the engine as the event
    `commit:<session id>`, and the engine drops an event it has already seen.
    Until a commit closed the session, an id per session was unique enough; a
    screen that stays open commits many times, so the second outcome was
    dropped as a duplicate and the session stayed COMMITTING, which rejects
    every later click as busy.

    Shared behavior affected: Runtime._commit (app/settings/discord/
    entrypoints.py), the single path that feeds a commit's outcome back into
    the engine for every command.

    Guaranteed behavior: consecutive changes on a screen that keeps itself
    each save, and the screen goes on answering afterwards."""
    from tests.behavioral.golden.paths.block_links import ENABLED
    from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
    from tests.behavioral.harness.locators import clickable_items, codec_target

    seed_document(deps, "block_links", ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")
    await scenario.click("edit:group:exceptions")

    await scenario.click("turn_on:allowed_links=spotify.com")
    await scenario.click("turn_on:allowed_links=twitter.com")

    saved = scenario.get_persisted("guild", "block_links", {"guild_id": GUILD_ID})
    assert saved["allowed_links"]["values"] == [
        "youtube.com", "twitch.tv", "spotify.com", "twitter.com"
    ], "every click on the screen must save, not only the first"

    await scenario.click("back")
    busy = ml("commands.form-notices.busy", locale="pt-br")
    said_busy = [
        event for event in scenario.outputs
        if busy and busy in (event.get("content") or "")
    ]
    assert not said_busy, "a screen that saved must not answer the next click busy"
    targets = [codec_target(item) for item in clickable_items(scenario.current_message)]
    assert not [
        target for target in targets
        if (target or "").startswith(("turn_on:", "turn_off:"))
    ], (
        "Voltar must leave the screen and draw the panel"
    )
    await scenario.finish()


@pytest.mark.shared_contract("form_engine")
async def test_a_change_that_keeps_its_screen_never_says_it_is_thinking(
        scenario_factory, deps):
    """What broke (same live test): every popular website clicked left an
    ephemeral "Keiko is thinking..." message stuck on screen forever.

    The executor defers with the thinking state before an `edit` commit,
    because the edit of a step ends in a final message that answers it. A
    change that keeps its screen writes under the same kind and answers by
    editing the message instead, so nothing ever resolved the thinking state.

    Shared behavior affected: Executor._execute (app/settings/discord/
    executor.py), which runs the Commit effect of every command.

    Guaranteed behavior: a quiet commit answers its click without ever
    showing the thinking state."""
    from tests.behavioral.golden.paths.block_links import ENABLED
    from tests.behavioral.golden.paths.common import seed_document

    seed_document(deps, "block_links", ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command("block_links")
    await scenario.click("edit:group:exceptions")

    await scenario.click("turn_on:allowed_links=spotify.com")

    thinking = [
        event for event in scenario.outputs
        if event["kind"] == "defer" and event.get("thinking")
    ]
    assert not thinking, (
        f"a click that redraws its own screen must not say it is thinking: "
        f"{thinking}"
    )
    await scenario.finish()


COG = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_roles": {"values": []},
    "allowed_chats": {"values": []},
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "custom_links": {"style": "composition", "values": []},
    "answer": "Nada de links por aqui!",
}


def _bm(key, locale="pt-br"):
    return ml(f"commands.commands.commons.block-links-manager.{key}", locale=locale)


def keyspace_walks(deps, monkeypatch):
    """Every SCAN or KEYS the Redis client receives, still answered."""
    walks = []
    for method in ("scan_iter", "keys"):
        original = getattr(deps.redis_client, method)

        def walk(*args, _original=original, _method=method, **kwargs):
            walks.append((_method, args))
            return _original(*args, **kwargs)

        monkeypatch.setattr(deps.redis_client, method, walk)
    return walks


def seed_record(user_id="111", host="spam-site.com"):
    insert_blocked_link({
        "guild_id": GUILD_ID,
        "user_id": user_id,
        "channel_id": "100",
        "message_id": "500",
        "link": f"https://{host}/promo",
        "host": host,
        "mode": "block_all",
        "reason": "blocked-no-rule",
        "rule": None,
        "match": None,
        "deleted": True,
    })


async def stats_description(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(COG)
    )
    await scenario.click(ml("buttons.stats.label", locale="pt-br"))
    stats = scenario.expect_message(title_contains=ml("commands.commands.commons.stats.title.block-links", locale="pt-br"))
    await scenario.finish()
    return (stats.get("embed") or {}).get("description") or ""


async def leave(guild_id):
    """Keiko is removed from a server: the gateway event `Events.on_guild_remove` handles."""
    from app.cogs.events import Events

    await Events(SimpleNamespace(user=SimpleNamespace(id=99), guilds=[])).on_guild_remove(
        SimpleNamespace(id=int(guild_id), owner=None, owner_id=313)
    )


@pytest.mark.shared_contract("config_cache")
async def test_the_stats_total_is_the_servers_lifetime_and_redis_holds_no_count(
    deps, mock_cache, channel, member, monkeypatch, scenario_factory
):
    """Broke as: every blocked link was counted three times by block links itself, in a
    Redis hash per server (`guild:{g}:block_links:counters`, kept 400 days), in the loose
    keys of v0.9.0 the Stats still read, and in `guild.block_links_totals`; the Stats
    summed the hash and the loose keys, and leaving a server deleted them by name.

    Shared behaviour: the analytics sink, the only writer of a number (`analytics.*`),
    and the Stats aside of block links. Consumer that exposed it: block links (database
    PR 2, "No feature increments a counter").

    Guaranteed: a blocked link writes no counter of its own, in Redis or Mongo; the
    Stats' all-time total is the server's lifetime count the sink keeps, one more for
    the link just blocked; the top sites
    and the top member come from the records of the last 90 days; nothing walks the
    keyspace."""
    deps.mongo_client.analytics.lifetime.insert_one(
        {"_id": GUILD_ID, "features": {"block_links": {"value_count": 6}}}
    )
    seed_record(user_id="222", host="old.com")
    mock_cache.return_value = dict(COG)
    await block_links.check_message(
        GUILD_ID, create_message(channel, member, "https://spam-site.com")
    )
    analytics.flush()
    walks = keyspace_walks(deps, monkeypatch)

    description = await stats_description(scenario_factory)

    assert walks == [], f"the Stats walked the keyspace: {walks}"
    assert deps.redis_client.keys(f"guild:{GUILD_ID}:block_links:*") == []
    assert "block_links_totals" not in deps.mongo_client.guild.list_collection_names()
    assert f"**{_bm('stats.fields.total')}:** 7" in description
    assert "`spam-site.com` (1)" in description and "`old.com` (1)" in description


@pytest.mark.shared_contract("config_cache")
async def test_the_sites_and_the_member_come_from_the_last_90_days_of_blocks(
    deps, scenario_factory
):
    """Database PR 2 (D5): a blocked link is a `link.blocked` activity kept 90 days,
    and the Stats count its sites and members from those, in Mongo, never by reading
    every block of the server. Guaranteed: a block older than the window names no
    site and no member, and the newest ones are counted by site and by member."""
    old = datetime.now(timezone.utc) - timedelta(days=120)
    insert_blocked_link({
        "guild_id": GUILD_ID, "user_id": "999", "host": "ancient.com",
        "deleted": True, "created_at": old,
    })
    for user_id in ("111", "111", "222"):
        seed_record(user_id=user_id, host="spam-site.com")

    description = await stats_description(scenario_factory)

    assert "ancient.com" not in description and "<@999>" not in description
    assert "`spam-site.com` (3)" in description
    assert "<@111> (2)" in description
    assert f"**{_bm('stats.fields.recent')}:** 3" in description


@pytest.mark.shared_contract("config_cache")
async def test_forgetting_a_servers_settings_never_walks_the_keyspace(deps, monkeypatch):
    """The settings cache forgets its own keys, by name."""
    seed_cog(deps.mongo_client, "block_links", dict(COG))
    cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY)
    walks = keyspace_walks(deps, monkeypatch)

    cache.remove_all_cache_by_guild(GUILD_ID)

    assert walks == [], f"leaving a server walked the keyspace: {walks}"
    assert deps.redis_client.get(f"cache:cog:{GUILD_ID}:block_links") is None


@pytest.mark.shared_contract("config_cache")
async def test_recording_a_blocked_link_never_freezes_the_bot(
    deps, mock_cache, channel, member, monkeypatch
):
    """Every blocked message in every server writes its record."""
    mock_cache.return_value = dict(COG)
    monkeypatch.setattr("app.core.analytics.record", blocking())
    message = create_message(channel, member, "https://spam-site.com")

    ticks = await ticks_while(block_links.check_message(GUILD_ID, message))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times while the record was written"
    )


@pytest.mark.shared_contract("config_cache")
async def test_the_stats_never_freeze_the_bot(deps, monkeypatch):
    monkeypatch.setattr(activity, "count_by_field", blocking([]))
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)

    from app.settings.features import feature_for
    from tests.mocks.discord import create_guild

    guild = create_guild(id=int(GUILD_ID))
    interaction = SimpleNamespace(
        guild=guild,
        guild_id=int(GUILD_ID),
        user=SimpleNamespace(id=555),
        locale="pt-BR",
        followup=SimpleNamespace(send=send),
    )
    stats = feature_for(Commands.BLOCK_LINKS_KEY).asides()["stats"]

    ticks = await ticks_while(stats.handler(interaction, []))

    assert ticks >= MIN_TICKS
    assert sent and _bm("stats.empty") in sent[0]["embed"].description


PINGING_COG = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_roles": {"values": []},
    "allowed_chats": {"values": []},
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "custom_links": {"style": "composition", "values": []},
    "answer": "{user}, nada de links por aqui! @everyone <@&999>",
}


async def test_the_answer_pings_the_member_and_everyone_the_admin_wrote(
    mock_cache, channel, member
):
    """The block links answer pings who the admin wrote, and its error keeps no member's link.

    Broke as: the answer was sent with discord.py's default mentions, so a stricter
    default for the whole bot (PR5 sets one) would have silenced the `@everyone`, the
    role and the member an admin wrote into it; its placeholder was filled by its own
    `str.replace` instead of the filler every notifier shares; and when Keiko could
    not delete a message, the error context carried the member's links themselves,
    query strings and tokens included, to the error channel and `guild.logs`.

    Shared behaviour: `block_links.check_message` (every message of every server with
    block links on), `fill_placeholders` and the error context of a failed delete.
    Consumer that exposed it: block_links (the review of v1 round 1, I4, I5 and S6).

    Guaranteed: the answer names the member through `{user}` and is sent with every
    mention allowed, the ones the admin wrote included; the error context of a
    failed delete names the blocked websites and how many links there were, never a
    link.
    """
    mock_cache.return_value = dict(PINGING_COG)
    message = create_message(channel, member, "https://spam-site.com")

    await block_links.check_message(GUILD_ID, message)

    sent = channel._send.await_args.kwargs
    assert sent["content"] == f"{member.mention}, nada de links por aqui! @everyone <@&999>"
    mentions = sent["allowed_mentions"]
    assert (mentions.everyone, mentions.users, mentions.roles) == (True, True, True)


async def test_a_failed_delete_names_the_websites_never_the_links(
    mock_cache, channel, member, monkeypatch
):
    mock_cache.return_value = dict(PINGING_COG)
    message = create_message(
        channel,
        member,
        "https://spam-site.com/convite?token=s3cr3t https://spam-site.com/b https://x.io/c",
    )
    message._delete.side_effect = discord.Forbidden(
        type("Response", (), {"status": 403, "reason": "Forbidden"})(),
        {"code": 50013, "message": "Missing Permissions"},
    )
    logged = []
    monkeypatch.setattr(
        block_links.logger, "error", lambda *args, **kwargs: logged.append(kwargs)
    )

    with pytest.raises(discord.Forbidden):
        await block_links.check_message(GUILD_ID, message)

    extra = logged[0]["context"].extra
    assert extra["blocked_hosts"] == ["spam-site.com", "x.io"]
    assert extra["blocked_count"] == 3
    copied = {
        key: value for key, value in extra.items()
        if key != "message_preview" and "s3cr3t" in str(value)
    }
    assert copied == {}, f"the error context copied a member's link: {copied}"
