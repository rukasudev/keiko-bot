"""Regressions: block_links redesign (2026-07-27).
"""
import pytest

from app.services.utils import ml
from datetime import datetime, timezone
from types import SimpleNamespace
from app.constants import Commands
from app.data import block_links as block_links_data
from app.services import block_links, cache
from tests.behavioral.regressions.test_event_loop_is_never_blocked import (
    blocking,
    MIN_TICKS,
    ticks_while,
)
from tests.helpers.assertions import keys_in, values_in
from tests.mocks import create_message
import discord

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
async def test_options_label_mapping_keeps_boolean_gates_untouched(scenario_factory):
    """What broke: nothing — this pins the boundary of the label<->raw
    mapping added for styled options steps. Gate steps with `style: boolean`
    (birthday register_now, block_links add_custom) must keep persisting raw
    True/False, or their conditions and boolean rendering would break.

    Guaranteed behavior: a boolean-styled gate answer stays raw."""
    from tests.behavioral.scenarios.test_reminders_birthday_flow import (
        _complete_global_card,
    )

    scenario = await scenario_factory(locale="pt-br").start("reminders_birthday")
    await scenario.confirm()
    await _complete_global_card(scenario)
    await scenario.click("Depois")                # register_now = False (raw)

    gate = scenario.answers["register_now"]
    assert gate in (False, "False"), f"boolean gate must persist raw, got {gate!r}"
    await scenario.finish()


@pytest.mark.shared_contract("form_engine")
async def test_failed_validation_in_first_composition_step_does_not_skip_it(
        scenario_factory):
    """What broke (found by the behavioral suite during the redesign): when
    the FIRST step of a composition failed validation, the on-screen buttons
    still belonged to the OUTER form; clicking them saved an EMPTY
    composition and skipped ahead, abandoning the sub-form and the user's
    retry path. Shared fix in Form._handle_after_step (composition guard).

    Guaranteed behavior: re-clicking the gate after a failed entry re-opens
    the entry modal instead of skipping the composition."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("done")
    await scenario.click("Sim")
    await scenario.submit_modal({"Digite o link ou site": "não é link"})

    await scenario.click("Sim")                   # retry through the gate
    scenario.expect_modal(title_contains="Link ou Site")
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
    assert entry.label == "Seus Links #1", (
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


COUNTERS = f"guild:{GUILD_ID}:block_links:counters"


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
    block_links_data.insert_blocked_link({
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


def seed_v090_counters(deps, total, hosts, users):
    """The loose keys v0.9.0 wrote, and still writes while it runs."""
    deps.redis_client.set(f"guild:{GUILD_ID}:block_links:total", str(total))
    for host, count in hosts.items():
        deps.redis_client.set(f"guild:{GUILD_ID}:block_links:host:{host}", str(count))
    for user, count in users.items():
        deps.redis_client.set(f"guild:{GUILD_ID}:block_links:user:{user}", str(count))


async def stats_description(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", dict(COG)
    )
    await scenario.click(ml("buttons.stats.label", locale="pt-br"))
    stats = scenario.expect_message(title_contains=_bm("stats.embed.title"))
    await scenario.finish()
    return (stats.get("embed") or {}).get("description") or ""


async def leave(guild_id):
    """Keiko is removed from a server: the gateway event `Events.on_guild_remove` handles."""
    from app.cogs.events import Events

    await Events(SimpleNamespace(user=SimpleNamespace(id=99), guilds=[])).on_guild_remove(
        SimpleNamespace(id=int(guild_id), owner=SimpleNamespace(id=313, mention="<@313>"))
    )


def keikos_record(deps):
    """Keiko's own count of what it blocked, by document id, without the clock."""
    return {
        document["_id"]: {key: value for key, value in document.items() if key != "updated_at"}
        for document in deps.mongo_client.guild.block_links_totals.find({})
    }


