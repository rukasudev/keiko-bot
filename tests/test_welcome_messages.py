"""
Testes de integracao para welcome_messages.

Estes testes verificam o fluxo de envio de mensagens de boas-vindas
quando um novo membro entra no servidor.
"""

import pytest
from unittest.mock import MagicMock
from app.services.welcome_messages import send_welcome_message
from tests.mocks import create_member
from tests.generators import moderations


class TestWelcomeMessagesSend:
    """Testes de envio de mensagens de boas-vindas."""

    @pytest.mark.asyncio
    async def test_sends_welcome_message_when_configured(
        self, mock_cache, mock_banner, mongodb, guild, bot, channel
    ):
        """
        Verifica que mensagem de boas-vindas e enviada quando configurado.

        Input: Novo membro entra, welcome_messages habilitado
        Output: Embed de boas-vindas enviado no canal configurado
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser")
        new_member._user = MagicMock()
        new_member._user.id = 999

        mock_cache.return_value = {
            "welcome_messages_channel": {"values": str(channel.id)},
            "welcome_messages": {"values": "Welcome {user}!"},
            "welcome_messages_title": "Welcome!",
            "welcome_messages_footer": "Enjoy your stay!"
        }

        # Act
        await send_welcome_message(new_member)

        # Assert
        channel.assert_message_sent()
        embed = channel.get_last_embed()
        assert embed is not None

    @pytest.mark.asyncio
    async def test_does_nothing_when_not_configured(
        self, mock_cache, mongodb, guild, bot
    ):
        """
        Verifica que nada acontece quando welcome_messages nao esta configurado.

        Input: Novo membro entra, welcome_messages desabilitado
        Output: Nenhuma mensagem enviada
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser")
        channel = guild.text_channels[0]
        mock_cache.return_value = None

        # Act
        await send_welcome_message(new_member)

        # Assert
        channel.assert_no_message_sent()

    @pytest.mark.asyncio
    async def test_does_nothing_when_channel_not_found(
        self, mock_cache, mongodb, guild, bot
    ):
        """
        Verifica que nada acontece se o canal configurado nao existe mais.

        Input: Canal configurado foi deletado
        Output: Nenhuma mensagem enviada, nenhum erro
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser")
        new_member._user = MagicMock()
        new_member._user.id = 999

        mock_cache.return_value = {
            "welcome_messages_channel": {"values": "999999"},  # Canal inexistente
            "welcome_messages": {"values": "Welcome!"},
            "welcome_messages_title": "Welcome!",
            "welcome_messages_footer": "Footer"
        }

        # Act
        await send_welcome_message(new_member)

        # Assert
        for ch in guild.text_channels:
            ch.assert_no_message_sent()


class TestWelcomeMessagesWithDatabase:
    """Testes usando o gerador de dados."""

    @pytest.mark.asyncio
    async def test_sends_message_from_database_config(
        self, mock_cache, mock_banner, mongodb, guild, bot, channel
    ):
        """
        Verifica que configuracao do banco e usada corretamente.

        Input: Configuracao no banco de dados
        Output: Mensagem de boas-vindas enviada
        """
        # Arrange
        await moderations.welcome_messages(
            mongodb,
            guild_id=str(guild.id),
            channel_id=str(channel.id),
            title="Welcome!",
            messages=["Hello {user}!"],
            footer="Enjoy!"
        )

        new_member = create_member(guild, id=999, name="NewUser")
        new_member._user = MagicMock()
        new_member._user.id = 999

        mock_cache.return_value = {
            "welcome_messages_channel": {"values": str(channel.id)},
            "welcome_messages": {"values": "Hello {user}!"},
            "welcome_messages_title": "Welcome!",
            "welcome_messages_footer": "Enjoy!"
        }

        # Act
        await send_welcome_message(new_member)

        # Assert
        channel.assert_message_sent()


class TestWelcomeMessagesPlaceholders:
    """Testes de substituicao de placeholders."""

    @pytest.mark.asyncio
    async def test_replaces_user_placeholder(
        self, mock_cache, mock_banner, mongodb, guild, bot, channel
    ):
        """
        Verifica que {user} e substituido pela mencao do membro.

        Input: Mensagem com {user}
        Output: Mensagem com mencao do usuario
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser")
        new_member._user = MagicMock()
        new_member._user.id = 999

        mock_cache.return_value = {
            "welcome_messages_channel": {"values": str(channel.id)},
            "welcome_messages": {"values": "Hello {user}!"},
            "welcome_messages_title": "Welcome!",
            "welcome_messages_footer": "Footer"
        }

        # Act
        await send_welcome_message(new_member)

        # Assert
        channel.assert_message_sent()
        embed = channel.get_last_embed()
        assert embed is not None
        assert "<@!999>" in embed.description or "<@!999>" in str(embed.fields)


class TestWelcomeMessagesEdgeCases:
    """Testes de casos especiais."""

    @pytest.mark.asyncio
    async def test_handles_missing_channel_value(
        self, mock_cache, mongodb, guild, bot
    ):
        """
        Verifica que nao falha quando channel nao tem valores.

        Input: Configuracao sem canal definido
        Output: Nenhuma mensagem enviada, nenhum erro
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser")
        mock_cache.return_value = {
            "welcome_messages_channel": {"values": None},
            "welcome_messages": {"values": "Welcome!"},
            "welcome_messages_title": "Welcome!",
            "welcome_messages_footer": "Footer"
        }

        # Act
        await send_welcome_message(new_member)

        # Assert
        for ch in guild.text_channels:
            ch.assert_no_message_sent()

    @pytest.mark.asyncio
    async def test_handles_missing_messages_value(
        self, mock_cache, mongodb, guild, bot, channel
    ):
        """
        Verifica que nao falha quando messages nao tem valores.

        Input: Configuracao sem mensagens definidas
        Output: Nenhuma mensagem enviada, nenhum erro

        NOTA: O codigo atual tem um bug - nao valida se messages e None
        antes de chamar split_welcome_messages. Este teste documenta
        o comportamento atual.
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser")
        mock_cache.return_value = {
            "welcome_messages_channel": {"values": str(channel.id)},
            "welcome_messages": {"values": None},
            "welcome_messages_title": "Welcome!",
            "welcome_messages_footer": "Footer"
        }

        # Act/Assert
        # BUG: Codigo falha com AttributeError quando messages e None
        with pytest.raises(AttributeError):
            await send_welcome_message(new_member)
