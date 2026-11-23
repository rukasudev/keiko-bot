"""Auto roles: a member or a bot who joins gets the roles saved for its kind, the
sync gives them to everyone already there, the panel warns about saved roles Keiko
can no longer give, and once a day each member climbs the roles by time."""

import logging
import re
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from app.commands.roles.service import (
    DefaultRoles,
    filter_roles,
    get_not_available_roles,
    get_roles_to_add,
    set_on_member_join,
)
from app.core.commands import command_for
from app.core.jobs import hand_due_schedules
from app.core.utils import fill, ml
from app.data import data
from app.data.keiko.schedules import schedule_id
from app.forms.discord.callbacks import RUNTIME
from tests.behavioral.harness.driver import FormScenario
from tests.generators import moderations
from tests.generators.moderations import recorded_server
from tests.helpers.slash_tree import synced_tree
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
        available_roles = {"202"}

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
        available_roles = {"202"}

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
        available_roles = {"202"}

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
        available_roles = {"202"}

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
        available_roles = {"202"}

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
        available_roles = {"202"}

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
        available_roles = {"202"}

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


LADDER = "role_ladder"


def joined(days_ago):
    """A join time `days_ago` days back from now."""
    return datetime.now(timezone.utc) - timedelta(days=days_ago, hours=1)


async def climb_now(guild_id):
    """The server's daily pass, due now, run by the loop that hands due schedules."""
    now = datetime.now(timezone.utc)
    await data().keiko_schedules.upsert_schedule(
        schedule_id(LADDER, guild_id), LADDER, None, due_at=now - timedelta(minutes=1)
    )
    await hand_due_schedules(now)


async def saved_pass(guild_id):
    return await data().keiko_schedules.find_schedule(schedule_id(LADDER, guild_id))


