"""
Testes de integracao para notificacoes Twitch.

Estes testes verificam o fluxo completo de notificacoes,
desde o recebimento do webhook ate o envio da mensagem.
"""

import pytest
import discord
from datetime import datetime, timedelta, timezone
from app.services.notifications_twitch import (
    compose_notification_message,
    create_stream_notification_embed,
    handle_send_streamer_notification,
    handle_send_streamer_offline_notification,
    is_more_than_one_hour,
    parse_streamer_message,
)
from tests.mocks import create_guild, create_member
from tests.generators import notifications
from tests.helpers import assert_embed_contains, assert_embed_field
import asyncio
import logging
from redis.exceptions import ConnectionError as RedisConnectionError
from types import SimpleNamespace


class TestTwitchStreamOnline:
    """Testes de notificacao quando streamer fica online."""

    @pytest.mark.asyncio
    async def test_sends_notification_when_configured_streamer_goes_live(
        self, twitch_data, mongodb, guild, twitch, bot
    ):
        """
        Verifica que uma notificacao e enviada no canal configurado quando
        um streamer monitorado inicia uma live.

        Input: Streamer "gaules" online jogando CS2, guild configurado para notificar
        Output: Embed enviado com nome do jogo e titulo da stream
        """
        # Arrange
        channel = guild.text_channels[0]
        twitch.add_user("gaules", user_id="123")
        twitch.set_stream_online("gaules", game="CS2", title="LOUD vs FURIA!")

        await notifications.twitch(
            mongodb,
            guild_id=str(guild.id),
            streamers=[{
                "name": "gaules",
                "channel_id": str(channel.id),
                "messages": ["{streamer} esta ao vivo!"]
            }]
        )

        bot.get_guild.return_value = guild
        guild.get_channel = lambda id: channel if str(id) == str(channel.id) else None

        twitch_data.find_guilds.return_value = [{
            "guild_id": str(guild.id),
            "notifications": {
                "values": [{
                    "channel": {"value": str(channel.id)},
                    "streamer": {"value": "gaules"},
                    "notification_messages": {"value": "{streamer} esta ao vivo!"}
                }]
            }
        }]
        twitch_data.find_last_stream_date.return_value = None  # Primeira vez
        twitch_data.wait_for_stream_info.return_value = twitch.get_stream_info("gaules")

        # Act
        await handle_send_streamer_notification("gaules")

        # Assert
        channel.assert_message_sent()
        embed = channel.get_last_embed()
        assert embed is not None
        assert_embed_contains(embed, "CS2")
        twitch.assert_stream_checked("gaules")

        # Verify mocks were called with correct arguments
        twitch_data.wait_for_stream_info.assert_called_once_with("gaules")
        twitch_data.find_last_stream_date.assert_called_once_with("gaules")
        twitch_data.find_guilds.assert_called_once_with("gaules")
        twitch_data.save_notification.assert_called_once()
        twitch_data.update_last_stream_date.assert_called_once_with(
            "gaules", twitch.get_stream_info("gaules").get("started_at")
        )

    @pytest.mark.asyncio
    async def test_does_not_send_when_stream_info_not_found(
        self, twitch_data, mongodb, guild, twitch, bot
    ):
        """
        Verifica que nenhuma notificacao e enviada quando stream info nao e encontrado.

        Input: Streamer existe mas nao esta online (get_stream_info retorna None)
        Output: Nenhuma mensagem enviada
        """
        # Arrange
        channel = guild.text_channels[0]
        twitch.add_user("gaules", user_id="123")

        await notifications.twitch(
            mongodb,
            guild_id=str(guild.id),
            streamers=[{
                "name": "gaules",
                "channel_id": str(channel.id),
            }]
        )

        bot.get_guild.return_value = guild
        twitch_data.wait_for_stream_info.return_value = None

        # Act
        await handle_send_streamer_notification("gaules")

        # Assert
        channel.assert_no_message_sent()

        # When stream_info is None, should not proceed to find guilds or save
        twitch_data.wait_for_stream_info.assert_called_once_with("gaules")
        twitch_data.find_guilds.assert_not_called()
        twitch_data.find_last_stream_date.assert_not_called()
        twitch_data.save_notification.assert_not_called()


