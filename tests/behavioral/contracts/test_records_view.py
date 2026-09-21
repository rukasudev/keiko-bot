"""Shared contract: the RecordsBrowser primitive.

`RecordsBrowser` (app/views/records.py) is the generic browse screen for a
command's records: fetch → to_fields → PaginationView, with an optional empty
state and an optional filter-by-member round trip. The blocked-links list and
both history screens (manager, log inspection) consume it; this suite pins the
primitive's own contract so every consumer inherits it.
"""
import pytest

from app.services.utils import ml
from app.views.pagination import PaginationView
from app.views.records import RecordsBrowser

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

FILTER_NS = "commands.commands.commons.block-links-manager.blocked-list.filter"


def _flt(leaf: str, locale: str = "pt-br") -> str:
    return ml(f"{FILTER_NS}.{leaf}", locale=locale)


def _browser(**overrides) -> RecordsBrowser:
    config = dict(
        fetch=lambda i, uid: [{"name": f"record-{n}"} for n in range(1, 7)],
        to_fields=lambda records, i: {r["name"]: "details" for r in records},
        title="Registros",
        description="Tudo que aconteceu",
    )
    config.update(overrides)
    return RecordsBrowser(**config)


async def test_records_render_paginated_and_ephemeral(scenario_factory):
    scenario = scenario_factory(locale="pt-br")
    await _browser().send(scenario._mint())

    listing = scenario.expect_message(ephemeral=True, title_contains="Registros")
    fields = (listing.get("embed") or {}).get("fields") or []
    assert [field["name"] for field in fields] == [
        f"record-{n}" for n in range(1, 5)
    ], "sep=4 keeps the first four records on page one"
    assert isinstance(scenario.current_message.view, PaginationView)


async def test_no_records_without_empty_copy_still_sends_the_pagination(
        scenario_factory):
    """The history screens' shape: no empty_description means the (empty)
    pagination goes out exactly as it did before the extraction."""
    scenario = scenario_factory(locale="pt-br")
    await _browser(fetch=lambda i, uid: [], description="").send(scenario._mint())

    listing = scenario.expect_message(ephemeral=True)
    assert not (listing.get("embed") or {}).get("fields")
    assert isinstance(scenario.current_message.view, PaginationView)


async def test_empty_state_replaces_the_pagination_when_copy_is_provided(
        scenario_factory):
    scenario = scenario_factory(locale="pt-br")
    await _browser(
        fetch=lambda i, uid: [], empty_description="Nada por aqui ainda!"
    ).send(scenario._mint())

    listing = scenario.expect_message(
        title_contains="Registros", description_contains="Nada por aqui ainda!")
    assert not listing.get("components"), "the empty state carries no pagination"


async def test_member_filter_round_trip(scenario_factory):
    """Filter → picker → filtered list → back to all, each on a fresh
    interaction, all inside the primitive."""
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=111, name="Culpado", roles=["Member"])
    create_member(guild, id=222, name="Inocente", roles=["Member"])
    data = [{"name": "spam", "user": "111"}, {"name": "ok", "user": "222"}]

    scenario = scenario_factory(locale="pt-br", guild=guild)
    browser = _browser(
        fetch=lambda i, uid: [r for r in data if uid is None or r["user"] == uid],
        filter_namespace=FILTER_NS,
    )
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    scenario.expect_message(title_contains=_flt("title"))
    await scenario.select_option("Culpado")
    await scenario.confirm()

    filtered = str(scenario.expect_message(title_contains="Registros").get("embed"))
    assert "spam" in filtered and "ok" not in filtered

    await scenario.click(_flt("all-label"))
    unfiltered = str(scenario.expect_message(title_contains="Registros").get("embed"))
    assert "spam" in unfiltered and "ok" in unfiltered


async def test_filtered_empty_resolves_the_namespace_copy(scenario_factory):
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=222, name="Inocente", roles=["Member"])

    scenario = scenario_factory(locale="pt-br", guild=guild)
    browser = _browser(
        fetch=lambda i, uid: [] if uid else [{"name": "spam"}],
        filter_namespace=FILTER_NS,
    )
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    await scenario.select_option("Inocente")
    await scenario.confirm()

    scenario.expect_message(description_contains=_flt("empty"))


async def test_a_consumer_needs_nothing_command_shaped(scenario_factory):
    """The minimal consumer: plain strings and a closure fetch — no i18n
    namespace, no filter, no service in sight."""
    scenario = scenario_factory(locale="pt-br")
    browser = RecordsBrowser(
        fetch=lambda i, uid: [{"name": "any"}],
        to_fields=lambda records, i: {r["name"]: "value" for r in records},
        title="Qualquer coisa",
    )
    await browser.send(scenario._mint())

    scenario.expect_message(title_contains="Qualquer coisa")
    assert isinstance(scenario.current_message.view, PaginationView)


async def test_confirming_with_nobody_chosen_shows_every_record(scenario_factory):
    """Confirm without picking anyone answers the click, showing everyone.

    What broke: the picker's Confirm called back into a filter that returned
    when nothing was selected, so the click was never answered and Discord
    showed "This interaction failed". The shared primitive is RecordsBrowser's
    member filter, consumed by the blocked-links list and both history screens.
    What must stay true: every click on the picker ends in a screen.
    """
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=111, name="Culpado", roles=["Member"])
    data = [{"name": "spam", "user": "111"}, {"name": "ok", "user": "222"}]

    scenario = scenario_factory(locale="pt-br", guild=guild)
    browser = _browser(
        fetch=lambda i, uid: [r for r in data if uid is None or r["user"] == uid],
        filter_namespace=FILTER_NS,
    )
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    await scenario.confirm()

    listing = str(scenario.expect_message(title_contains="Registros").get("embed"))
    assert "spam" in listing and "ok" in listing


async def test_cancelling_the_picker_returns_to_every_record(scenario_factory):
    """Cancel leaves the picker for the list it was opened from."""
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=111, name="Culpado", roles=["Member"])
    data = [{"name": "spam", "user": "111"}, {"name": "ok", "user": "222"}]

    scenario = scenario_factory(locale="pt-br", guild=guild)
    browser = _browser(
        fetch=lambda i, uid: [r for r in data if uid is None or r["user"] == uid],
        filter_namespace=FILTER_NS,
    )
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    await scenario.click(ml("buttons.cancel.label", locale="pt-br"))

    listing = str(scenario.expect_message(title_contains="Registros").get("embed"))
    assert "spam" in listing and "ok" in listing
