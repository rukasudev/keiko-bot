"""
Testes de integracao para notificacoes Twitch.

Estes testes verificam o fluxo completo de notificacoes,
desde o recebimento do webhook ate o envio da mensagem.
"""

import pytest
import discord
from datetime import datetime, timedelta, timezone
from app.services.notifications_twitch import (
    handle_send_streamer_notification,
    is_more_than_one_hour,
    compose_notification_message,
    parse_streamer_message,
    create_stream_notification_embed,
)
from tests.mocks import create_guild, create_member
from tests.generators import notifications
from tests.helpers import assert_embed_contains, assert_embed_field


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
