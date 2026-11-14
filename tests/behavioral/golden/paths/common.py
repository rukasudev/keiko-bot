"""Steps every form shares: seeding a saved configuration, opening the
command through its service, and the manager lifecycle buttons."""
import inspect
import copy
import datetime
from typing import Any, Callable, Dict

from app.data import data, wait
from app.core.cogs import LIFECYCLE_ANALYTICS_EVENTS
from app.core.utils import ml

from tests.behavioral.golden.paths import golden_path
from tests.generators.moderations import seed_cog

GUILD_ID = "123456789"
ADMIN_ID = "555"
FIXED_EVENT_TIME = datetime.datetime(2026, 1, 1, 9, 30, 0, tzinfo=datetime.timezone.utc)

LOCALIZED = {
    "yes": {"pt-br": "Sim", "en-us": "Yes"},
    "later": {"pt-br": "Depois", "en-us": "Later"},
}


def label(key: str, locale: str) -> str:
    return LOCALIZED[key][locale]


def keep_label(locale: str) -> str:
    return ml("buttons.cancel.keep", locale=locale)


def discard_label(locale: str) -> str:
    return ml("buttons.cancel.discard", locale=locale)


def seed_document(deps, form: str, document: Dict[str, Any]) -> None:
    """A deep copy: the old engine edits nested lists of the document in place,
    and a shared module-level seed would carry one path's edits into the next."""
    seed_cog(deps.mongo_client, form, copy.deepcopy(document))


def seed_history(deps, form: str, *events: str) -> None:
    """Changes the history screen lists, on a fixed clock."""
    for index, event in enumerate(events):
        wait(data().events_changes.insert({
            "ts": FIXED_EVENT_TIME + datetime.timedelta(hours=index),
            "type": LIFECYCLE_ANALYTICS_EVENTS[event],
            "guild_id": GUILD_ID,
            "cog": form,
            "actor": {"kind": "user", "id": ADMIN_ID},
            "trace_id": None,
            "session_id": None,
            "props": {"source": "manager"},
            "v": 1,
        }))


async def open_manager(scenario_factory, deps, locale: str, form: str,
                       seed: Callable[[Any], Any]):
    seeded = seed(deps)
    if inspect.isawaitable(seeded):
        await seeded
    return await scenario_factory(locale=locale).start_command(form)


async def cancel_keep(scenario, locale: str) -> None:
    await scenario.cancel()
    await scenario.click(keep_label(locale))


async def cancel_discard(scenario, locale: str) -> None:
    await scenario.cancel()
    await scenario.click(discard_label(locale))


def register_lifecycle(form: str, seed_enabled: Callable[[Any], None],
                       seed_paused: Callable[[Any], None]) -> None:
    """Pause, unpause, disable and history: the same four clicks on every panel."""

    @golden_path(form, "manager_pause")
    async def manager_pause(scenario_factory, deps, locale):
        scenario = await open_manager(scenario_factory, deps, locale, form, seed_enabled)
        await scenario.click("pause")
        await scenario.submit_confirmation()
        return scenario

    @golden_path(form, "manager_unpause")
    async def manager_unpause(scenario_factory, deps, locale):
        scenario = await open_manager(scenario_factory, deps, locale, form, seed_paused)
        await scenario.click("unpause")
        await scenario.submit_confirmation()
        return scenario

    @golden_path(form, "manager_disable")
    async def manager_disable(scenario_factory, deps, locale):
        scenario = await open_manager(scenario_factory, deps, locale, form, seed_enabled)
        await scenario.click("disable")
        await scenario.submit_confirmation()
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
