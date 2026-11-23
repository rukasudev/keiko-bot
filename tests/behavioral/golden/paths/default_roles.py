"""Auto roles: one card (roles for bots, roles for members, the roles by time
in the server with a card per rung, and, in the setup only, whether to give the
join roles to who is already in the server), and the question the first rung asks.

No Back exists anywhere on this form (the card is the first step and the
review screen has none), so there is no `setup_back` path.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.commands.roles import service
from tests.behavioral.flows.common import cancel_discard, cancel_keep, open_manager
from tests.behavioral.flows.roles import (
    FORM,
    add_rung,
    open_card,
    seed,
    seed_member_roles_only,
    seed_paused,
)
from tests.behavioral.golden.paths import golden_book, golden_path
from tests.behavioral.golden.paths.common import register_lifecycle
from tests.mocks.discord import create_guild, create_member

NOW = datetime(2026, 5, 12, 12, 0, tzinfo=timezone.utc)


golden_book(FORM, rendered=("form.panel", "form.review"))


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.select_option("Member", target="default_roles")
    await scenario.select_option("Moderator", target="default_roles_bot")
    await scenario.click("done")
    await scenario.click("pick:False")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_gives_the_roles_now")
async def setup_gives_the_roles_now(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.select_option("Member", target="default_roles")
    await scenario.click("done")
    await scenario.click("pick:True")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.select_option("Member", target="default_roles")
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.select_option("Member", target="default_roles")
    await cancel_discard(scenario, locale)
    return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("section:default_roles_config/default_roles")
    await scenario.select_option("Moderator", target="default_roles")
    return scenario


@golden_path(FORM, "setup_with_a_rung", locales=("pt-br", "en-us"))
async def setup_with_a_rung(scenario_factory, deps, locale):
    guild = create_guild()
    create_member(guild, id=771, name="Veterana", joined_at=NOW - timedelta(days=10))
    with patch.object(service, "utc_now", lambda: NOW):
        scenario = await scenario_factory(locale=locale, guild=guild).start_command(
            FORM
        )
        await scenario.confirm()
        await add_rung(scenario, 7, "Member")
        await scenario.click("done")
        await scenario.confirm()
        await scenario.confirm()
    return scenario


@golden_path(FORM, "manager_add_rung")
async def manager_add_rung(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await add_rung(scenario, 30, "Moderator", where="panel")
    return scenario


@golden_path(FORM, "manager_keeps_one_setting")
async def manager_keeps_one_setting(scenario_factory, deps, locale):
    scenario = await open_manager(
        scenario_factory, deps, locale, FORM, seed_member_roles_only
    )
    await scenario.click("section:default_roles_config/default_roles")
    await scenario.select_option([], target="default_roles")
    return scenario


register_lifecycle(FORM, seed_enabled=seed, seed_paused=seed_paused)
