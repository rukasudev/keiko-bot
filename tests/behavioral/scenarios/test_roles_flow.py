"""Behavioral scenarios: /moderações cargos automáticos (cargos padrão until PR10), a
card since beta.2, with its roles by time since PR10.

Guaranteed: after the card, a setup-only question asks, with the server's totals
and the chosen roles, whether to give them to who is already in the server; Sim
gives them after saving to the members already there, Não leaves them for whoever
joins, and the answer is never saved with the settings. The first rung asks the
same about the members already past it, and the join roles or a rung must stay.
"""

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import discord
import pytest

from app.commands.roles import service
from app.core.dates import elapsed_label, full_date_label
from app.core.jobs import hand_due_schedules
from app.core.utils import ml
from tests.behavioral.flows import roles
from app.data import data
from tests.behavioral.flows.common import GUILD_ID, delete_feature, seed_document
from tests.behavioral.harness.copy_keys import copy_text
from tests.helpers.components import card_texts
from tests.generators import moderations
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

QUESTION = "default_roles.form.steps.sync_existing"
NOW = datetime(2026, 4, 10, 15, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    """Keiko's roles read the moment the members of these tests are built from:
    Apr 10 2026, noon in the server's zone."""
    monkeypatch.setattr(service, "utc_now", lambda: NOW)


async def _asked(scenario_factory, guild):
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    await scenario.select_option("Member", target="default_roles")
    await scenario.click("done")
    return scenario


def _member_role(guild):
    return next(role for role in guild.roles if role.name == "Member")


async def test_a_setup_that_says_yes_gives_the_roles_to_who_is_already_there(
    scenario_factory,
):
    """N3 (beta.2 round 2): after the card, a setup step of its own asks with the
    server's totals and the chosen roles; Sim gives them, after saving, to the
    members already in the server."""
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")
    create_member(guild, id=888, name="Robo", bot=True)
    scenario = await _asked(scenario_factory, guild)

    asked = scenario.expect_message()["embed"]
    assert asked["title"].endswith(copy_text(f"{QUESTION}.title", "pt-br"))
    counted = copy_text(
        f"{QUESTION}.description", "pt-br", members_count=2, bots_count=1
    )
    for written in re.split(r"\$\{[^}]*\}", counted):
        assert written in asked["description"], "the members and bots it counted"
    assert f"<@&{_member_role(guild).id}>" in asked["description"]
    assert [button.label for button in scenario.current_message.view.children] == [
        copy_text(f"{QUESTION}.options.0.label", "pt-br"),
        copy_text(f"{QUESTION}.options.1.label", "pt-br"),
        ml("buttons.back.label", "pt-br"),
        ml("buttons.cancel.label", "pt-br"),
    ]

    await scenario.click("pick:True")
    await scenario.confirm()
    await scenario.finish()

    assert _member_role(guild) in friend.roles, "the member already there got the role"
    stored = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})
    assert "sync_existing" not in stored


async def test_a_setup_left_on_no_gives_the_roles_only_to_whoever_joins(
    scenario_factory,
):
    guild = create_guild()
    friend = create_member(guild, id=777, name="Amiga")
    scenario = await _asked(scenario_factory, guild)

    await scenario.click("pick:False")
    await scenario.confirm()
    await scenario.finish()

    assert _member_role(guild) not in friend.roles


LADDER_QUESTION = "default_roles.ui.responses.ladder_confirm"


def _past_and_new(guild):
    """A member in the server for ten days and one for two."""
    past = create_member(
        guild, id=771, name="Veterana", joined_at=NOW - timedelta(days=10)
    )
    new = create_member(guild, id=772, name="Novata", joined_at=NOW - timedelta(days=2))
    return past, new


async def _set_up_with_a_rung(scenario_factory, guild):
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    await roles.add_rung(scenario, 7, "Member")
    await scenario.click("done")
    await scenario.confirm()
    return scenario


def _asked_how_many(scenario):
    """The text of the time roles question on screen, a card under its title."""
    title, body, *_rest = card_texts(scenario.current_message.view)
    assert title == f"## {copy_text(f'{LADDER_QUESTION}.title', 'pt-br')}"
    return body


def _buttons_below_the_card(message):
    """The labels of the buttons in the row under a question's card."""
    card, row = message.view.children
    assert isinstance(card, discord.ui.Container)
    assert isinstance(row, discord.ui.ActionRow)
    return [button.label for button in row.children]


SERVER_ZONE = ZoneInfo(moderations.SERVER_SETTINGS["timezone"])


