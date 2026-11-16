"""Shared contract: the RecordsBrowser primitive.

`RecordsBrowser` (app/ui/records.py) is the generic browse screen for a
command's records: fetch → to_fields → PaginationView, with an optional empty
state and an optional filter-by-member round trip. The blocked-links list and
both history screens (manager, log inspection) consume it; this suite pins the
primitive's own contract so every consumer inherits it.
"""
import pytest

from app.core.utils import ml
from app.ui.pagination import PaginationView
from app.ui.records import RecordsBrowser
from tests.helpers.copy import checked_copy  # noqa: F401

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

FILTERED = "block_links.ui.records.blocked_list"
LISTS = """\
ui:
  records:
    listed:
      title: {en-us: Records, pt-br: Registros}
      description: {en-us: Everything that happened, pt-br: Tudo que aconteceu}
    quiet:
      title: {en-us: Records, pt-br: Registros}
      empty: {en-us: Nothing here yet!, pt-br: Nada por aqui ainda!}
    bare:
      title: {en-us: Anything, pt-br: Qualquer coisa}
"""


@pytest.fixture
def lists(checked_copy):  # noqa: F811
    checked_copy(LISTS, prefix="lists")


def _flt(leaf: str, locale: str = "pt-br") -> str:
    return ml(f"{FILTERED}.filter.{leaf}", locale=locale)


def _listed(locale: str = "pt-br") -> str:
    return ml(f"{FILTERED}.title", locale=locale)


def _browser(**overrides) -> RecordsBrowser:
    config = dict(
        fetch=lambda i, uid: [{"name": f"record-{n}"} for n in range(1, 7)],
        to_fields=lambda records, i: {r["name"]: "details" for r in records},
        copy="lists.ui.records.listed",
    )
    config.update(overrides)
    listed = config["fetch"]

    async def fetch(interaction, user_id):
        return listed(interaction, user_id)

    return RecordsBrowser(**{**config, "fetch": fetch})


def _filtered(listed) -> RecordsBrowser:
    """A browser with the member filter, worded by the blocked links' Records block."""

    async def fetch(interaction, user_id):
        return listed(interaction, user_id)

    return RecordsBrowser(
        fetch=fetch,
        to_fields=lambda records, i: {r["name"]: "details" for r in records},
        copy=FILTERED,
    )


async def test_records_render_paginated_and_ephemeral(lists, scenario_factory):
    scenario = scenario_factory(locale="pt-br")
    await _browser().send(scenario._mint())

    listing = scenario.expect_message(ephemeral=True, title_contains="Registros")
    fields = (listing.get("embed") or {}).get("fields") or []
    assert [field["name"] for field in fields] == [
        f"record-{n}" for n in range(1, 5)
    ], "sep=4 keeps the first four records on page one"
    assert isinstance(scenario.current_message.view, PaginationView)


async def test_no_records_without_empty_copy_still_sends_the_pagination(
        lists, scenario_factory):
    """The history screens' shape: a Records block without `empty` sends the
    (empty) pagination, exactly as it did before the extraction."""
    scenario = scenario_factory(locale="pt-br")
    await _browser(fetch=lambda i, uid: []).send(scenario._mint())

    listing = scenario.expect_message(ephemeral=True)
    assert not (listing.get("embed") or {}).get("fields")
    assert isinstance(scenario.current_message.view, PaginationView)


async def test_empty_state_replaces_the_pagination_when_copy_is_provided(
        lists, scenario_factory):
    scenario = scenario_factory(locale="pt-br")
    await _browser(fetch=lambda i, uid: [], copy="lists.ui.records.quiet").send(
        scenario._mint()
    )

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
    browser = _filtered(
        lambda i, uid: [r for r in data if uid is None or r["user"] == uid]
    )
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    scenario.expect_message(title_contains=_flt("title"))
    await scenario.select_option("Culpado")
    await scenario.confirm()

    filtered = str(scenario.expect_message(title_contains=_listed()).get("embed"))
    assert "spam" in filtered and "ok" not in filtered

    await scenario.click(_flt("clear_label"))
    unfiltered = str(scenario.expect_message(title_contains=_listed()).get("embed"))
    assert "spam" in unfiltered and "ok" in unfiltered


async def test_filtered_empty_resolves_the_filter_copy(scenario_factory):
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=222, name="Inocente", roles=["Member"])

    scenario = scenario_factory(locale="pt-br", guild=guild)
    browser = _filtered(lambda i, uid: [] if uid else [{"name": "spam"}])
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    await scenario.select_option("Inocente")
    await scenario.confirm()

    scenario.expect_message(description_contains=_flt("empty"))


async def test_a_consumer_needs_nothing_command_shaped(lists, scenario_factory):
    """The minimal consumer: a Records block with a title alone and a coroutine
    fetch — no filter, no service in sight."""
    scenario = scenario_factory(locale="pt-br")

    async def fetch(_interaction, _user_id):
        return [{"name": "any"}]

    browser = RecordsBrowser(
        fetch=fetch,
        to_fields=lambda records, i: {r["name"]: "value" for r in records},
        copy="lists.ui.records.bare",
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
    browser = _filtered(
        lambda i, uid: [r for r in data if uid is None or r["user"] == uid]
    )
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    await scenario.confirm()

    listing = str(scenario.expect_message(title_contains=_listed()).get("embed"))
    assert "spam" in listing and "ok" in listing


async def test_cancelling_the_picker_returns_to_every_record(scenario_factory):
    """Cancel leaves the picker for the list it was opened from."""
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=111, name="Culpado", roles=["Member"])
    data = [{"name": "spam", "user": "111"}, {"name": "ok", "user": "222"}]

    scenario = scenario_factory(locale="pt-br", guild=guild)
    browser = _filtered(
        lambda i, uid: [r for r in data if uid is None or r["user"] == uid]
    )
    await browser.send(scenario._mint())

    await scenario.click(_flt("label"))
    await scenario.click(ml("buttons.cancel.label", locale="pt-br"))

    listing = str(scenario.expect_message(title_contains=_listed()).get("embed"))
    assert "spam" in listing and "ok" in listing
