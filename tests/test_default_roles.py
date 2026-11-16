"""
Testes de integracao para default_roles.

Estes testes verificam o fluxo de atribuicao automatica de roles
quando um novo membro entra no servidor.
"""

import pytest
from app.commands.roles.service import (
    DefaultRoles,
    set_on_member_join,
    filter_roles,
    get_roles_to_add,
    get_not_available_roles,
)
from tests.mocks import create_member, MockRole


class TestDefaultRolesOnMemberJoin:
    """Testes de atribuicao de roles ao entrar no servidor."""

    @pytest.mark.asyncio
    async def test_adds_default_roles_to_new_member(
        self, mock_cache, mock_available_roles, mongodb, guild, bot
    ):
        """
        Verifica que roles padrao sao adicionadas a novos membros.

        Input: Novo membro entra, default_roles configurado
        Output: Roles padrao adicionadas ao membro
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser", roles=[])
        mock_cache.return_value = {
            "default_roles": {"values": ["202"]},
            "default_roles_bot": {"values": []}
        }
        mock_available_roles.return_value = {"Member": "202"}

        # Act
        await set_on_member_join(new_member)

        # Assert
        new_member.assert_roles_added("Member")

    @pytest.mark.asyncio
    async def test_does_nothing_when_not_configured(
        self, mock_cache, mongodb, guild, bot
    ):
        """
        Verifica que nada acontece quando default_roles nao esta configurado.

        Input: Novo membro entra, default_roles desabilitado
        Output: Nenhuma role adicionada
        """
        # Arrange
        new_member = create_member(guild, id=999, name="NewUser", roles=[])
        mock_cache.return_value = None

        # Act
        await set_on_member_join(new_member)

        # Assert
        new_member.assert_no_roles_added()

    @pytest.mark.asyncio
    async def test_adds_bot_roles_to_bot_member(
        self, mock_cache, mock_available_roles, mongodb, guild, bot
    ):
        """
        Verifica que bot recebe roles de bot, nao de usuario.

        Input: Bot entra no servidor, roles de bot configuradas
        Output: Bot recebe roles de bot
        """
        # Arrange
        bot_role = MockRole(id=300, name="BotRole", position=2, guild=guild)
        guild.roles.append(bot_role)
        new_bot = create_member(guild, id=999, name="NewBot", roles=[], bot=True)

        mock_cache.return_value = {
            "default_roles": {"values": ["202"]},  # Member role
            "default_roles_bot": {"values": ["300"]}  # Bot role
        }
        mock_available_roles.return_value = {"BotRole": "300", "Member": "202"}

        # Act
        await set_on_member_join(new_bot)

        # Assert
        new_bot.assert_roles_added("BotRole")


class TestDefaultRolesFiltering:
    """Testes de filtragem de roles disponiveis."""

    def test_filter_roles_returns_only_available_roles(self):
        """
        Verifica que apenas roles disponiveis sao retornadas.

        Input: Lista com roles validas e invalidas
        Output: Apenas roles validas
        """
        # Arrange
        roles = ["202", "999"]  # 999 nao existe
        available_roles = {"Member": "202"}

        # Act
        result = filter_roles(roles, available_roles)

        # Assert
        assert result == ["202"]
        assert "999" not in result

    def test_filter_roles_handles_string_input(self):
        """
        Verifica que funciona quando roles e string ao inves de lista.

        Input: Role como string
        Output: Lista com a role se valida
        """
        # Arrange
        roles = "202"  # String, nao lista
        available_roles = {"Member": "202"}

        # Act
        result = filter_roles(roles, available_roles)

        # Assert
        assert result == ["202"]

    def test_filter_roles_returns_empty_for_invalid_string(self):
        """
        Verifica que retorna lista vazia para string invalida.

        Input: Role string que nao existe
        Output: Lista vazia
        """
        # Arrange
        roles = "999"
        available_roles = {"Member": "202"}

        # Act
        result = filter_roles(roles, available_roles)

        # Assert
        assert result == []

    def test_filter_roles_handles_empty_list(self):
        """
        Verifica que lista vazia retorna lista vazia.

        Input: Lista vazia de roles
        Output: Lista vazia
        """
        # Arrange
        roles = []
        available_roles = {"Member": "202"}

        # Act
        result = filter_roles(roles, available_roles)

        # Assert
        assert result == []


class TestGetRolesToAdd:
    """Testes de selecao de roles a adicionar."""

    def test_returns_roles_not_already_assigned(self, guild):
        """
        Verifica que retorna apenas roles que o membro ainda nao tem.

        Input: Membro sem roles, role configurada
        Output: Role retornada para adicao
        """
        # Arrange
        member = create_member(guild, id=999, name="NewUser", roles=[])
        roles_mapping = DefaultRoles(members=("202",), bots=())

        # Act
        result = get_roles_to_add(member, guild, roles_mapping)

        # Assert
        assert len(result) == 1
        assert result[0].id == 202

    def test_skips_roles_already_assigned(self, guild):
        """
        Verifica que nao adiciona roles que o membro ja tem.

        Input: Membro com Member role, Member role configurada
        Output: Lista vazia (ja tem a role)
        """
        # Arrange
        member = create_member(guild, id=999, name="ExistingUser", roles=["Member"])
        roles_mapping = DefaultRoles(members=("202",), bots=())

        # Act
        result = get_roles_to_add(member, guild, roles_mapping)

        # Assert
        assert len(result) == 0

    def test_uses_bot_roles_for_bots(self, guild):
        """
        Verifica que bots recebem roles do mapeamento de bots.

        Input: Bot, roles de bot configuradas
        Output: Apenas roles de bot
        """
        # Arrange
        bot_role = MockRole(id=300, name="BotRole", position=2, guild=guild)
        guild.roles.append(bot_role)

        bot_member = create_member(guild, id=999, name="Bot", roles=[], bot=True)
        roles_mapping = DefaultRoles(members=("202",), bots=("300",))

        # Act
        result = get_roles_to_add(bot_member, guild, roles_mapping)

        # Assert
        assert len(result) == 1
        assert result[0].name == "BotRole"


class TestGetNotAvailableRoles:
    """Testes de deteccao de roles indisponiveis."""

    def test_returns_empty_when_all_roles_available(self):
        """
        Verifica que retorna string vazia quando todas as roles estao disponiveis.

        Input: Todas as roles configuradas sao validas
        Output: String vazia
        """
        # Arrange
        roles = ["202"]
        available_roles = {"Member": "202"}

        # Act
        result = get_not_available_roles(roles, available_roles, "en-US")

        # Assert
        assert result == ""

    def test_returns_message_when_roles_unavailable(self):
        """
        Verifica que retorna mensagem quando roles nao estao disponiveis.

        Input: Role configurada nao existe mais
        Output: Mensagem de erro com mencao da role
        """
        # Arrange
        roles = ["999"]
        available_roles = {"Member": "202"}

        # Act
        result = get_not_available_roles(roles, available_roles, "en-US")

        # Assert
        assert "<@&999>" in result

    def test_handles_string_role_input(self):
        """
        Verifica que funciona quando roles e string.

        Input: Role como string, nao lista
        Output: Mensagem de erro apropriada
        """
        # Arrange
        roles = "999"
        available_roles = {"Member": "202"}

        # Act
        result = get_not_available_roles(roles, available_roles, "en-US")

        # Assert
        assert "<@&999>" in result


class TestDefaultRolesSync:
    """The sync button's messages."""

    @pytest.mark.asyncio
    async def test_the_sync_result_reads_like_keiko(self, mock_cache, guild, monkeypatch):
        """Broke as: a bare green embed with no picture, no footer and no numbers."""
        from unittest.mock import AsyncMock, MagicMock

        from app.constants import KeikoIcons, Style
        from app.commands.roles import service

        guild.members = [
            create_member(guild, id=1, name="Ana"),
            create_member(guild, id=2, name="Bia"),
            create_member(guild, id=3, name="Helper", bot=True),
        ]
        mock_cache.return_value = {"default_roles": {"values": ["202"]}}
        monkeypatch.setattr(
            service,
            "set_default_roles",
            AsyncMock(return_value=service.RolesGiven(members=2, bots=0)),
        )
        message = MagicMock()
        message.edit = AsyncMock()
        interaction = MagicMock()
        interaction.guild = guild
        interaction.locale = "pt-br"
        interaction.followup.send = AsyncMock(return_value=message)

        await service.set_on_default_roles_sync(interaction)

        waiting = interaction.followup.send.call_args.kwargs["embed"]
        result = message.edit.call_args.kwargs["embed"]
        for embed in (waiting, result):
            assert embed.color.value == int(Style.BACKGROUND_COLOR, 16)
            assert embed.thumbnail.url == KeikoIcons.IMAGE_01
            assert "/reportar" in embed.description
        assert "Membros: **2**" in result.description
        assert "Bots: **0**" in result.description, (
            "only the member roles are configured, so no bot got one"
        )