def _member_line(member):
    """A member's line under a role: the mention, the day they joined and their time
    in the server, both in the server's time zone."""
    joined = member.joined_at.astimezone(SERVER_ZONE).date()
    today = NOW.astimezone(SERVER_ZONE).date()
    return copy_text(
        "default_roles.ui.texts.ladder_member",
        "pt-br",
        user=f"<@{member.id}>",
        date=full_date_label(joined, "pt-br"),
        time_in_server=elapsed_label(joined, today, "pt-br"),
    )


def _asked_about(role, *members, shown=None):
    """The question as it reads with one block: the role's mention and how many get
    it, over a line per member, the first `shown` and how many more."""
    return _asked_about_each([(role, members)], shown)


def _asked_about_each(groups, shown=None):
    """The question as it reads with one block per role of `groups`, in their order."""
    blocks = []
    for role, members in groups:
        limit = len(members) if shown is None else shown
        lines = [_member_line(member) for member in members[:limit]]
        if len(members) > limit:
            hidden = len(members) - limit
            lines.append(copy_text("texts.and_more", "pt-br", count=hidden))
        blocks.append(
            copy_text(
                "default_roles.ui.texts.ladder_group",
                "pt-br",
                role=role.id,
                count=len(members),
                climbers="\n".join(lines),
            )
        )
    groups_text = "\n\n".join(blocks)
    return copy_text(f"{LADDER_QUESTION}.description", "pt-br", groups=groups_text)


async def test_a_setup_with_a_rung_asks_before_giving_it_to_who_is_already_past_it(
    scenario_factory,
):
    """D10-06: the first time, Keiko counts the members already past a rung and
    gives them its role only on yes; whoever has not reached one is left alone."""
    guild = roles.server_where_keiko_gives_every_role()
    past, new = _past_and_new(guild)
    scenario = await _set_up_with_a_rung(scenario_factory, guild)

    assert _asked_how_many(scenario).startswith(_asked_about(_member_role(guild), past))
    await scenario.confirm()
    await scenario.finish()

    assert _member_role(guild) in past.roles
    assert _member_role(guild) not in new.roles


async def test_a_rung_question_answered_no_gives_nothing_now_or_on_the_next_pass(
    scenario_factory, deps
):
    guild = roles.server_where_keiko_gives_every_role()
    deps.bot.get_guild = lambda guild_id: guild
    past, _new = _past_and_new(guild)
    scenario = await _set_up_with_a_rung(scenario_factory, guild)

    await scenario.cancel()
    due = roles.daily_pass(scenario, guild)["due_at"]
    await hand_due_schedules(due)

    assert _member_role(guild) not in past.roles
    await scenario.finish()


async def test_the_first_rung_added_on_the_panel_asks_the_same_question(
    scenario_factory, deps
):
    guild = roles.server_where_keiko_gives_every_role()
    past, _new = _past_and_new(guild)
    roles.seed(deps)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await roles.add_rung(scenario, 7, "Moderator", where="panel")

    moderator = next(role for role in guild.roles if role.name == "Moderator")
    assert _asked_how_many(scenario).startswith(_asked_about(moderator, past))
    await scenario.confirm()
    await scenario.finish()
    assert moderator in past.roles


@pytest.mark.parametrize("with_a_rung", [True, False], ids=["a rung", "nothing"])
async def test_a_rung_alone_is_enough_to_save_and_nothing_at_all_is_not(
    scenario_factory, with_a_rung
):
    """The join roles became optional once a rung can stand in for them; with
    neither, the summary refuses to save, as the card once did."""
    guild = roles.server_where_keiko_gives_every_role()
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    if with_a_rung:
        await roles.add_rung(scenario, 30, "Member")
    await scenario.click("done")
    await scenario.confirm()

    stored = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})
    if with_a_rung:
        assert stored["enabled"] is True
    else:
        assert stored is None
        scenario.expect_error(ml("errors.fill_one_field.message", "pt-br"))
    await scenario.finish()


async def _empty_the_member_roles(scenario):
    await scenario.click("section:default_roles_config/default_roles")
    await scenario.select_option([], target="default_roles")


async def _remove_the_rung(scenario):
    await scenario.click("edit:group:ladder")
    await scenario.click("remove_this:role_ladder$0")
    await scenario.click("remove_items")


