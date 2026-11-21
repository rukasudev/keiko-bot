"""The lifecycle paths every panel shares: Pause, Unpause, Delete and History,
the same clicks on every form, registered once per form."""
import inspect
from typing import Any, Callable

from tests.behavioral.flows.common import open_manager, seed_history
from tests.behavioral.golden.paths import golden_path


def register_lifecycle(form: str, seed_enabled: Callable[[Any], None],
                       seed_paused: Callable[[Any], None]) -> None:
    """Pause, resume, delete and history: the same clicks on every panel."""

    @golden_path(form, "manager_pause")
    async def manager_pause(scenario_factory, deps, locale):
        scenario = await open_manager(scenario_factory, deps, locale, form, seed_enabled)
        await scenario.click("pause")
        return scenario

    @golden_path(form, "manager_unpause")
    async def manager_unpause(scenario_factory, deps, locale):
        scenario = await open_manager(scenario_factory, deps, locale, form, seed_paused)
        await scenario.click("unpause")
        return scenario

    @golden_path(form, "manager_disable")
    async def manager_disable(scenario_factory, deps, locale):
        scenario = await open_manager(scenario_factory, deps, locale, form, seed_enabled)
        await scenario.click("disable")
        await scenario.click("delete_all")
        return scenario

    @golden_path(form, "manager_history")
    async def manager_history(scenario_factory, deps, locale):
        async def seed(deps_):
            seeded = seed_enabled(deps_)
            if inspect.isawaitable(seeded):
                await seeded
            seed_history(deps_, form, "enabled", "edited")

        scenario = await open_manager(scenario_factory, deps, locale, form, seed)
        await scenario.click("history")
        return scenario
