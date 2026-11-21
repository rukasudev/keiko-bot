"""A Twitch live reaches every server that follows the streamer: the notice, its
status and the admin's message, each server in its own language."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest
from dateutil import parser
from redis.exceptions import ConnectionError as RedisConnectionError

from app.commands.twitch import service as notifications_twitch
from app.commands.twitch.service import (
    compose_notification_message,
    create_stream_notification_embed,
    handle_send_streamer_notification,
    handle_send_streamer_offline_notification,
    is_more_than_one_hour,
    parse_streamer_message,
)
from app.core.utils import fill, ml
from app.data import data
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks import create_guild

ANNOUNCEMENT = "notifications_twitch.ui.announcement"


GONE = 4004


@pytest.fixture
def servers(deps):
    """Two servers following `gaules`, and the streamer live for two hours."""
    first = create_guild(id=5001, name="First", channels=["lives"])
    second = create_guild(id=5002, name="Second", channels=["streams", "more-streams"])
    known = {first.id: first, second.id: second}
    deps.bot.get_guild = lambda guild_id: known.get(int(guild_id))
    deps.twitch.add_user("gaules", user_id="123")
    deps.twitch.set_stream_online(
        "gaules", game="CS2", started_at=datetime.now(timezone.utc) - timedelta(hours=2)
    )
    for guild in known.values():
        recorded_server(deps.mongo_client, guild.id, language="en-us")
    return SimpleNamespace(first=first, second=second)


def follow(deps, guild_id, *channels):
    recorded_server(deps.mongo_client, guild_id)
    seed_cog(deps.mongo_client, "notifications_twitch", {
        "guild_id": str(guild_id),
        "enabled": True,
        "notifications": {"values": [
            {
                "streamer": {"value": "gaules"},
                "channel": {"value": str(channel)},
                "notification_messages": {"value": "{streamer} is live! {stream_link}"},
            }
            for channel in channels
        ]},
    })


def notices(guild):
    return [message for channel in guild.text_channels for message in channel.get_all_messages()]


def footers(guild):
    return [message.embeds[0].footer.text for message in notices(guild)]


def errors(caplog, flow="twitch_notification"):
    return [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == flow
    ]


def field(name, locale="en-us"):
    """A field name of the live notice, as the copy writes it."""
    return ml(f"{ANNOUNCEMENT}.fields.{name}", locale)


def footer(status, locale="en-us", duration=None):
    """The status line of a live notice, as the copy writes it."""
    line = ml(f"{ANNOUNCEMENT}.states.{status}", locale)
    if duration:
        line += " | " + fill(ml(f"{ANNOUNCEMENT}.states.duration", locale), duration=duration)
    return f"• {line}"


def started_at(deps):
    return parser.parse(deps.twitch.get_stream_info("gaules")["started_at"])


async def test_a_live_is_announced_in_the_followed_channel_and_remembered(deps, servers):
    """A new live is posted in the channel the server chose, with its category, and
    Keiko keeps the message and when the live started."""
    channel = servers.first.text_channels[0]
    follow(deps, servers.first.id, channel.id)

    await handle_send_streamer_notification("gaules")

    [notice] = notices(servers.first)
    assert (field("game"), "CS2") in [(f.name, f.value) for f in notice.embeds[0].fields]
    saved = await data().guild_messages.find_message(
        servers.first.id, channel.id, "notifications_twitch", "gaules"
    )
    assert saved["message_id"] == str(notice.id)
    state = await data().keiko_subscriptions.subscription_state("twitch", "gaules")
    assert state["last_live_started_at"] == started_at(deps)


async def test_a_live_twitch_never_shows_is_not_announced(deps, servers, monkeypatch):
    """When Twitch never shows the live, nothing is posted and nothing remembered."""
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    monkeypatch.setattr(notifications_twitch, "wait_for_stream_info", lambda streamer: None)

    await handle_send_streamer_notification("gaules")

    assert notices(servers.first) == []
    assert await data().keiko_subscriptions.subscription_state("twitch", "gaules") == {}


async def test_a_reconnect_within_the_hour_posts_no_second_notice(deps, servers):
    """A live that starts again less than an hour after the one announced is the
    same live: the notice is marked online again, never posted twice."""
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    earlier = started_at(deps) - timedelta(minutes=30)
    await data().keiko_subscriptions.update_subscription_state(
        "twitch", "gaules", {"last_live_started_at": earlier}
    )

    await handle_send_streamer_notification("gaules")

    assert notices(servers.first) == []
    state = await data().keiko_subscriptions.subscription_state("twitch", "gaules")
    assert state["last_live_started_at"] == earlier


class TestTwitchTimeValidation:
    """Testes da validacao de tempo entre streams."""

    def test_is_more_than_one_hour_returns_true(self):
        """
        Verifica que retorna True quando diferenca e maior que 1 hora.

        Input: Tempo atual e tempo de 2 horas atras
        Output: True
        """
        # Arrange
        now = datetime.now(timezone.utc)
        two_hours_ago = now - timedelta(hours=2)

        # Act
        result = is_more_than_one_hour(now, two_hours_ago)

        # Assert
        assert result is True

    def test_is_more_than_one_hour_returns_false(self):
        """
        Verifica que retorna False quando diferenca e menor que 1 hora.

        Input: Tempo atual e tempo de 30 minutos atras
        Output: False
        """
        # Arrange
        now = datetime.now(timezone.utc)
        thirty_min_ago = now - timedelta(minutes=30)

        # Act
        result = is_more_than_one_hour(now, thirty_min_ago)

        # Assert
        assert result is False


class TestTwitchMessageComposition:
    """Testes da composicao de mensagens de notificacao."""

    def test_compose_notification_message_replaces_placeholders(self):
        """
        Verifica que placeholders sao substituidos corretamente.

        Input: Mensagem com {streamer} e {stream_link}
        Output: Mensagem com valores reais
        """
        # Arrange
        notice = {"notification_messages": "{streamer} esta ao vivo! {stream_link}"}
        streamer = "gaules"

        # Act
        result = compose_notification_message(notice, streamer)

        # Assert
        assert result == "gaules esta ao vivo! https://www.twitch.tv/gaules"

    def test_compose_notification_message_adds_link_if_missing(self):
        """
        Verifica que link e adicionado se nao estiver no template.

        Input: Mensagem sem {stream_link}
        Output: Mensagem com link adicionado ao final
        """
        # Arrange
        message = "{streamer} esta ao vivo!"
        streamer = "gaules"
        stream_link = "https://www.twitch.tv/gaules"

        # Act
        result = parse_streamer_message(message, streamer, stream_link)

        # Assert
        assert "twitch.tv/gaules" in result

    def test_a_capitalised_stream_link_is_printed_as_typed_and_the_link_still_added(self):
        """A placeholder in capitals is text: it used to stop the link from being added."""
        stream_link = "https://www.twitch.tv/gaules"

        result = parse_streamer_message("{STREAM_LINK}", "gaules", stream_link)

        assert result == f"{{STREAM_LINK}}\n{stream_link}"


    @pytest.mark.parametrize(
        "written",
        [
            "https://twitch.tv/gaules",
            "https://www.twitch.tv/Gaules",
            "twitch.tv/gaules",
        ],
    )
    def test_a_message_that_already_links_the_streamer_gets_no_second_link(
        self, written
    ):
        """Broke as (tw-5, beta.2): the setup now writes the streamer's link into
        the suggested message, and a message holding the link but not
        `{stream_link}` got the link appended again on a line of its own."""
        message = f"@everyone gaules está ao vivo! {written}"

        result = parse_streamer_message(
            message, "gaules", "https://www.twitch.tv/gaules"
        )

        assert result == message

    def test_a_link_to_another_channel_still_gets_the_streamer_link(self):
        """Only the streamer's own channel counts as the live link."""
        stream_link = "https://www.twitch.tv/gaules"

        result = parse_streamer_message(
            "vem também https://twitch.tv/gaulesfan", "gaules", stream_link
        )

        assert result.endswith(f"\n{stream_link}")