@pytest.mark.parametrize(
    ("seed", "change"),
    [
        pytest.param(roles.seed_member_roles_only, _empty_the_member_roles, id="roles"),
        pytest.param(roles.seed_ladder_only, _remove_the_rung, id="rung"),
    ],
)
async def test_the_panel_keeps_either_the_join_roles_or_a_rung(
    scenario_factory, deps, seed, change
):
    """The summary's rule holds on the panel too: taking out the last of them is
    refused with a message of its own, and what was saved stays."""
    guild = roles.server_where_keiko_gives_every_role()
    seed(deps)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    before = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})

    await change(scenario)

    scenario.expect_warning(
        ml("commands.responses.keep_one_field.description", "pt-br").split("$")[0]
    )
    after = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})
    assert after == before
    await scenario.finish()


async def test_the_daily_pass_is_kept_only_while_the_ladder_runs(
    scenario_factory, deps
):
    """Unpausing arms the pass for the next midnight; pausing and deleting drop it."""
    guild = roles.server_where_keiko_gives_every_role()
    roles.seed_ladder_only(deps, enabled=False)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await scenario.click("unpause")
    armed = roles.daily_pass(scenario, guild)
    await scenario.click("pause")
    paused = roles.daily_pass(scenario, guild)
    await scenario.click("unpause")
    await delete_feature(scenario)

    assert armed is not None and paused is None
    assert roles.daily_pass(scenario, guild) is None
    await scenario.finish()


async def test_an_edit_of_the_join_roles_keeps_the_rungs(scenario_factory, deps):
    guild = roles.server_where_keiko_gives_every_role()
    roles.seed_ladder_only(deps)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await scenario.click("section:default_roles_config/default_roles")
    await scenario.select_option(["Member"], target="default_roles")

    stored = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})
    assert stored["role_ladder"] == roles.LADDER_ONLY["role_ladder"]
    await scenario.finish()


def _role(guild, name):
    return next(role for role in guild.roles if role.name == name)


@pytest.mark.parametrize("answer", ["yes", "no"])
async def test_a_rung_added_later_asks_about_who_is_already_past_it(
    scenario_factory, deps, answer
):
    """Each rung asks on its own: a 365 days rung added on Apr 10 to a ladder whose 30
    days rung was answered no on Jan 1 counts both members already past a year, Ana
    (365 days on Mar 6) and Bruno (in November), and treats them alike.

    Broke as: one start, written when the first rung asked, stood for every rung, so a
    rung added later asked nothing and the next pass gave its role to whoever reached
    it after that first start (Ana) and never to whoever reached it before (Bruno).
    Shared behaviour: the question a save leaves (`FollowUp`), now about one item of
    a list. Consumer that exposed it: auto roles' roles by time. Guaranteed: a rung
    added on the panel asks with the members already past it; yes gives it to all of
    them now, no to none of them, now or on a later pass.
    """
    guild = roles.server_where_keiko_gives_every_role()
    deps.bot.get_guild = lambda guild_id: guild
    await moderations.default_roles(
        deps.mongo_client,
        GUILD_ID,
        user_roles=[],
        rungs=[(30, "202")],
        ladder_since=NOW - timedelta(days=99),
    )
    ana = create_member(guild, id=781, name="Ana", joined_at=NOW - timedelta(days=400))
    bruno = create_member(
        guild, id=782, name="Bruno", joined_at=NOW - timedelta(days=515)
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await roles.add_rung(scenario, 365, "Moderator", where="panel")

    asked = _asked_about(_role(guild, "Moderator"), ana, bruno)
    assert _asked_how_many(scenario).startswith(asked)
    if answer == "yes":
        await scenario.confirm()
    else:
        await scenario.cancel()
        await hand_due_schedules(roles.daily_pass(scenario, guild)["due_at"])
    await scenario.finish()

    moderator = _role(guild, "Moderator")
    given = [moderator in member.roles for member in (ana, bruno)]
    assert given == ([True, True] if answer == "yes" else [False, False])


async def test_a_save_just_after_midnight_keeps_the_pass_of_the_day(
    scenario_factory, deps
):
    """Broke as: every save moved the daily pass to the next midnight, so a save in the
    seconds between midnight and the loop's next tick put off that day's pass to the
    day after. Shared behaviour: none, auto roles' own schedule. Consumer that exposed
    it: auto roles' panel. Guaranteed: a save keeps a pass already due, and the loop
    runs it on its next tick.
    """
    guild = roles.server_where_keiko_gives_every_role()
    roles.seed_ladder_only(deps)
    due = NOW - timedelta(seconds=10)
    await data().keiko_schedules.upsert_schedule(
        f"role_ladder:{guild.id}", "role_ladder", None, due_at=due
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await scenario.click("section:default_roles_config/default_roles")
    await scenario.select_option(["Member"], target="default_roles")

    kept = roles.daily_pass(scenario, guild)["due_at"]
    kept = kept if kept.tzinfo else kept.replace(tzinfo=timezone.utc)
    assert abs(kept - due) < timedelta(seconds=1)
    await scenario.finish()


@pytest.mark.parametrize("trouble", ["write_fails", "paused"])
async def test_the_yes_says_it_is_giving_and_then_how_it_went(
    scenario_factory, deps, trouble
):
    """Broke as: the yes gave every role inside the click, with no word until it ended,
    a failed write of the rung's start left "Dando os cargos..." on screen for good,
    and a feature paused before the yes answered the Sync's error. Shared behaviour:
    `in_background` of the feature module, which logs a failure through the form's
    context. Consumer that exposed it: auto roles' roles by time. Guaranteed: the yes
    answers that the roles are on their way, then the generic error when the start
    cannot be written, and a paused feature answers its own roles by time error.
    """
    guild = roles.server_where_keiko_gives_every_role()
    deps.bot.get_guild = lambda guild_id: guild
    _past_and_new(guild)
    scenario = await _set_up_with_a_rung(scenario_factory, guild)
    if trouble == "paused":
        saved = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})
        seed_document(deps, roles.FORM, {**saved, "enabled": False})
    else:

        async def refused(*_args, **_kwargs):
            raise RuntimeError("the database is down")

        for write in (
            "set_cog_state",
            "update_cog_if_unchanged",
            "update_cog_by_guild",
        ):
            setattr(data().guild_cogs, write, refused)

    await scenario.confirm()
    await scenario.finish()

    if trouble == "paused":
        scenario.expect_error(
            ml("default_roles.ui.errors.ladder_deactivated.message", "pt-br")
        )
    else:
        giving = ml("default_roles.ui.responses.ladder_giving.title", "pt-br")
        assert any(
            (event.get("embed") or {}).get("title") == giving
            for event in scenario.outputs
        )
        scenario.expect_error(ml("errors.generic.title", "pt-br"))


