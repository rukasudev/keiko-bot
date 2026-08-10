"""
Testes de integracao para block_links.

Estes testes verificam o fluxo completo de verificacao de links,
desde a deteccao ate a acao de moderacao.
"""

import pytest
from app.services.block_links import check_message
from tests.mocks import create_message, create_member
from tests.generators import moderations


class TestBlockLinksCheckMessage:
    """Testes de verificacao de links em mensagens."""

    @pytest.mark.asyncio
    async def test_deletes_message_with_blocked_link(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que mensagem com link bloqueado e deletada.

        Input: Mensagem com link de site nao permitido, block_links habilitado
        Output: Mensagem deletada, aviso enviado no canal
        """
        # Arrange
        await moderations.block_links(
            mongodb,
            guild_id=str(guild.id),
            allowed_links=["twitter"],
            allowed_roles=[],
            allowed_channels=[],
            answer="Links nao sao permitidos aqui!"
        )

        msg = create_message(channel, member, "Check this https://spam-site.com")
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": ["twitter"],
            "answer": "Links nao sao permitidos aqui!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_deleted()
        channel.assert_message_sent()

    @pytest.mark.asyncio
    async def test_allows_message_from_allowed_role(
        self, mock_cache, mongodb, guild, bot, channel, admin_member
    ):
        """
        Verifica que mensagens de usuarios com role permitido nao sao deletadas.

        Input: Mensagem com link de usuario com role Admin, Admin na lista permitida
        Output: Mensagem NAO deletada
        """
        # Arrange
        msg = create_message(channel, admin_member, "Check this https://spam-site.com")
        mock_cache.return_value = {
            "allowed_roles": {"values": ["200"]},  # Admin role ID
            "allowed_chats": {"values": []},
            "allowed_links": [],
            "answer": "Links nao sao permitidos!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_not_deleted()

    @pytest.mark.asyncio
    async def test_allows_message_in_allowed_channel(
        self, mock_cache, mongodb, guild, bot, member
    ):
        """
        Verifica que mensagens em canais permitidos nao sao deletadas.

        Input: Mensagem com link em canal permitido
        Output: Mensagem NAO deletada
        """
        # Arrange
        allowed_channel = guild.text_channels[0]
        msg = create_message(allowed_channel, member, "Check https://spam-site.com")
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": [str(allowed_channel.id)]},
            "allowed_links": [],
            "answer": "Links nao sao permitidos!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_not_deleted()

    @pytest.mark.asyncio
    async def test_allows_twitter_link_when_configured(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que links do Twitter sao permitidos quando configurado.

        Input: Mensagem com link do Twitter, Twitter na lista de permitidos
        Output: Mensagem NAO deletada
        """
        # Arrange
        msg = create_message(channel, member, "Check https://twitter.com/user")
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": ["twitter"],
            "answer": "Links nao sao permitidos!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        # Permitir um site permite os links reais dele (match por dominio),
        # nao apenas a homepage.
        msg.assert_not_deleted()

    @pytest.mark.asyncio
    async def test_answer_mentions_author_with_user_placeholder(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """{user} na resposta vira a mencao do autor da mensagem."""
        msg = create_message(channel, member, "olha https://spam-site.com")
        mock_cache.return_value = {
            "mode": "block_all",
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": {"values": []},
            "answer": "Sem links, {user}!",
        }

        await check_message(str(guild.id), msg)

        msg.assert_deleted()
        sent = channel._sent_messages[-1]
        assert member.mention in str(sent.content)

    @pytest.mark.asyncio
    async def test_blocks_schemeless_spam_link(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """Spam sem http(s):// tambem e detectado (dominio.tld/caminho)."""
        msg = create_message(channel, member, "corre em bit.ly/golpe")
        mock_cache.return_value = {
            "mode": "allow_all",
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "custom_links": {"values": [
                {"link": {"value": "bit.ly/golpe"},
                 "match_type": {"_raw_value": "exact"}},
            ]},
            "answer": "Nada disso!",
        }

        await check_message(str(guild.id), msg)

        msg.assert_deleted()

    @pytest.mark.asyncio
    async def test_allow_all_mode_only_blocks_listed_links(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """No modo permitir-todos, links comuns passam livres."""
        msg = create_message(channel, member, "veja https://youtube.com/watch?v=abc")
        mock_cache.return_value = {
            "mode": "allow_all",
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "custom_links": {"values": [
                {"link": {"value": "spam.com"},
                 "match_type": {"_raw_value": "domain"}},
            ]},
            "answer": "Nada disso!",
        }

        await check_message(str(guild.id), msg)

        msg.assert_not_deleted()

    @pytest.mark.asyncio
    async def test_single_exempt_role_scalar_still_exempts(
        self, mock_cache, mongodb, guild, bot, channel, admin_member
    ):
        """Regressao: 1 cargo unico era persistido como escalar e a isencao
        virava interseccao char a char (silenciosamente inerte)."""
        msg = create_message(channel, admin_member, "https://spam-site.com")
        mock_cache.return_value = {
            "mode": "block_all",
            "allowed_roles": {"values": "200"},   # escalar, como persistido
            "allowed_chats": {"values": []},
            "allowed_links": {"values": []},
            "answer": "Sem links!",
        }

        await check_message(str(guild.id), msg)

        msg.assert_not_deleted()

    @pytest.mark.asyncio
    async def test_does_nothing_when_block_links_disabled(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que nada acontece quando block_links esta desabilitado.

        Input: Mensagem com link, block_links NAO configurado
        Output: Mensagem NAO deletada
        """
        # Arrange
        msg = create_message(channel, member, "Check https://spam-site.com")
        mock_cache.return_value = None

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_not_deleted()

    @pytest.mark.asyncio
    async def test_does_nothing_when_no_links_in_message(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que mensagens sem links nao sao afetadas.

        Input: Mensagem sem links
        Output: Mensagem NAO deletada
        """
        # Arrange
        msg = create_message(channel, member, "Hello world, no links here!")
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": [],
            "answer": "Links nao sao permitidos!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_not_deleted()

    @pytest.mark.asyncio
    async def test_sends_custom_answer_when_blocking(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que a mensagem customizada e enviada ao bloquear.

        Input: Mensagem com link bloqueado, mensagem customizada configurada
        Output: Mensagem customizada enviada no canal
        """
        # Arrange
        custom_answer = "Ei! Links nao sao permitidos neste servidor."
        msg = create_message(channel, member, "Check https://spam.com")
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": [],
            "answer": custom_answer
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_deleted()
        channel.assert_message_sent()


class TestBlockLinksMultipleLinks:
    """Testes com multiplos links na mesma mensagem."""

    @pytest.mark.asyncio
    async def test_blocks_message_with_mixed_links(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que mensagem com link permitido E bloqueado e deletada.

        Input: Mensagem com Twitter (permitido) e spam site (bloqueado)
        Output: Mensagem deletada (um link bloqueado e suficiente)
        """
        # Arrange
        msg = create_message(
            channel, member,
            "Check https://twitter.com and also https://spam-site.com"
        )
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": ["twitter"],
            "answer": "Bloqueado!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_deleted()


class TestBlockLinksEdgeCases:
    """Testes de casos especiais."""

    @pytest.mark.asyncio
    async def test_handles_allowed_links_as_string(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que allowed_links como string (ao inves de lista) e tratado.

        Input: allowed_links configurado como string
        Output: Funciona normalmente
        """
        # Arrange
        msg = create_message(channel, member, "Check https://spam.com")
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": "twitter",  # String ao inves de lista
            "answer": "Bloqueado!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_deleted()

    @pytest.mark.asyncio
    async def test_handles_http_links(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Verifica que links HTTP (sem SSL) tambem sao bloqueados.

        Input: Link HTTP
        Output: Mensagem deletada
        """
        # Arrange
        msg = create_message(channel, member, "Check http://spam-site.com")
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": [],
            "answer": "Bloqueado!"
        }

        # Act
        await check_message(str(guild.id), msg)

        # Assert
        msg.assert_deleted()


class TestBlockLinksEditedMessages:
    """Mensagens editadas depois de enviadas (bug reportado em teste manual)."""

    def _payload(self, guild, channel, message_id, data):
        from types import SimpleNamespace

        return SimpleNamespace(
            guild_id=guild.id,
            channel_id=channel.id,
            message_id=message_id,
            data=data,
        )

    @pytest.mark.asyncio
    async def test_blocks_a_link_added_by_editing_the_message(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Bug reportado: mandar uma mensagem sem link e depois edita-la
        incluindo um link burlava o bloqueio, porque so existia listener
        de on_message.

        Input: edicao cujo conteudo novo tem um link bloqueado
        Output: mensagem deletada e resposta enviada
        """
        from unittest.mock import MagicMock

        from app.services.block_links import check_edited_message

        msg = create_message(channel, member, "Agora vai https://spam-site.com")
        channel.register_message(msg)
        bot.get_channel = MagicMock(return_value=channel)
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": {"values": []},
            "answer": "Nada de links por aqui! :p",
        }

        await check_edited_message(
            bot,
            self._payload(guild, channel, msg.id,
                          {"content": "Agora vai https://spam-site.com"}),
        )

        msg.assert_deleted()

    @pytest.mark.asyncio
    async def test_ignores_an_edit_that_only_attached_an_embed(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Discord dispara uma edicao quando anexa o preview do link. Sem
        conteudo novo nao ha nada para reavaliar, e buscar a mensagem
        custaria uma chamada HTTP por preview.

        Input: payload de edicao sem a chave content
        Output: nenhuma busca de mensagem
        """
        from unittest.mock import MagicMock

        from app.services.block_links import check_edited_message

        bot.get_channel = MagicMock(return_value=channel)

        await check_edited_message(
            bot,
            self._payload(guild, channel, 500, {"embeds": [{"url": "x"}]}),
        )

        channel._fetch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_an_edit_whose_new_content_has_no_link(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """
        Input: edicao para um texto sem link
        Output: nenhuma busca de mensagem (nada a bloquear)
        """
        from unittest.mock import MagicMock

        from app.services.block_links import check_edited_message

        bot.get_channel = MagicMock(return_value=channel)

        await check_edited_message(
            bot,
            self._payload(guild, channel, 500, {"content": "corrigindo o texto"}),
        )

        channel._fetch_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_ignores_edits_from_bots(
        self, mock_cache, mongodb, guild, bot, channel, bot_member
    ):
        """
        Input: edicao de mensagem cujo autor e um bot
        Output: mensagem preservada
        """
        from unittest.mock import MagicMock

        from app.services.block_links import check_edited_message

        msg = create_message(channel, bot_member, "https://spam-site.com")
        channel.register_message(msg)
        bot.get_channel = MagicMock(return_value=channel)
        mock_cache.return_value = {
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": {"values": []},
            "answer": "Nada de links por aqui! :p",
        }

        await check_edited_message(
            bot,
            self._payload(guild, channel, msg.id,
                          {"content": "https://spam-site.com"}),
        )

        msg.assert_not_deleted()


class TestBlockLinksRecords:
    """O que eu bloqueei fica registrado para a listagem e as estatisticas."""

    def _cog(self):
        return {
            "enabled": True,
            "allowed_roles": {"values": []},
            "allowed_chats": {"values": []},
            "allowed_links": {"values": ["youtube.com"]},
            "answer": "Nada de links por aqui!",
        }

    @pytest.mark.asyncio
    async def test_records_the_blocked_link_with_the_rule_that_decided_it(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        from app.data.blocked_links import find_blocked_links_by_guild

        mock_cache.return_value = self._cog()
        msg = create_message(channel, member, "olha https://spam-site.com/promo")

        await check_message(str(guild.id), msg)

        records = find_blocked_links_by_guild(str(guild.id))
        assert len(records) == 1
        assert records[0]["host"] == "spam-site.com"
        assert records[0]["reason"] == "blocked-no-rule"
        assert records[0]["user_id"] == str(member.id)
        assert records[0]["deleted"] is True
        assert "created_at" in records[0]

    @pytest.mark.asyncio
    async def test_allowed_link_records_nothing(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        from app.data.blocked_links import find_blocked_links_by_guild

        mock_cache.return_value = self._cog()
        msg = create_message(channel, member, "https://youtube.com/watch?v=abc")

        await check_message(str(guild.id), msg)

        msg.assert_not_deleted()
        assert find_blocked_links_by_guild(str(guild.id)) == []

    @pytest.mark.asyncio
    async def test_a_failed_deletion_is_recorded_as_not_deleted(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """Faltou permissao de apagar mensagens: o registro precisa existir
        marcado como nao apagado, senao o dono do servidor nunca descobre."""
        from app.data.blocked_links import find_blocked_links_by_guild

        mock_cache.return_value = self._cog()
        msg = create_message(channel, member, "https://spam-site.com")
        msg._delete.side_effect = RuntimeError("missing permissions")

        with pytest.raises(RuntimeError):
            await check_message(str(guild.id), msg)

        records = find_blocked_links_by_guild(str(guild.id))
        assert len(records) == 1
        assert records[0]["deleted"] is False

    @pytest.mark.asyncio
    async def test_recording_never_breaks_the_moderation(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        """A escrita de auditoria e fail-soft: se o banco cair, a mensagem
        ainda tem que ser apagada."""
        from unittest.mock import patch

        mock_cache.return_value = self._cog()
        msg = create_message(channel, member, "https://spam-site.com")

        with patch("app.data.blocked_links.insert_blocked_link",
                   side_effect=RuntimeError("mongo down")):
            await check_message(str(guild.id), msg)

        msg.assert_deleted()

    @pytest.mark.asyncio
    async def test_records_are_capped_per_message(
        self, mock_cache, mongodb, guild, bot, channel, member
    ):
        from app.data.blocked_links import find_blocked_links_by_guild

        mock_cache.return_value = self._cog()
        links = " ".join(f"https://spam{index}.com" for index in range(9))
        msg = create_message(channel, member, links)

        await check_message(str(guild.id), msg)

        assert len(find_blocked_links_by_guild(str(guild.id))) == 3