class TestTwitchDuplicatePrevention:
    """Testes de prevencao de notificacoes duplicadas."""

    @pytest.mark.asyncio
    async def test_does_not_send_duplicate_for_recent_stream(
        self, twitch_data, mongodb, guild, twitch, bot
    ):
        """
        Verifica que nao envia notificacao duplicada se a stream foi notificada
        recentemente (menos de 1 hora). Evita spam quando streamer reconecta.

        Input: Stream ja notificada ha 30 minutos, webhook dispara novamente
        Output: Nenhuma nova notificacao enviada (edita a existente)
        """
        # Arrange
        channel = guild.text_channels[0]
        twitch.add_user("gaules", user_id="123")
        twitch.set_stream_online("gaules", game="CS2")

        await notifications.twitch(
            mongodb,
            guild_id=str(guild.id),
            streamers=[{"name": "gaules", "channel_id": str(channel.id)}]
        )

        recent_time = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()

        bot.get_guild.return_value = guild
        guild.get_channel = lambda id: channel

        twitch_data.find_guilds.return_value = [{
            "guild_id": str(guild.id),
            "notifications": {"values": [{
                "streamer": {"value": "gaules"},
                "channel": {"value": str(channel.id)},
                "notification_messages": {"value": "{streamer} esta ao vivo!"}
            }]}
        }]
        twitch_data.find_last_stream_date.return_value = recent_time
        twitch_data.is_more_than_one_hour.return_value = False  # Menos de 1 hora

        # Act
        await handle_send_streamer_notification("gaules")

        # Assert - Nao deve ter enviado nova mensagem (editou a existente)
        assert len(channel.get_all_messages()) == 0

        # Verify deduplication flow was followed
        twitch_data.wait_for_stream_info.assert_called_once_with("gaules")
        twitch_data.find_last_stream_date.assert_called_once_with("gaules")
        twitch_data.is_more_than_one_hour.assert_called_once()
        twitch_data.save_notification.assert_not_called()
        twitch_data.update_last_stream_date.assert_not_called()


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
        result = is_more_than_one_hour(
            now.isoformat(),
            two_hours_ago.isoformat()
        )

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
        result = is_more_than_one_hour(
            now.isoformat(),
            thirty_min_ago.isoformat()
        )

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
        notification = {
            "notification_messages": {
                "value": "{streamer} esta ao vivo! {stream_link}"
            }
        }
        streamer = "gaules"

        # Act
        result = compose_notification_message(notification, streamer)

        # Assert
        assert "gaules" in result
        assert "twitch.tv/gaules" in result

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
        assert_embed_field(embed, "Game", "CS2")
        assert_embed_field(embed, "Streamer", "gaules")

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


class TestTwitchSubscriptionManagement:
    """Testes de gerenciamento de subscriptions."""

    def test_subscribe_returns_202_for_new_subscription(self, twitch):
        """
        Verifica que subscription nova retorna 202.

        Input: User ID sem subscription existente
        Output: Response com status 202
        """
        # Arrange
        twitch.add_user("gaules", user_id="123")

        # Act
        response = twitch.subscribe_to_stream_online_event("123")

        # Assert
        assert response.status_code == 202
        twitch.assert_subscribed("123", "stream.online")

    def test_subscribe_returns_409_for_duplicate(self, twitch):
        """
        Verifica que subscription duplicada retorna 409.

        Input: User ID ja com subscription
        Output: Response com status 409
        """
        # Arrange
        twitch.add_user("gaules", user_id="123")
        twitch.subscribe_to_stream_online_event("123")

        # Act
        response = twitch.subscribe_to_stream_online_event("123")

        # Assert
        assert response.status_code == 409

    def test_unsubscribe_removes_subscription(self, twitch):
        """
        Verifica que unsubscribe remove a subscription.

        Input: Subscription existente
        Output: Subscription removida, status 204
        """
        # Arrange
        twitch.add_user("gaules", user_id="123")
        twitch.subscribe_to_stream_online_event("123")

        # Act
        response = twitch.unsubscribe_from_stream_event("sub-online-123-1")

        # Assert
        assert response.status_code == 204
        twitch.assert_unsubscribed()


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
    (`app/services/notifications_twitch.py`), behind the Twitch webhook. Exposed by: the
    review, next to the claim the YouTube announcement already takes on its video.

    Guaranteed: before the fan-out, the notice claims the stream (its streamer and when it
    started) in Redis, as the YouTube announcement claims its video; a delivery that finds
    the claim taken posts nothing. The claim is taken only once the followers are read, so a
    delivery that could not read them (found in the next review: its claim held the live for
    a day) leaves the live to the next delivery. When Redis cannot answer, the live is still
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
    from app.services import notifications_twitch

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
        guild.preferred_locale = discord.Locale.american_english
        for channel in guild.text_channels:
            remember_sent_messages(channel)
    return SimpleNamespace(first=first, second=second)