@pytest.mark.shared_contract("config_cache")
async def test_a_blocked_link_is_counted_in_one_hash_of_its_server(
    deps, mock_cache, guild, channel, member
):
    """Block links counts what it blocked per server and for Keiko, read without a keyspace walk.

    Broke as: every blocked link added three loose Redis keys that never expire
    (`guild:{g}:block_links:total`, `:host:{host}`, `:user:{member}`), one more for
    every new website and every new member; the Stats button found them with a
    `SCAN` of the whole Redis keyspace, every server's keys included, and leaving a
    server deleted its keys with a `KEYS guild:{g}:*`. Both walks grow with the
    whole bot and block Redis while they run. The record of each blocked link was
    also written to Mongo synchronously in `check_message`, on the event loop that
    answers every message of every server. And nothing counted what Keiko blocked
    across servers: the numbers lived only per server, in Redis, and left with it.

    Shared behaviour: `cache.remove_all_cache_by_guild` (every server Keiko leaves),
    the Redis helpers of `app/services/cache.py`, and the block_links enforcement,
    Stats and leave. Consumer that exposed it: block_links (the review of v1,
    findings 3.6 and 3.12).

    Guaranteed: a blocked link adds to one hash per server, in one round trip that
    also keeps the hash 400 days after the server's last block (it names the server
    and its members, so it does not live forever), and to Keiko's own record in Mongo
    (`guild.block_links_totals`: the total and each site, with no server and no
    member, kept for good), off the loop; the Stats read that hash plus the
    counters v0.9.0 wrote and still writes (found by name, never by a walk), so no
    number drops at the deploy; no Stats and no leaving ever walks the keyspace;
    leaving a server deletes its own counters, hash and old keys, by name, and keeps
    Keiko's record.
    """
    mock_cache.return_value = dict(COG)
    message = create_message(
        channel, member, "https://spam-site.com e https://outro-site.com"
    )

    await block_links.check_message(GUILD_ID, message)

    assert deps.redis_client.hgetall(COUNTERS) == {
        "total": "2",
        "host:spam-site.com": "1",
        "host:outro-site.com": "1",
        f"user:{member.id}": "2",
    }
    assert deps.redis_client.keys(f"guild:{GUILD_ID}:block_links:*") == [COUNTERS], (
        "a blocked link still added loose keys"
    )


@pytest.mark.shared_contract("config_cache")
async def test_a_servers_counters_live_400_days_after_its_last_block(
    deps, mock_cache, guild, channel, member
):
    mock_cache.return_value = dict(COG)
    await block_links.check_message(
        GUILD_ID, create_message(channel, member, "https://spam-site.com")
    )
    deps.redis_client.expire(COUNTERS, 60)

    await block_links.check_message(
        GUILD_ID, create_message(channel, member, "https://outro-site.com")
    )

    assert deps.redis_client.ttl(COUNTERS) == Commands.BLOCK_LINKS_COUNTERS_TTL_SECONDS
    assert Commands.BLOCK_LINKS_COUNTERS_TTL_SECONDS == 60 * 60 * 24 * 400


def names_someone(key):
    """A field that would name a server, a member or another Discord object."""
    return key != "_id" and (
        key.endswith("_id") or key in {"guild", "server", "user", "member", "owner", "author"}
    )


class Colliding(datetime):
    """A clock whose microseconds hold both the second server's id and the member's,
    so a check that reads the record as text fails here every run, not once in a while."""

    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 10, 6, 1, 2, 3, 987111, tzinfo=timezone.utc)


