"""Default roles: a member or a bot who joins gets the roles saved for its kind, the
sync gives them to everyone already there, and the panel warns about saved roles
Keiko can no longer give."""

import re

import pytest

from app.commands.roles.service import (
    DefaultRoles,
    filter_roles,
    get_not_available_roles,
    get_roles_to_add,
    set_on_member_join,
)
from app.core.utils import fill, ml
from app.forms.discord.callbacks import RUNTIME
from tests.behavioral.harness.driver import FormScenario
from tests.generators import moderations
from tests.mocks import MockRole, create_guild, create_member

WARNING = "default_roles.command.panel.info.description"


def roles_of(member):
    return sorted(role.name for role in member.roles)


class TestDefaultRolesOnMemberJoin:
    """A member or a bot who joins gets the roles saved for its kind."""

    async def test_a_new_member_gets_the_member_roles(self, deps, guild):
        await moderations.default_roles(deps.mongo_client, str(guild.id), user_roles=["202"])
        new_member = create_member(guild, id=999, name="NewUser", roles=[])

        await set_on_member_join(new_member)

        assert roles_of(new_member) == ["Member"]

    async def test_nothing_is_given_when_nothing_is_saved(self, guild):
        new_member = create_member(guild, id=999, name="NewUser", roles=[])

        await set_on_member_join(new_member)

        assert roles_of(new_member) == []

    async def test_a_new_bot_gets_the_bot_roles_and_not_the_member_ones(self, deps, guild):
        guild.roles.append(MockRole(id=300, name="BotRole", position=2, guild=guild))
        await moderations.default_roles(
            deps.mongo_client, str(guild.id), user_roles=["202"], bot_roles=["300"]
        )
        new_bot = create_member(guild, id=999, name="NewBot", roles=[], bot=True)

        await set_on_member_join(new_bot)

        assert roles_of(new_bot) == ["BotRole"]


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

    async def test_the_sync_result_reads_like_keiko(self, deps, guild):
        """Broke as: a bare green embed with no picture, no footer and no numbers."""
        from unittest.mock import AsyncMock, MagicMock

        from app.commands.roles import service
        from app.constants import KeikoIcons, Style
        from app.ui.embed import standard_footer

        await moderations.default_roles(deps.mongo_client, str(guild.id), user_roles=["202"])
        create_member(guild, id=1, name="Ana")
        create_member(guild, id=2, name="Bia")
        create_member(guild, id=3, name="Helper", bot=True)
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
            assert standard_footer("pt-br") in embed.description
        synced = ml("default_roles.ui.responses.synced.description", "pt-br")
        assert result.description.startswith(fill(synced, members=2, bots=0)), (
            "only the member roles are configured, so no bot got one"
        )


async def open_panel(deps, locale, **saved):
    """The default roles panel of a server that saved `saved`, as its admin opens it."""
    RUNTIME.reset()
    guild = create_guild()
    admin = create_member(guild, id=555, name="Tester")
    scenario = FormScenario(guild=guild, user=admin, locale=locale, mongo=deps.mongo_client)
    await moderations.default_roles(deps.mongo_client, str(guild.id), **saved)
    await scenario.start_command("default_roles")
    return scenario.rendered_summary


class TestThePanelWarnsOnlyAboutSavedRoles:
    """The panel's missing-permission warning names only roles the server saved."""

    async def test_a_server_that_saved_only_bot_roles_sees_no_warning(self, deps):
        """
        Broke as: a server that gave roles only to bots opened its panel to
        "I no longer have permission to assign the following roles: <@&None>",
        because the absent member roles were read as one role named None. The
        shared behaviour is the default roles panel info; it must stay empty
        while every saved role is one Keiko can still assign.
        """
        panel = await open_panel(deps, "en-us", bot_roles=["202"])

        assert re.findall(r"<@&[^>]*>", panel) == ["<@&202>"], (
            "the saved bot role is the only role the panel names"
        )

    @pytest.mark.parametrize("locale", ["en-us", "pt-br"])
    async def test_a_role_keiko_lost_is_the_panel_warning_with_no_title(self, deps, locale):
        """The warning is the panel's closing paragraph, worded as its copy says."""
        panel = await open_panel(deps, locale, user_roles=["999"])

        warning = fill(ml(WARNING, locale), roles="<@&999>")
        assert f"\n{warning}\n" in panel
        above = panel.split(warning)[0].rstrip("\n").split("\n")[-1]
        assert not above.startswith("#"), "the warning has no title of its own"
