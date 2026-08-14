"""Behavioral scenarios: what the manager offers on top of the settings.

The blocked-link records are the raw material of two buttons (the list and
the stats) and of the app command tip. These drive the real manager view with
real records in the (mocked) database.
"""
import pytest

from app.data.block_links import insert_blocked_link
from app.services.utils import ml

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("manager_form")]

GUILD_ID = "123456789"

COG = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "custom_links": {"style": "composition", "values": []},
    "answer": "Nada de links por aqui!",
}


def _bm(key: str, locale: str = "pt-br") -> str:
    return ml(f"commands.commands.commons.block-links-manager.{key}", locale=locale)


def _seed(user_id="111", host="spam-site.com", deleted=True):
    insert_blocked_link({
        "guild_id": GUILD_ID,
        "user_id": user_id,
        "channel_id": "100",
        "message_id": "500",
        "link": f"https://{host}/promo",
        "host": host,
        "mode": "block_all",
        "reason": "blocked-no-rule",
        "rule": None,
        "match": None,
        "deleted": deleted,
    })


async def test_manager_offers_the_blocks_and_stats_buttons_and_the_app_command_tip(
        scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))

    scenario.expect_message(kind="send", ephemeral=True)
    scenario.expect_component(label_or_action=_bm("blocked-list.button.label"))
    scenario.expect_component(label_or_action=ml("buttons.stats.label", locale="pt-br"))

    description = scenario.rendered_summary
    assert "Validar Bloqueio de Link" in description, (
        "the manager must teach how to reach the diagnostic app command"
    )
    assert "botão direito" in description, "the tip must be a step by step"


async def test_blocked_list_shows_the_records_of_the_server(scenario_factory):
    _seed()
    _seed(user_id="222", host="outro-site.com")

    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await scenario.click(_bm("blocked-list.button.label"))

    listing = scenario.expect_message(title_contains=_bm("blocked-list.embed.title"))
    rendered = str(listing.get("embed"))
    assert "spam-site.com" in rendered
    assert "outro-site.com" in rendered
    assert "<@111>" in rendered and "<#100>" in rendered


async def test_blocked_list_warns_about_messages_it_could_not_delete(scenario_factory):
    _seed(deleted=False)

    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await scenario.click(_bm("blocked-list.button.label"))

    rendered = str(scenario.expect_message(
        title_contains=_bm("blocked-list.embed.title")).get("embed"))
    assert "⚠️" in rendered
    assert _bm("blocked-list.record.not-deleted") in rendered


async def test_blocked_list_says_so_when_there_is_nothing_yet(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await scenario.click(_bm("blocked-list.button.label"))

    scenario.expect_message(description_contains=_bm("blocked-list.embed.empty"))


async def test_stats_button_reports_the_numbers(scenario_factory):
    _seed()
    _seed(host="spam-site.com")
    _seed(user_id="222", host="outro-site.com")

    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await scenario.click(ml("buttons.stats.label", locale="pt-br"))

    stats = scenario.expect_message(title_contains=_bm("stats.embed.title"))
    description = (stats.get("embed") or {}).get("description") or ""
    assert _bm("stats.fields.total") in description
    assert "spam-site.com" in description, "the most blocked website must show up"
    assert "<@111>" in description, "the member with most blocks must show up"


async def test_stats_button_says_so_on_an_empty_server(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await scenario.click(ml("buttons.stats.label", locale="pt-br"))

    scenario.expect_message(description_contains=_bm("stats.empty"))


async def test_blocked_list_can_be_filtered_by_member(scenario_factory):
    """The "search by user" of the feature: pick a member on the native user
    select and the list narrows to that member only."""
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    member = create_member(guild, id=111, name="Culpado", roles=["Member"])
    create_member(guild, id=222, name="Inocente", roles=["Member"])
    _seed(user_id=str(member.id), host="spam-site.com")
    _seed(user_id="222", host="outro-site.com")

    scenario = await scenario_factory(locale="pt-br", guild=guild).start_manager(
        "block_links", dict(COG))
    await scenario.click(_bm("blocked-list.button.label"))
    await scenario.click(_bm("blocked-list.filter.label"))

    scenario.expect_message(title_contains=_bm("blocked-list.filter.title"))
    await scenario.select_option("Culpado")
    await scenario.confirm()

    filtered = str(scenario.expect_message(
        title_contains=_bm("blocked-list.embed.title")).get("embed"))
    assert "spam-site.com" in filtered
    assert "outro-site.com" not in filtered, "the filter must exclude other members"


async def test_filter_says_so_when_the_member_has_no_blocks(scenario_factory):
    from tests.mocks.discord import create_guild, create_member

    guild = create_guild()
    create_member(guild, id=222, name="Inocente", roles=["Member"])
    _seed(user_id="111", host="spam-site.com")

    scenario = await scenario_factory(locale="pt-br", guild=guild).start_manager(
        "block_links", dict(COG))
    await scenario.click(_bm("blocked-list.button.label"))
    await scenario.click(_bm("blocked-list.filter.label"))
    await scenario.select_option("Inocente")
    await scenario.confirm()

    scenario.expect_message(description_contains=_bm("blocked-list.filter.empty"))


async def test_filter_button_sits_below_the_pagination_row(scenario_factory):
    """Without an explicit row discord.py first-fits the extra button into the
    free slot of the pagination row, which crowds the arrows."""
    import discord

    _seed()
    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await scenario.click(_bm("blocked-list.button.label"))

    view = scenario.current_message.view
    buttons = [item for item in view.children if isinstance(item, discord.ui.Button)]
    arrows = [b for b in buttons if b.label in ("|<", "<", ">", ">|")]
    filter_button = next(b for b in buttons if b.label == _bm("blocked-list.filter.label"))

    assert {b._rendered_row for b in arrows} == {0}
    assert filter_button._rendered_row == 1, (
        "the member filter belongs on its own row, under the pagination"
    )


async def test_record_uses_a_timestamp_each_client_translates(scenario_factory):
    """A formatted UTC string is never localized; <t:unix:R> is rendered by
    each reader's own client as "2 hours ago" in their language."""
    _seed()
    scenario = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await scenario.click(_bm("blocked-list.button.label"))

    rendered = str(scenario.expect_message(
        title_contains=_bm("blocked-list.embed.title")).get("embed"))
    assert "<t:" in rendered and ":R>" in rendered
    assert "UTC" not in rendered


async def test_stats_and_list_share_the_same_picture(scenario_factory):
    """Two views of one feature: a different thumbnail on each reads as two
    unrelated screens."""
    _seed()
    listing = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await listing.click(_bm("blocked-list.button.label"))
    list_thumbnail = (listing.expect_message(
        title_contains=_bm("blocked-list.embed.title")).get("embed") or {}).get("thumbnail")

    stats = await scenario_factory(locale="pt-br").start_manager("block_links", dict(COG))
    await stats.click(ml("buttons.stats.label", locale="pt-br"))
    stats_thumbnail = (stats.expect_message(
        title_contains=_bm("stats.embed.title")).get("embed") or {}).get("thumbnail")

    assert list_thumbnail and stats_thumbnail == list_thumbnail