class TestThePanelWarnsOnlyAboutSavedRoles:
    """The panel's missing-permission warning names only roles the server saved."""

    @pytest.mark.asyncio
    async def test_a_server_that_saved_only_bot_roles_sees_no_warning(
        self, deps, monkeypatch
    ):
        """
        Broke as: a server that gave roles only to bots opened its panel to
        "I no longer have permission to assign the following roles: <@&None>",
        because the absent member roles were read as one role named None. The
        shared behaviour is the default roles panel info; it must stay empty
        while every saved role is one Keiko can still assign.
        """
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from app.commands.roles import service as default_roles
        from app.forms.features import feature_for
        from app.forms.features.feature import CommitContext, OpenContext
        from app.forms.form.form_state import Answer

        monkeypatch.setattr(
            default_roles,
            "get_available_roles_by_guild",
            lambda guild: {"Member": "202", "Helper": "201"},
        )
        feature = feature_for("default_roles")
        commit = CommitContext(
            "123", "555", "en-us", {}, datetime.now(timezone.utc), "slash", "s1"
        )
        await feature.commit(
            "setup", {"answers": {"default_roles_bot": Answer(["201"])}}, commit
        )
        guild = SimpleNamespace(id=123, members=[], member_count=0, roles=[])

        opened = await feature.open(OpenContext("123", "555", "en-us", guild))

        assert opened.document is not None
        assert opened.panel.info == ""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "locale,warning",
        [
            (
                "en-us",
                "I no longer have permission to assign the following roles: "
                "**<@&999>**\n\nIn the server's role hierarchy, please give me a "
                "role **above** these 🧑‍⚖️",
            ),
            (
                "pt-br",
                "Não tenho mais permissão para atribuir os seguintes cargos: "
                "**<@&999>**\n\nNa hierarquia de cargos do servidor, me dê um cargo "
                "**acima** desses 🧑‍⚖️",
            ),
        ],
    )
    async def test_a_role_keiko_lost_is_the_panel_warning_with_no_title(
        self, deps, monkeypatch, locale, warning
    ):
        """The warning is the panel's closing paragraph, worded as it always was."""
        from datetime import datetime, timezone
        from types import SimpleNamespace

        from app.commands.roles import service as default_roles
        from app.forms.features import feature_for
        from app.forms.features.feature import CommitContext, OpenContext
        from app.forms.form.form_state import Answer

        monkeypatch.setattr(
            default_roles, "get_available_roles_by_guild", lambda guild: {"Member": "202"}
        )
        feature = feature_for("default_roles")
        commit = CommitContext(
            "123", "555", locale, {}, datetime.now(timezone.utc), "slash", "s1"
        )
        await feature.commit(
            "setup", {"answers": {"default_roles": Answer(["999"])}}, commit
        )
        guild = SimpleNamespace(id=123, members=[], member_count=0, roles=[])

        opened = await feature.open(OpenContext("123", "555", locale, guild))

        assert opened.panel.info == warning
        assert opened.panel.info_title == ""