@pytest.mark.shared_contract("config_cache")
async def test_a_blocked_link_is_counted_in_keikos_own_record_too(
    deps, mock_cache, guild, channel, member, monkeypatch
):
    """Broke as (rehearsal 3, the test itself): the record was read as text, and once in
    about a hundred runs a timestamp's microseconds (911189) held the member's id, 111.
    The record is now read field by field, under a clock that collides on purpose."""
    monkeypatch.setattr(block_links, "datetime", Colliding)
    mock_cache.return_value = dict(COG)
    await block_links.check_message(
        GUILD_ID, create_message(channel, member, "https://spam-site.com/a e https://outro-site.com")
    )
    mock_cache.return_value = {**COG, "guild_id": "987"}
    await block_links.check_message(
        "987", create_message(channel, member, "https://spam-site.com/b")
    )

    assert keikos_record(deps) == {
        "total": {"_id": "total", "blocked": 3},
        "site:spam-site.com": {"_id": "site:spam-site.com", "host": "spam-site.com", "blocked": 2},
        "site:outro-site.com": {"_id": "site:outro-site.com", "host": "outro-site.com", "blocked": 1},
    }
    documents = list(deps.mongo_client.guild.block_links_totals.find({}))
    fields = [key for key in keys_in(documents) if names_someone(key)]
    assert fields == [], "Keiko's record has a field that names a server or a member"
    identities = {GUILD_ID, "987", str(member.id), int(GUILD_ID), 987, member.id}
    named = [value for value in values_in(documents) if value in identities]
    assert named == [], "Keiko's record names a server or a member"
    assert all(isinstance(document["updated_at"], datetime) for document in documents)


@pytest.mark.shared_contract("config_cache")
async def test_the_numbers_v090_counted_are_still_counted(
    deps, mock_cache, guild, channel, member, scenario_factory
):
    seed_v090_counters(
        deps, total=5, hosts={"spam-site.com": 4, "old.com": 1}, users={"222": 5}
    )
    seed_record(user_id="222", host="spam-site.com")
    seed_record(user_id="222", host="old.com")
    mock_cache.return_value = dict(COG)

    await block_links.check_message(
        GUILD_ID, create_message(channel, member, "https://spam-site.com")
    )
    description = await stats_description(scenario_factory)

    assert f"**{_bm('stats.fields.total')}:** 6" in description
    assert "`spam-site.com` (5)" in description
    assert "<@222> (5)" in description


@pytest.mark.shared_contract("config_cache")
async def test_a_counter_whose_records_expired_is_found_by_its_name_in_the_hash(
    deps, scenario_factory
):
    """What the one-off fold of the deploy leaves: the name, at zero, in the hash."""
    seed_v090_counters(deps, total=50, hosts={"ancient.com": 50}, users={"333": 50})
    deps.redis_client.hincrby(COUNTERS, "host:ancient.com", 0)
    deps.redis_client.hincrby(COUNTERS, "user:333", 0)

    description = await stats_description(scenario_factory)

    assert f"**{_bm('stats.fields.total')}:** 50" in description
    assert "`ancient.com` (50)" in description
    assert "<@333> (50)" in description


@pytest.mark.shared_contract("config_cache")
async def test_the_stats_never_walk_the_keyspace(deps, monkeypatch, scenario_factory):
    seed_record()
    deps.redis_client.hincrby(COUNTERS, "total", 3)
    deps.redis_client.hincrby(COUNTERS, "host:spam-site.com", 3)
    deps.redis_client.hincrby(COUNTERS, "user:111", 3)
    for other in range(20):
        deps.redis_client.set(f"guild:{other}:block_links:host:x.com", "1")
    walks = keyspace_walks(deps, monkeypatch)

    description = await stats_description(scenario_factory)

    assert walks == [], f"the Stats walked the keyspace: {walks}"
    assert f"**{_bm('stats.fields.total')}:** 3" in description
    assert "`spam-site.com` (3)" in description


@pytest.mark.shared_contract("config_cache")
async def test_forgetting_a_servers_settings_never_walks_the_keyspace(deps, monkeypatch):
    """The settings cache forgets its own keys; the counters are block links' to delete."""
    deps.mongo_client.guild["block_links"].insert_one(dict(COG))
    cache.get_cog_data_or_populate(GUILD_ID, Commands.BLOCK_LINKS_KEY)
    deps.redis_client.hincrby(COUNTERS, "total", 3)
    walks = keyspace_walks(deps, monkeypatch)

    cache.remove_all_cache_by_guild(GUILD_ID)

    assert walks == [], f"leaving a server walked the keyspace: {walks}"
    assert deps.redis_client.get(f"guild:{GUILD_ID}:cog.block_links") is None
    assert deps.redis_client.hgetall(COUNTERS) == {"total": "3"}