def _ladder_line(count_text):
    label = copy_text("default_roles.form.steps.role_ladder.title", "pt-br")
    return f"⏳ **{label}:** {count_text}"


def _set_up_count(count):
    return copy_text("vocabulary.texts.set_up_counter", "pt-br", count=count, max=5)


async def test_the_card_keeps_the_ladder_on_a_screen_of_its_own(scenario_factory):
    """The owner's marks (PR10 rounds 1 and 2): the card shows the roles by time on
    one heading beside Configurar, the empty value or each time role on a line of its
    own, its role's mention then its time; Configurar opens a screen with the list,
    and Back returns to the card with what was configured."""
    guild = roles.server_where_keiko_gives_every_role()
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    assert _ladder_line(ml("texts.empty_value", "pt-br")) in scenario.rendered_summary

    await scenario.click("customize:ladder_screen")
    await scenario.click("add:role_ladder")
    await roles.submit_time(scenario, 5, "months")
    await scenario.select_option("Member", target="role")
    await scenario.click("done")
    scenario.expect_component(label_or_action="edit:role_ladder$0")

    await scenario.click("back")
    line = f"- <@&{_member_role(guild).id}> → 5 meses"
    card = scenario.rendered_summary
    assert f"{_ladder_line('').rstrip()}\n{line}" in card
    scenario.expect_component(
        label_or_action=copy_text("vocabulary.texts.configure", "pt-br")
    )
    await scenario.finish()


async def test_the_panel_keeps_the_ladder_on_a_screen_of_its_own(
    scenario_factory, deps
):
    """The panel draws the roles by time as one line with the Edit that opens their
    screen, where Add, each item's Edit and Remove are; the panel's last row keeps no
    Add or Remove of its own."""
    guild = roles.server_where_keiko_gives_every_role()
    roles.seed_ladder_only(deps)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    panel = scenario.rendered_summary
    assert _ladder_line(_set_up_count(1)) in panel
    assert "→" not in panel and "**Cargo:**" not in panel
    for hidden in ("add:role_ladder", "remove:role_ladder", "add", "remove"):
        with pytest.raises(Exception):
            scenario.expect_component(label_or_action=hidden)

    await scenario.click("edit:group:ladder")
    scenario.expect_component(label_or_action="add:role_ladder")
    scenario.expect_component(label_or_action="edit:role_ladder$0")
    assert "<@&202>" in scenario.rendered_summary
    await scenario.finish()


