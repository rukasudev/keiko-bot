"""What every form's flow shares: the fixed server, seeding a saved configuration and
its history, opening a command over it (from one admin or two at once), Cancel
answered both ways and an item removed from its panel."""

import copy
import datetime
import inspect
from types import SimpleNamespace
from typing import Any, Callable, Dict

from app.core.cogs import LIFECYCLE_ANALYTICS_EVENTS
from app.core.utils import ml
from app.data import data, wait
from tests.generators.moderations import seed_cog
from tests.mocks.discord import create_guild, create_member

GUILD_ID = "123456789"
ADMIN_ID = "555"
FIXED_EVENT_TIME = datetime.datetime(2026, 1, 1, 9, 30, 0, tzinfo=datetime.timezone.utc)
UPLOADED_URL = "https://cdn.discordapp.com/attachments/999/1/custom-banner.png"


def keep_label(locale: str) -> str:
    """The Keep editing button of Cancel's question."""
    return ml("buttons.keep.label", locale=locale)


def discard_label(locale: str) -> str:
    """The Discard button of Cancel's question."""
    return ml("buttons.discard.label", locale=locale)


def seed_document(deps, form: str, document: Dict[str, Any]) -> None:
    """Save `document` as the server's `form`, as a deep copy: the engine edits
    nested lists in place, and a shared seed would carry one test's edits into
    the next."""
    seed_cog(deps.mongo_client, form, copy.deepcopy(document))


def dump_channel(deps) -> SimpleNamespace:
    """The channel an uploaded image is re-sent to: it answers `UPLOADED_URL` and
    keeps what each send carried in `sent`."""
    sent = []

    async def send(*args, **kwargs):
        sent.append(kwargs)
        return SimpleNamespace(attachments=[SimpleNamespace(url=UPLOADED_URL)])

    channel = SimpleNamespace(send=send, sent=sent)
    deps.bot.get_channel = lambda _channel_id: channel
    return channel


def seed_history(deps, form: str, *events: str) -> None:
    """Changes the history screen lists, one an hour from a fixed clock."""
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
    """Run `seed(deps)` (sync or async), then open `form` as its command does."""
    seeded = seed(deps)
    if inspect.isawaitable(seeded):
        await seeded
    return await scenario_factory(locale=locale).start_command(form)


async def cancel_keep(scenario, locale: str) -> None:
    """Cancel, then keep editing."""
    await scenario.cancel()
    await scenario.click(keep_label(locale))


async def cancel_discard(scenario, locale: str) -> None:
    """Cancel, then discard what was answered."""
    await scenario.cancel()
    await scenario.click(discard_label(locale))


async def two_panels(scenario_factory, form: str, locale: str):
    """Two admins of the same server, Tester (555) and Helper (556), each with
    `form`'s panel open."""
    guild = create_guild()
    first_admin = create_member(guild, id=555, name="Tester")
    second_admin = create_member(guild, id=556, name="Helper")
    first = await scenario_factory(
        locale=locale, guild=guild, user=first_admin
    ).start_command(form)
    second = await scenario_factory(
        locale=locale, guild=guild, user=second_admin
    ).start_command(form)
    return first, second


async def remove_item(scenario, target: str) -> None:
    """Remove on the panel's item `target` (`<list>$<index>`), confirmed."""
    await scenario.click(f"remove_this:{target}")
    await scenario.click("remove_items")


async def delete_feature(scenario) -> None:
    """Delete on the panel, confirmed: the feature and what it saved are gone."""
    await scenario.click("disable")
    await scenario.click("delete_all")
