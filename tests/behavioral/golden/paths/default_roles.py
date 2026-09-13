"""Default roles: one multi-select step (roles for bots, roles for members).

No Back exists anywhere on this form (the multi-select is the first step and
the review screen has none), so there is no `setup_back` path.
"""
from tests.behavioral.golden.paths import golden_path
from tests.behavioral.golden.paths.common import (
    GUILD_ID,
    cancel_discard,
    cancel_keep,
    open_manager,
    register_lifecycle,
    seed_document,
)

FORM = "default_roles"

ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "default_roles": {"style": "role", "values": "202"},
    "default_roles_bot": {"style": "role", "values": ["201"]},
}
PAUSED = {**ENABLED, "enabled": False}


async def _setup(scenario_factory, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await _setup(scenario_factory, locale)
    await scenario.select_option("Member", target="default_roles")
    await scenario.select_option("Moderator", target="default_roles_bot")
    await scenario.confirm()
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await _setup(scenario_factory, locale)
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await _setup(scenario_factory, locale)
    await cancel_discard(scenario, locale)
    return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(
        scenario_factory, deps, locale, FORM, lambda d: seed_document(d, FORM, ENABLED)
    )
    await scenario.click("section:default_roles_config")
    await scenario.select_option("Admin", target="default_roles")
    await scenario.confirm()
    return scenario


register_lifecycle(
    FORM,
    seed_enabled=lambda deps: seed_document(deps, FORM, ENABLED),
    seed_paused=lambda deps: seed_document(deps, FORM, PAUSED),
)