class TestTheRolesByTime:
    """Once a day each member gets the role of the highest rung reached and loses the
    other rungs' roles; bots and roles outside the ladder are left alone."""

    async def test_a_member_keeps_only_the_role_of_the_highest_rung_reached(
        self, deps, guild
    ):
        await moderations.default_roles(
            deps.mongo_client,
            str(guild.id),
            rungs=[(7, "202"), (30, "201"), (90, "200")],
        )
        climber = create_member(
            guild, id=701, name="Ana", roles=["Member", "Admin"], joined_at=joined(40)
        )
        newcomer = create_member(guild, id=702, name="Bia", joined_at=joined(2))

        await climb_now(guild.id)

        assert roles_of(climber) == ["Moderator"], (
            "the 30 days role given, the 7 and the 90 days ones taken"
        )
        assert roles_of(newcomer) == [], "below the first rung nothing changes"

    async def test_a_bot_never_climbs(self, deps, guild):
        await moderations.default_roles(
            deps.mongo_client, str(guild.id), rungs=[(1, "202")]
        )
        robot = create_member(guild, id=703, name="Robo", bot=True, joined_at=joined(400))

        await climb_now(guild.id)

        assert roles_of(robot) == []

    async def test_a_rung_keiko_cannot_give_leaves_its_members_as_they_are(
        self, deps, guild, caplog
    ):
        """The pass warns once, not once per member, and the rest of the ladder runs."""
        guild.roles.append(MockRole(id=210, name="Above", position=20, guild=guild))
        await moderations.default_roles(
            deps.mongo_client, str(guild.id), rungs=[(7, "202"), (30, "210")]
        )
        veteran = create_member(
            guild, id=704, name="Cris", roles=["Member"], joined_at=joined(60)
        )
        other = create_member(guild, id=705, name="Duda", joined_at=joined(60))
        rookie = create_member(guild, id=706, name="Edu", joined_at=joined(10))

        with caplog.at_level(logging.WARNING):
            await climb_now(guild.id)

        assert (roles_of(veteran), roles_of(other)) == (["Member"], [])
        assert roles_of(rookie) == ["Member"]
        skipped = [record for record in caplog.records if "210" in record.getMessage()]
        assert len(skipped) == 1

    async def test_a_member_past_a_rung_before_the_ladder_started_is_left_as_is(
        self, deps, guild
    ):
        """What an admin who answered no to the first question gets: only a rung
        reached from then on is given."""
        await moderations.default_roles(
            deps.mongo_client,
            str(guild.id),
            rungs=[(7, "202")],
            ladder_since=datetime.now(timezone.utc) - timedelta(days=5),
        )
        earlier = create_member(guild, id=707, name="Fê", joined_at=joined(20))
        later = create_member(guild, id=708, name="Gabi", joined_at=joined(10))

        await climb_now(guild.id)

        assert (roles_of(earlier), roles_of(later)) == ([], ["Member"])

    async def test_a_member_holds_one_ladder_role_after_a_rung_moved_up(
        self, deps, guild
    ):
        """Broke as: a pass took only the roles of the rungs below the one reached, so
        a member who held the role of a rung whose days the admin raised past theirs
        kept it beside the new one, two ladder roles. Shared behaviour: none, auto
        roles' own pass. Consumer that exposed it: a rung edited on the panel.
        Guaranteed: after a pass a member holds only the role of the rung they reach,
        and no other role of the ladder."""
        await moderations.default_roles(
            deps.mongo_client, str(guild.id), rungs=[(7, "202"), (60, "201")]
        )
        climber = create_member(
            guild, id=711, name="Iara", roles=["Moderator"], joined_at=joined(40)
        )

        await climb_now(guild.id)

        assert roles_of(climber) == ["Member"]

    @pytest.mark.parametrize(
        ("today", "expected"),
        [
            pytest.param(datetime(2025, 2, 27, 12), [], id="a day short of a month"),
            pytest.param(datetime(2025, 2, 28, 12), ["Member"], id="Feb 28 is a month"),
            pytest.param(datetime(2026, 1, 30, 12), ["Member"], id="a day short of a year"),
            pytest.param(datetime(2026, 1, 31, 12), ["Moderator"], id="a year"),
        ],
    )
    async def test_a_time_in_months_and_years_ends_on_its_calendar_day(
        self, deps, guild, today, expected
    ):
        """The owner's choice (PR10 round 2): a time role counts calendar months and
        years from the day the member joined, in the server's zone, a month too short
        for that day ending on its last day: who joined on Jan 31 reaches 1 month on
        Feb 28 and 1 year on the next Jan 31, and then holds only the year's role."""
        from app.commands.roles.service import climb_ladder

        config = await moderations.default_roles(
            deps.mongo_client, str(guild.id), rungs=[("P1M", "202"), ("P1Y", "201")]
        )
        member = create_member(
            guild,
            id=712,
            name="Jo",
            roles=["Member"] if expected == ["Moderator"] else [],
            joined_at=datetime(2025, 1, 31, 15, tzinfo=timezone.utc),
        )

        moment = today.replace(tzinfo=timezone.utc)
        await climb_ladder(guild, config, moment, timezone.utc)

        assert roles_of(member) == expected

    async def test_the_day_still_begins_where_its_midnight_is_skipped(
        self, deps, guild
    ):
        """In Santiago, 6 September 2026 starts at 01:00: the pass armed the evening
        before is due then, and the next one at the midnight after it."""
        from app.commands.roles.service import arm_ladder, climb_server

        santiago = ZoneInfo("America/Santiago")
        recorded_server(deps.mongo_client, guild.id, timezone="America/Santiago")
        await moderations.default_roles(
            deps.mongo_client, str(guild.id), rungs=[(7, "202")]
        )
        evening = datetime(2026, 9, 5, 23, 30, tzinfo=santiago)

        await arm_ladder(guild_id=str(guild.id), now=evening)
        first = (await saved_pass(guild.id))["due_at"].replace(tzinfo=timezone.utc)
        await climb_server(guild_id=str(guild.id), now=first)
        second = (await saved_pass(guild.id))["due_at"].replace(tzinfo=timezone.utc)

        assert first.astimezone(santiago) == datetime(2026, 9, 6, 1, 0, tzinfo=santiago)
        assert second.astimezone(santiago) == datetime(2026, 9, 7, 0, 0, tzinfo=santiago)

    async def test_the_pass_is_due_again_at_the_next_midnight_of_the_server(
        self, deps, guild
    ):
        recorded_server(deps.mongo_client, guild.id, timezone="Asia/Tokyo")
        await moderations.default_roles(
            deps.mongo_client, str(guild.id), rungs=[(7, "202")]
        )

        await climb_now(guild.id)

        due = (await saved_pass(guild.id))["due_at"]
        due = due if due.tzinfo else due.replace(tzinfo=timezone.utc)
        tokyo = due.astimezone(ZoneInfo("Asia/Tokyo"))
        assert tokyo.time() == time(0)
        assert timedelta(0) < due - datetime.now(timezone.utc) <= timedelta(days=1)

    @pytest.mark.parametrize(
        "saved",
        [
            pytest.param({"enabled": False, "rungs": [(7, "202")]}, id="paused"),
            pytest.param({"user_roles": ["202"]}, id="no rungs"),
        ],
    )
    async def test_a_paused_ladder_or_one_without_rungs_drops_its_daily_pass(
        self, deps, guild, saved
    ):
        await moderations.default_roles(deps.mongo_client, str(guild.id), **saved)
        member = create_member(guild, id=709, name="Hugo", joined_at=joined(30))

        await climb_now(guild.id)

        assert await saved_pass(guild.id) is None
        assert roles_of(member) == []


@pytest.mark.parametrize(
    ("locale", "typed"),
    [("en-us", "/moderations roles auto"), ("pt-br", "/moderações cargos automáticos")],
)
async def test_auto_roles_is_typed_by_its_new_name(locale, typed):
    """The command was `/moderations default roles` (`/moderações cargos padrão`)
    until it gained the roles by time and became Auto Roles."""
    tree = await synced_tree()

    assert tree.path_of(command_for("default_roles").cogs, locale) == [typed]