async def test_the_question_names_the_first_members_and_the_success_only_counts(
    scenario_factory, deps
):
    """The owner's marks (PR10 rounds 1, 2 and 6): the question groups the members by
    the role a yes gives them, the role's mention and count over a line per member,
    when they joined and their time in the server, the first
    `ViewConstants.QUESTION_GROUP_ITEMS_SHOWN` and "e mais N"; the message after
    giving says only how many."""
    from app.constants import ViewConstants

    limit = ViewConstants.QUESTION_GROUP_ITEMS_SHOWN
    guild = roles.server_where_keiko_gives_every_role()
    deps.bot.get_guild = lambda guild_id: guild
    veterans = [
        create_member(
            guild, id=800 + n, name=f"V{n}", joined_at=NOW - timedelta(days=10)
        )
        for n in range(limit + 2)
    ]
    scenario = await _set_up_with_a_rung(scenario_factory, guild)

    asked = _asked_about(_member_role(guild), *veterans, shown=limit)
    assert _asked_how_many(scenario).startswith(asked)
    joined = (NOW - timedelta(days=10)).astimezone(SERVER_ZONE).strftime("%d/%m/%Y")
    block = _asked_how_many(scenario).split("\n\n")[1].split("\n")
    assert block[:2] == [
        f"<@&{_member_role(guild).id}> ({limit + 2})",
        f"- <@800> (entrou em {joined}): 10 dias",
    ]
    assert block[-1] == "e mais 2"
    await scenario.confirm()
    await scenario.finish()

    given = ml("default_roles.ui.responses.ladder_given.title", "pt-br")
    [told] = [
        event["embed"]
        for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == given
    ]
    assert f"**{limit + 2}**" in told["description"]
    assert "<@8" not in told["description"], "the success never names the members"


def _veterans(guild, count, days, first_id):
    """`count` members in the server for `days` days, their ids from `first_id`."""
    joined = NOW - timedelta(days=days)
    return [
        create_member(guild, id=first_id + n, name=f"V{first_id + n}", joined_at=joined)
        for n in range(count)
    ]


@pytest.mark.parametrize(
    ("time_roles", "members", "cut_line"),
    [(5, 7, 0), (2, 7, 1), (2, 3, None)],
    ids=["roles left out", "members left out", "everything shown"],
)
async def test_the_question_is_a_card_of_three_roles_of_five_members(
    scenario_factory, deps, time_roles, members, cut_line
):
    """The owner's mark (PR10 round 6, s20): the time roles question is a card that
    shows the first three roles, shortest time first, and five members of each, "e
    mais N" under a role it cuts; after a divider, one 🔎 line names every role it
    leaves out, or, when it only cut members, points to See all for every member, and
    nothing is written when it cut nothing. Below the card, 🔎 Ver todos sits between
    Confirmar and Cancelar exactly while that line shows."""
    from app.constants import ViewConstants
    from app.core.utils import join_names

    guild = roles.server_where_keiko_gives_every_role()
    deps.bot.get_guild = lambda guild_id: guild
    names = [f"Time role {index}" for index in range(time_roles)]
    added = roles.add_roles(guild, names, first_id=300)
    climbers = [
        _veterans(guild, members, 10 * index + 15, 1000 + 100 * index)
        for index in range(time_roles)
    ]
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    for index, role in enumerate(added):
        await roles.add_rung(scenario, 10 * index + 10, role.name)
    await scenario.click("done")
    await scenario.confirm()

    shown = ViewConstants.QUESTION_GROUPS_SHOWN
    groups = list(zip(added, climbers))[:shown]
    each = ViewConstants.QUESTION_GROUP_ITEMS_SHOWN
    assert _asked_how_many(scenario) == _asked_about_each(groups, shown=each)
    left_out = join_names([f"<@&{role.id}>" for role in added[shown:]], "pt-br")
    lines = (
        []
        if cut_line is None
        else [
            copy_text(
                f"{LADDER_QUESTION}.overrides.{cut_line}.info",
                "pt-br",
                roles_left_out=left_out,
            )
        ]
    )
    _title, _body, *closing, _footer = card_texts(scenario.current_message.view)
    assert closing == lines
    see_all = [ml("buttons.see_all.label", "pt-br")] if lines else []
    assert _buttons_below_the_card(scenario.current_message) == [
        ml("buttons.confirm.label", "pt-br"),
        *see_all,
        ml("buttons.cancel.label", "pt-br"),
    ]
    await scenario.finish()


def _page(scenario):
    [field] = scenario.expect_message(ephemeral=True)["embed"]["fields"]
    return field["value"]


def _grouped(role, members, count):
    return copy_text(
        "default_roles.ui.texts.ladder_group",
        "pt-br",
        role=role.id,
        count=count,
        climbers="\n".join(_member_line(member) for member in members),
    )