class TestTwitchStreamEmbed:
    """Testes da criacao de embeds de notificacao."""

    def test_create_stream_notification_embed_has_required_fields(self, twitch):
        """
        Verifica que o embed tem todos os campos necessarios.

        Input: Informacoes de stream e usuario
        Output: Embed com titulo, game, streamer e thumbnail
        """
        # Arrange
        twitch.add_user("gaules", user_id="123")
        twitch.set_stream_online("gaules", game="CS2", title="Live Test!")

        stream_info = twitch.get_stream_info("gaules")
        user_info = twitch.get_user_info("gaules")

        # Act
        embed = create_stream_notification_embed("gaules", stream_info, user_info)

        # Assert
        assert embed is not None
        assert embed.title == "Live Test!"
        assert [(f.name, f.value) for f in embed.fields] == [
            (field("game"), "CS2"),
            (field("streamer"), "gaules"),
        ]

    def test_embed_has_correct_color(self, twitch):
        """
        Verifica que o embed tem cor roxa (Twitch).

        Input: Stream info valido
        Output: Embed com cor roxa
        """
        # Arrange
        twitch.add_user("streamer", user_id="123")
        twitch.set_stream_online("streamer")

        stream_info = twitch.get_stream_info("streamer")
        user_info = twitch.get_user_info("streamer")

        # Act
        embed = create_stream_notification_embed("streamer", stream_info, user_info)

        # Assert
        assert embed.color == discord.Color.purple()


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_two_deliveries_of_one_live_at_once_post_it_once(deps, servers):
    """A Twitch live is announced once, however many deliveries of it overlap.

    Reported in the v1 review of the loop and runtime PR: the live notice decides it is a new
    stream by the date of the last one, and writes that date only after the fan-out. Twitch
    delivers an event again when it is not sure the first delivery arrived, so two
    deliveries of one `stream.online` that overlapped both read the old date and both posted
    the notice in every following server.

    Shared behaviour: `handle_send_streamer_notification`
    (`app/commands/twitch/service.py`), behind the Twitch webhook. Exposed by: the
    review, next to the claim the YouTube announcement already takes on its video.

    Guaranteed: before the fan-out, the notice claims the stream (its streamer and when it
    started) in Redis, as the YouTube announcement claims its video; a delivery that finds
    the claim taken posts nothing. The claim comes before the followers are read and is given
    back when they cannot be read, so a delivery that could not read them (found in the next
    review: its claim held the live for a day) leaves the live to the next delivery. When Redis cannot answer, the live is still
    announced, with a warning, rather than missed.
    """
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)

    await asyncio.gather(
        handle_send_streamer_notification("gaules"),
        handle_send_streamer_notification("gaules"),
    )

    assert len(notices(servers.first)) == 1
    assert len(notices(servers.second)) == 1


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_a_delivery_that_could_not_read_its_followers_leaves_the_live_to_the_next(
    deps, servers, monkeypatch
):
    from app.commands.twitch import service as notifications_twitch

    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    read = notifications_twitch.following_guilds
    failures = iter([RuntimeError("Mongo hung up")])

    def following_guilds(streamer_name):
        for failure in failures:
            raise failure
        return read(streamer_name)

    monkeypatch.setattr(notifications_twitch, "following_guilds", following_guilds)

    with pytest.raises(RuntimeError):
        await handle_send_streamer_notification("gaules")
    await handle_send_streamer_notification("gaules")

    assert len(notices(servers.first)) == 1, "the second delivery posts the live"


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_a_live_is_announced_when_redis_cannot_say_whether_it_was(
    deps, servers, monkeypatch, caplog
):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)

    def down(*args, **kwargs):
        raise RedisConnectionError("Redis is down")

    monkeypatch.setattr(deps.redis_client, "set", down)

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.first)) == 1, "better twice than never"
    assert any(
        record.levelno == logging.WARNING and "**gaules**" in record.getMessage()
        for record in caplog.records
    )


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_a_live_already_claimed_never_reads_who_follows_the_streamer(
    deps, servers, monkeypatch
):
    """A delivery of a live another delivery already announces stops before the fan-out query.

    Reported in the database plan of v1: the live notice read every server following the
    streamer before it tried the claim, so each redelivery of one `stream.online` scanned
    the followers only to find the live already announced.

    Shared behaviour: `handle_send_streamer_notification`
    (`app/commands/twitch/service.py`), behind the Twitch webhook. Exposed by: the
    query review of the database plan, on the fan-out query.

    Guaranteed: the claim comes first; a delivery that finds it taken sends no query for
    the followers and posts nothing.
    """
    from app.commands.twitch import service as notifications_twitch

    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    started_at = deps.twitch.get_stream_info("gaules")["started_at"]
    assert notifications_twitch.claim_the_live("gaules", started_at)
    read = notifications_twitch.find_guilds_by_streamer_name
    queried = []

    def find_guilds_by_streamer_name(streamer_name):
        queried.append(streamer_name)
        return read(streamer_name)

    monkeypatch.setattr(
        notifications_twitch, "find_guilds_by_streamer_name", find_guilds_by_streamer_name
    )

    await handle_send_streamer_notification("gaules")

    assert notices(servers.first) == [], "the live was announced by the other delivery"
    assert queried == [], "no server list is read for a live already claimed"


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_a_server_keiko_left_does_not_stop_the_live_notice_of_the_next(
    deps, servers, caplog
):
    """One server Keiko cannot reach never stops a Twitch notice for the others.

    Reported in the v1 review: `process_notifications` posted a live to every following
    server in one loop with nothing between them, so a server Keiko had left
    (`bot.get_guild` returning None), a deleted channel or a 403 stopped the notice for
    every server after it; production holds a Twitch document still on in a guild Keiko
    left. The offline edit walked the same loop and reassigned `status` inside it, so a
    streamer followed in two channels got "🔴 🔴 offline | ⌛️ duration: 2h | ⌛️ Duration:
    2h" on the second message; that footer was English, written in Python.

    Shared behaviour: the per-server fan-out every notice now runs through
    (`app/core/trace.py`, the generic part of the YouTube announcement), and the
    Twitch notifier on top of it. Exposed by: the production guild Keiko left.

    Guaranteed: each server gets its notice and its status on its own; a server Keiko
    cannot reach is a warning, one that refuses is counted as a permission failure, and
    anything else is an error with its context, and the next server still hears. The
    status is written once per message, in the server's language, from the language files.
    """
    follow(deps, GONE, 100)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.second)) == 1, "the server after the missing one still hears"
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == [], (
        "a server Keiko left is a warning, not an error"
    )


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_a_server_that_refuses_the_notice_does_not_stop_the_next(
    deps, servers, analytics_events, caplog
):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    servers.first.text_channels[0].denied.add("send_messages")

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.second)) == 1
    refused = [event["guild_id"] for event in analytics_events
               if event["type"] == "value.blocked_by_permission"]
    assert refused == [str(servers.first.id)]
    assert errors(caplog) == [], "a missing permission is a warning, not an error"


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_an_unexpected_failure_is_an_error_with_its_context_and_the_next_still_hears(
    deps, servers, caplog, monkeypatch
):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    monkeypatch.setattr(
        servers.first.text_channels[0],
        "send",
        AsyncMock(side_effect=RuntimeError("the embed is too long")),
    )

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.second)) == 1
    failed = errors(caplog)
    assert [record.context.guild_id for record in failed] == [str(servers.first.id)]
    assert failed[0].exc_info is not None, "the traceback travels with the error"


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_a_streamer_followed_in_several_channels_gets_its_status_once_per_message(
    deps, servers
):
    follow(deps, servers.second.id, *(channel.id for channel in servers.second.text_channels))
    await handle_send_streamer_notification("gaules")

    await handle_send_streamer_offline_notification("gaules")

    written = footers(servers.second)
    assert len(written) == 2
    assert all(line.startswith(footer("offline", duration="2h")) for line in written)
    assert all(line.count(ml(f"{ANNOUNCEMENT}.states.offline", "en-us")) == 1 for line in written)


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_the_status_speaks_the_servers_language(deps, servers):
    recorded_server(deps.mongo_client, servers.second.id, language="pt-br")
    follow(deps, servers.second.id, servers.second.text_channels[0].id)

    await handle_send_streamer_notification("gaules")
    assert footers(servers.second) == [footer("online", "pt-br")]

    await handle_send_streamer_offline_notification("gaules")
    assert footers(servers.second)[0].startswith(footer("offline", "pt-br", duration="2h"))


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_the_offline_edit_skips_a_server_keiko_left_and_reaches_the_next(
    deps, servers, caplog
):
    follow(deps, GONE, 100)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    await handle_send_streamer_notification("gaules")
    await data().guild_messages.save_message(GONE, 100, "notifications_twitch", "gaules", 1)

    with caplog.at_level("INFO"):
        await handle_send_streamer_offline_notification("gaules")

    assert footers(servers.second)[0].startswith(footer("offline"))
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []


