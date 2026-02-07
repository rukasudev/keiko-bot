"""
Testes de integracao para default_roles.

Estes testes verificam o fluxo de atribuicao automatica de roles
quando um novo membro entra no servidor.
"""

import pytest
from app.services.default_roles import (
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
        roles_mapping = {
            "default_roles": ["202"],
            "default_roles_bot": []
        }

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
        roles_mapping = {
            "default_roles": ["202"],
            "default_roles_bot": []
        }

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
        roles_mapping = {
            "default_roles": ["202"],
            "default_roles_bot": ["300"]
        }

        # Act
        result = get_roles_to_add(bot_member, guild, roles_mapping)

        # Assert
        assert len(result) == 1
        assert result[0].name == "BotRole"


class TestGetNotAvailableRoles:
    """Testes de deteccao de roles indisponiveis."""

    def test_returns_empty_when_all_roles_available(self, mock_ml):
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

    def test_returns_message_when_roles_unavailable(self, mock_ml):
        """
        Verifica que retorna mensagem quando roles nao estao disponiveis.

        Input: Role configurada nao existe mais
        Output: Mensagem de erro com mencao da role
        """
        # Arrange
        roles = ["999"]
        available_roles = {"Member": "202"}
        mock_ml.return_value = "Missing roles: $roles"

        # Act
        result = get_not_available_roles(roles, available_roles, "en-US")

        # Assert
        assert "<@&999>" in result

    def test_handles_string_role_input(self, mock_ml):
        """
        Verifica que funciona quando roles e string.

        Input: Role como string, nao lista
        Output: Mensagem de erro apropriada
        """
        # Arrange
        roles = "999"
        available_roles = {"Member": "202"}
        mock_ml.return_value = "Missing: $roles"

        # Act
        result = get_not_available_roles(roles, available_roles, "en-US")

        # Assert
        assert "<@&999>" in result