def _option(scenario, label):
    [select] = [
        item
        for item in scenario.current_message.view.children
        if isinstance(item, discord.ui.Select)
    ]
    return next(option.value for option in select.options if option.label == label)


async def test_see_all_lists_every_member_the_question_counted_by_page_and_role(
    scenario_factory, deps
):
    """The owner's mark (PR10 round 3, s20 and s23): See all opens, apart from the
    question, every member it counted under the role a yes gives them, ten a page,
    and filters by role. The list is the one the question counted, kept with it: a
    member who joins after it was asked is not on it, and the question still waits
    for its answer."""
    from app.constants import ViewConstants

    limit = ViewConstants.COMPOSITION_PREVIEW_LIMIT
    guild = roles.server_where_keiko_gives_every_role()
    deps.bot.get_guild = lambda guild_id: guild
    members = _veterans(guild, limit + 2, 10, 800)
    moderators = _veterans(guild, 3, 40, 900)
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    await roles.add_rung(scenario, 7, "Member")
    await roles.add_rung(scenario, 30, "Moderator")
    await scenario.click("done")
    await scenario.confirm()
    question = scenario.current_message
    _veterans(guild, 1, 10, 990)

    await scenario.click(ml("buttons.see_all.label", "pt-br"))
    member, moderator = _member_role(guild), _role(guild, "Moderator")
    title = copy_text("default_roles.ui.records.climbers.title", "pt-br")
    scenario.expect_message(ephemeral=True, title_contains=title)
    assert _page(scenario) == _grouped(member, members[:limit], limit + 2)
    await scenario.click(">")
    assert _page(scenario) == "\n\n".join(
        (
            _grouped(member, members[limit:], limit + 2),
            _grouped(moderator, moderators, 3),
        )
    )

    await scenario.click(
        copy_text("default_roles.ui.records.climbers.filter.label", "pt-br")
    )
    await scenario.select_option(_option(scenario, moderator.name))
    await scenario.confirm()
    assert _page(scenario) == _grouped(moderator, moderators, 3)
    assert ml("buttons.confirm.label", "pt-br") in _buttons_below_the_card(question)
    await scenario.finish()


async def test_the_ladder_screen_shows_every_time_role_under_one_title(
    scenario_factory, deps
):
    """Round 2 of PR10: the panel's ladder screen drew only the first three time roles
    and named its list twice, under the screen's title and again on the list's head.
    A collapsed group's screen is the only way to its items, so it draws every one,
    each with its Edit, and its list reads only its count under the screen's title."""
    guild = roles.server_where_keiko_gives_every_role()
    rungs = [moderations.ladder_rung(days, "202") for days in (1, 3, 7, 14, 30)]
    seed_document(
        deps,
        roles.FORM,
        {
            **roles.LADDER_ONLY,
            "role_ladder": {"style": "composition", "values": rungs},
        },
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await scenario.click("edit:group:ladder")

    for index in range(5):
        scenario.expect_component(label_or_action=f"edit:role_ladder${index}")
    title = copy_text("default_roles.form.steps.role_ladder.title", "pt-br")
    assert scenario.rendered_summary.count(title) == 1
    assert ml("texts.and_more", "pt-br").split("$")[0] not in scenario.rendered_summary
    await scenario.finish()


async def test_the_card_ladder_screen_names_its_list_once(scenario_factory):
    guild = roles.server_where_keiko_gives_every_role()
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()

    title = copy_text("default_roles.form.steps.role_ladder.title", "pt-br")
    await scenario.click("customize:ladder_screen")
    assert scenario.rendered_summary.count(title) == 1
    await scenario.click("back")
    await roles.add_rung(scenario, 30, "Member")

    await scenario.click("customize:ladder_screen")
    assert scenario.rendered_summary.count(title) == 1
    scenario.expect_component(label_or_action="edit:role_ladder$0")
    await scenario.finish()


async def test_the_panel_ladder_screen_removes_each_time_role_on_its_own(
    scenario_factory, deps
):
    """The owner's mark (PR10 round 3, s19): the panel's ladder screen drew an Edit
    beside each time role and one Remove under them all, a picker, where every other
    list of a panel ends each item with its own Edit and red Remove. A collapsed
    group's screen draws its list the way a panel list does: each time role its own
    red Remove, which asks and takes that one alone, the screen staying."""
    guild = roles.server_where_keiko_gives_every_role()
    rungs = [moderations.ladder_rung(days, "202") for days in (1, 3, 7)]
    seed_document(
        deps,
        roles.FORM,
        {
            **roles.LADDER_ONLY,
            "role_ladder": {"style": "composition", "values": rungs},
        },
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.click("edit:group:ladder")

    for index in range(3):
        removal = scenario.component(f"remove_this:role_ladder${index}")
        assert removal.style == discord.ButtonStyle.danger
    with pytest.raises(Exception):
        scenario.expect_component(label_or_action="remove:role_ladder")
    await scenario.click("remove_this:role_ladder$1")
    await scenario.click("remove_items")

    assert _stored_times(scenario, guild) == ["P1D", "P7D"]
    scenario.expect_component(label_or_action="remove_this:role_ladder$1")
    with pytest.raises(Exception):
        scenario.expect_component(label_or_action="remove_this:role_ladder$2")
    await scenario.finish()


async def test_the_card_ladder_screen_removes_one_time_role_from_the_setup(
    scenario_factory,
):
    """The owner's mark (PR10 round 4, s19): the setup's ladder screen drew its time
    roles its own way, one Remove under them all, where the panel's ends each one with
    its Edit and Remove. The setup draws the panel's screen, its Remove grey as every
    setup's is (round 5): the middle time role's Remove asks and takes it alone out of
    the setup, nothing saved yet, the screen staying with the other two, and the setup
    saves those two."""
    guild = roles.server_where_keiko_gives_every_role()
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    for days, role in ((7, "Member"), (30, "Moderator"), (90, "Member")):
        await roles.add_rung(scenario, days, role)
    await scenario.click("customize:ladder_screen")

    assert (
        scenario.component("remove_this:role_ladder$1").style
        == discord.ButtonStyle.secondary
    )
    await scenario.click("remove_this:role_ladder$1")
    await scenario.click("remove_items")

    assert (
        scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)}) is None
    )
    scenario.expect_component(label_or_action="remove_this:role_ladder$1")
    with pytest.raises(Exception):
        scenario.expect_component(label_or_action="remove_this:role_ladder$2")
    await scenario.click("back")
    await scenario.click("done")
    await scenario.confirm()
    assert _stored_times(scenario, guild) == ["P7D", "P90D"]
    await scenario.finish()