@pytest.mark.unit
@pytest.mark.parametrize("locale", ["en-us", "pt-br"])
def test_a_live_notice_names_its_fields_in_the_servers_language(twitch, locale):
    """A live or video notice names its fields in the language of the server it reaches.

    Reported in the v1 review of the loop and runtime PR: once the Twitch status footer came
    from the language files, four field names were still English written in Python: the
    Twitch notice's "Game" and "Streamer", the YouTube notice's "Description" and "Tags".

    Guaranteed: the field names come from the `announcement.fields` of its copy in the
    locale the notice is written in, both locales carry them, and English reads as before.
    """
    twitch.add_user("gaules", user_id="123")
    twitch.set_stream_online("gaules", game="CS2", title="Live Test!")

    embed = create_stream_notification_embed(
        "gaules", twitch.get_stream_info("gaules"), twitch.get_user_info("gaules"), locale
    )

    assert [name.name for name in embed.fields] == [
        field("game", locale),
        field("streamer", locale),
    ]


@pytest.mark.behavioral
@pytest.mark.shared_contract("fan_out")
async def test_a_streamer_saved_by_the_old_pause_is_still_announced(deps, servers):
    """N1 (round 3) took the per-item pause off: an item a dev panel saved with
    `enabled: false` is announced like any other, the field ignored."""
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, *(channel.id for channel in servers.second.text_channels))
    collection = deps.mongo_client.guild.cogs
    second = {"_id": f"{servers.second.id}:notifications_twitch"}
    stored = collection.find_one(second)
    stored["options"]["notifications"]["values"][0]["enabled"] = False
    collection.update_one(
        second, {"$set": {"options.notifications": stored["options"]["notifications"]}}
    )

    await handle_send_streamer_notification("gaules")

    assert len(notices(servers.first)) == 1
    assert all(
        len(channel.get_all_messages()) == 1 for channel in servers.second.text_channels
    )


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.parametrize("game", [None, ""], ids=["no-category", "empty-category"])
async def test_a_live_without_a_category_announces_no_category_field(deps, servers, game):
    """A live with no category is announced without a category field.

    Broke as: a live Twitch sent with no category (`game_name` missing, or empty) was
    announced with a "Game" / "Categoria" field reading "None", since the notice wrote
    its fields by hand and added every one whatever its value. Shared behaviour:
    `announcement_embed` (`app/ui/embed.py`), which leaves out a field whose value is
    empty. Consumer that exposed it: the Twitch live announcement. Guaranteed: such a
    live is announced with the streamer field alone, and a live with a category keeps
    both fields.
    """
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    deps.twitch.set_stream_online(
        "gaules", game=game, started_at=datetime.now(timezone.utc) - timedelta(hours=2)
    )

    await handle_send_streamer_notification("gaules")

    [notice] = notices(servers.first)
    fields = [(shown.name, shown.value) for shown in notice.embeds[0].fields]
    assert fields == [(field("streamer"), "gaules")]