def remember_sent_messages(channel):
    """Discord finds a message by its id whether it is given as text or as a number."""
    channel._fetch_message.side_effect = lambda message_id: next(
        (sent for sent in channel._sent_messages if str(sent.id) == str(message_id)), None
    )


def follow(deps, guild_id, *channels):
    deps.mongo_client.guild.notifications_twitch.insert_one({
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
    return [message for channel in guild.text_channels for message in channel._sent_messages]


def footers(guild):
    return [message.embeds[0].footer.text for message in notices(guild)]


def errors(caplog, flow="twitch_notification"):
    return [
        record for record in caplog.records
        if record.levelno >= logging.ERROR
        and getattr(getattr(record, "context", None), "flow", None) == flow
    ]


def refuse(channel):
    channel._send.side_effect = discord.Forbidden(
        SimpleNamespace(status=403, reason="Forbidden"), "Missing Permissions"
    )


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
    (`app/services/trace.py`, the generic part of the YouTube announcement), and the
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
    refuse(servers.first.text_channels[0])

    with caplog.at_level("INFO"):
        await handle_send_streamer_notification("gaules")

    assert len(notices(servers.second)) == 1
    refused = [event["guild_id"] for event in analytics_events
               if event["event"] == "value.blocked_by_permission"]
    assert refused == [str(servers.first.id)]
    assert errors(caplog) == [], "a missing permission is a warning, not an error"


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_an_unexpected_failure_is_an_error_with_its_context_and_the_next_still_hears(
    deps, servers, caplog
):
    follow(deps, servers.first.id, servers.first.text_channels[0].id)
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    servers.first.text_channels[0]._send.side_effect = RuntimeError("the embed is too long")

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
    assert [footer.count("🔴") for footer in written] == [1, 1]
    assert [footer.count("Duration") for footer in written] == [1, 1]
    assert all(footer.startswith("• 🔴 Offline | ⌛️ Duration: 2h") for footer in written)


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_the_status_speaks_the_servers_language(deps, servers):
    servers.second.preferred_locale = discord.Locale.brazil_portuguese
    follow(deps, servers.second.id, servers.second.text_channels[0].id)

    await handle_send_streamer_notification("gaules")
    assert footers(servers.second) == ["• 🟢 Ao vivo"]

    await handle_send_streamer_offline_notification("gaules")
    assert footers(servers.second)[0].startswith("• 🔴 Live encerrada | ⌛️ Duração: 2h")


@pytest.mark.behavioral
@pytest.mark.regression
@pytest.mark.shared_contract("fan_out")
async def test_the_offline_edit_skips_a_server_keiko_left_and_reaches_the_next(
    deps, servers, caplog
):
    follow(deps, servers.second.id, servers.second.text_channels[0].id)
    await handle_send_streamer_notification("gaules")
    follow(deps, GONE, 100)
    deps.mongo_client.notifications.notifications_twitch.insert_one({
        "guild_id": str(GONE), "channel_id": "100", "streamer": "gaules", "message_id": "1",
    })
    reordered = sorted(
        deps.mongo_client.guild.notifications_twitch._data,
        key=lambda document: document["guild_id"] != str(GONE),
    )
    deps.mongo_client.guild.notifications_twitch._data[:] = reordered

    with caplog.at_level("INFO"):
        await handle_send_streamer_offline_notification("gaules")

    assert footers(servers.second)[0].startswith("• 🔴 Offline")
    assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []


def field_names(embed):
    return [field.name for field in embed.fields]


@pytest.mark.unit
@pytest.mark.parametrize(
    "locale,names", [("en-us", ["Game", "Streamer"]), ("pt-br", ["Categoria", "Streamer"])]
)
def test_a_live_notice_names_its_fields_in_the_servers_language(twitch, locale, names):
    """A live or video notice names its fields in the language of the server it reaches.

    Reported in the v1 review of the loop and runtime PR: once the Twitch status footer came
    from the language files, four field names were still English written in Python: the
    Twitch notice's "Game" and "Streamer", the YouTube notice's "Description" and "Tags".

    Guaranteed: the field names come from `commands.commons.notifications-fields` in the
    locale the notice is written in, both locales carry them, and English reads as before.
    """
    twitch.add_user("gaules", user_id="123")
    twitch.set_stream_online("gaules", game="CS2", title="Live Test!")

    embed = create_stream_notification_embed(
        "gaules", twitch.get_stream_info("gaules"), twitch.get_user_info("gaules"), locale
    )

    assert field_names(embed) == names