async def test_the_card_ladder_screen_edits_a_time_role_in_place(scenario_factory):
    """An item's Edit on the setup's ladder screen comes back to that screen with the
    time role changed."""
    guild = roles.server_where_keiko_gives_every_role()
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    await roles.add_rung(scenario, 7, "Member")
    await scenario.click("customize:ladder_screen")

    await scenario.click("edit:role_ladder$0")
    await roles.submit_time(scenario, 2, "months")
    await scenario.click("done")

    assert "2 meses" in scenario.rendered_summary
    scenario.expect_component(label_or_action="edit:role_ladder$0")
    await scenario.finish()


def _stored_times(scenario, guild):
    stored = scenario.get_persisted("guild", roles.FORM, {"guild_id": str(guild.id)})
    return [item["time"]["value"] for item in stored["role_ladder"]["values"]]


async def _open_a_new_time_role(scenario_factory, guild):
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )
    await scenario.confirm()
    await scenario.click("customize:ladder_screen")
    await scenario.click("add:role_ladder")
    return scenario


@pytest.mark.parametrize(
    ("amount", "unit"),
    [
        ("0", "days"),
        ("1000", "days"),
        ("11", "years"),
        ("522", "weeks"),
        ("três", "months"),
    ],
)
async def test_a_time_out_of_its_bounds_is_refused_and_typed_again(
    scenario_factory, amount, unit
):
    """The owner's choice (PR10 round 2): a time role's time is a number and a unit
    typed in one modal, the number from `FormConstants.TIME_SPAN_AMOUNT_MIN` to
    `TIME_SPAN_AMOUNT_MAX` and the whole no longer than `TIME_SPAN_YEARS_MAX` years. A
    time out of those bounds saves nothing, is told with its error, which names those
    bounds, and the modal opens again with what was typed and the unit chosen."""
    from app.constants import FormConstants

    guild = roles.server_where_keiko_gives_every_role()
    scenario = await _open_a_new_time_role(scenario_factory, guild)

    await roles.submit_time(scenario, amount, unit)

    scenario.expect_error(
        copy_text(
            "errors.invalid_time_span.message",
            "pt-br",
            span_amount_min=FormConstants.TIME_SPAN_AMOUNT_MIN,
            span_amount_max=FormConstants.TIME_SPAN_AMOUNT_MAX,
            span_years_max=FormConstants.TIME_SPAN_YEARS_MAX,
        )
    )
    assert ml("texts.empty_value", "pt-br") in scenario.rendered_summary
    await scenario.click("customize:time")
    number, chosen = scenario.pending_modal_inputs()
    assert number.default == amount
    assert [option.value for option in chosen.options if option.default] == [unit]
    await scenario.finish()