@pytest.mark.shared_contract("config_cache")
async def test_keiko_leaving_a_server_deletes_its_counters_by_name_and_keeps_keikos_record(
    deps, monkeypatch
):
    deps.redis_client.hincrby(COUNTERS, "total", 3)
    deps.redis_client.hincrby(COUNTERS, "host:spam-site.com", 3)
    deps.redis_client.hincrby(COUNTERS, "user:111", 3)
    seed_v090_counters(deps, total=7, hosts={"old.com": 7}, users={"222": 7})
    seed_record(user_id="222", host="old.com")
    other = "guild:987:block_links:host:spam-site.com"
    deps.redis_client.set(other, "4")
    deps.mongo_client.guild.block_links_totals.insert_one({"_id": "total", "blocked": 10})
    deps.mongo_client.guild.block_links_totals.insert_one(
        {"_id": "site:old.com", "host": "old.com", "blocked": 7}
    )
    before = keikos_record(deps)
    walks = keyspace_walks(deps, monkeypatch)

    await leave(GUILD_ID)

    assert walks == [], f"leaving a server walked the keyspace: {walks}"
    left = deps.redis_client.keys(f"guild:{GUILD_ID}:block_links:*")
    assert left == [], f"the server's counters outlived it: {left}"
    assert deps.redis_client.get(other) == "4", "another server's counter was deleted"
    assert keikos_record(deps) == before, "leaving changed Keiko's own record"


@pytest.mark.shared_contract("config_cache")
async def test_a_failed_read_on_leave_still_deletes_the_hash_and_the_total(deps, monkeypatch):
    """The names of the other loose keys need the records; the hash and the total do not."""
    deps.redis_client.hincrby(COUNTERS, "total", 3)
    deps.redis_client.hincrby(COUNTERS, "host:spam-site.com", 3)
    seed_v090_counters(deps, total=7, hosts={}, users={})

    def mongo_is_down(*args, **kwargs):
        raise ConnectionError("mongo is down")

    monkeypatch.setattr(block_links_data, "find_blocked_links_by_guild", mongo_is_down)

    await leave(GUILD_ID)

    assert deps.redis_client.hgetall(COUNTERS) == {}, "the hash outlived the server"
    assert deps.redis_client.get(f"guild:{GUILD_ID}:block_links:total") is None


@pytest.mark.shared_contract("config_cache")
async def test_keiko_leaving_a_server_never_freezes_the_bot(deps, monkeypatch):
    monkeypatch.setattr(block_links_data, "find_blocked_links_by_guild", blocking([]))

    ticks = await ticks_while(leave(GUILD_ID))

    assert ticks >= MIN_TICKS


@pytest.mark.shared_contract("config_cache")
async def test_recording_a_blocked_link_never_freezes_the_bot(
    deps, mock_cache, channel, member, monkeypatch
):
    """Every blocked message in every server writes its record."""
    mock_cache.return_value = dict(COG)
    monkeypatch.setattr(block_links_data, "insert_blocked_link", blocking())
    message = create_message(channel, member, "https://spam-site.com")

    ticks = await ticks_while(block_links.check_message(GUILD_ID, message))

    assert ticks >= MIN_TICKS, (
        f"the loop only came back {ticks} times while the record was written"
    )


@pytest.mark.shared_contract("config_cache")
async def test_the_stats_never_freeze_the_bot(deps, monkeypatch):
    monkeypatch.setattr(
        block_links_data, "find_blocked_links_by_guild", blocking([])
    )
    sent = []

    async def send(**kwargs):
        sent.append(kwargs)

    interaction = SimpleNamespace(
        guild_id=int(GUILD_ID),
        locale="pt-BR",
        followup=SimpleNamespace(send=send),
    )

    ticks = await ticks_while(block_links.send_blocked_links_stats_message(interaction))

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