@pytest.mark.parametrize(
    ("amount", "unit", "shown"),
    [
        ("999", "days", "999 dias"),
        ("521", "weeks", "521 semanas"),
        ("120", "months", "120 meses"),
        ("10", "years", "10 anos"),
        ("1", "months", "1 mês"),
    ],
)
async def test_a_time_within_its_bounds_reads_as_typed_on_the_card(
    scenario_factory, amount, unit, shown
):
    guild = roles.server_where_keiko_gives_every_role()
    scenario = await _open_a_new_time_role(scenario_factory, guild)

    await roles.submit_time(scenario, amount, unit)

    assert f"📅 **Tempo no servidor**\n> {shown}\n" in scenario.rendered_summary
    await scenario.finish()


async def test_an_edited_time_role_opens_its_modal_on_the_saved_time(
    scenario_factory, deps
):
    guild = roles.server_where_keiko_gives_every_role()
    seed_document(
        deps,
        roles.FORM,
        {
            **roles.LADDER_ONLY,
            "role_ladder": {
                "style": "composition",
                "values": [moderations.ladder_rung("P5M", "202")],
            },
        },
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await scenario.click("edit:group:ladder")
    await scenario.click("edit:role_ladder$0")
    assert "5 meses" in scenario.rendered_summary
    await scenario.click("customize:time")

    number, chosen = scenario.pending_modal_inputs()
    assert number.default == "5"
    assert [option.value for option in chosen.options if option.default] == ["months"]
    await scenario.finish()


@pytest.mark.parametrize(
    ("saved", "added", "refused"),
    [
        pytest.param("P28D", (4, "weeks"), True, id="4 weeks over 28 days"),
        pytest.param("P12M", (1, "years"), True, id="1 year over 12 months"),
        pytest.param("P30D", (1, "months"), False, id="1 month beside 30 days"),
    ],
)
async def test_two_time_roles_never_share_a_calendar_span(
    scenario_factory, deps, saved, added, refused
):
    """Unique per calendar span: 4 weeks is 28 days and 1 year is 12 months on every
    calendar, so the second is the same time role and is refused as already
    registered; 1 month and 30 days end on different days, so both may stay."""
    guild = roles.server_where_keiko_gives_every_role()
    seed_document(
        deps,
        roles.FORM,
        {
            **roles.LADDER_ONLY,
            "role_ladder": {
                "style": "composition",
                "values": [moderations.ladder_rung(saved, "202")],
            },
        },
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await roles.add_rung(scenario, added, "Moderator", where="panel")

    times = _stored_times(scenario, guild)
    if refused:
        scenario.expect_error(ml("errors.item_already_registered.message", "pt-br"))
        assert times == [saved]
    else:
        assert len(times) == 2
    await scenario.finish()


async def test_time_roles_are_kept_shortest_first(scenario_factory, deps):
    """The list reads in the order a member climbs it: a time role added after a
    longer one is stored, and drawn, before it."""
    guild = roles.server_where_keiko_gives_every_role()
    seed_document(
        deps,
        roles.FORM,
        {
            **roles.LADDER_ONLY,
            "role_ladder": {
                "style": "composition",
                "values": [moderations.ladder_rung("P1Y", "202")],
            },
        },
    )
    scenario = await scenario_factory(locale="pt-br", guild=guild).start_command(
        roles.FORM
    )

    await roles.add_rung(scenario, (2, "weeks"), "Moderator", where="panel")
    if scenario.current_message.embeds:
        await scenario.cancel()

    assert _stored_times(scenario, guild) == ["P2W", "P1Y"]
    await scenario.finish()


async def test_the_slash_command_opens_the_auto_roles_setup(scenario_factory, deps):
    """`/moderações cargos automáticos` opens the same first screen of the Cargos
    Automáticos setup that `/configurar` opens."""
    from app.commands.roles.cog import Roles

    by_command = scenario_factory(locale="pt-br")
    by_command.user.guild_permissions = discord.Permissions(administrator=True)
    await by_command.run_command(Roles.default_roles, Roles(deps.bot))
    by_setup = await scenario_factory(locale="pt-br").start_command(roles.FORM)

    opened = by_command.expect_message()["embed"]
    assert opened == by_setup.expect_message()["embed"]
    assert opened["title"].endswith(
        copy_text("default_roles.form.steps.form.title", "pt-br")
    )
