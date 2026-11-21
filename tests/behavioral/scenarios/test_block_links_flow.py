"""Behavioral scenarios: /moderações bloquear links, from its card to its records.

Guaranteed: the card's defaults save in one Done; each mode explains itself on the
card's own select; Configurar opens the exceptions on a screen of their own (the
exempt channels and roles, the popular websites and the custom links in block all;
the channels and roles in block some, where the blocked links stay on the card),
and what is picked there is told on the card, the summary and the saved document;
a custom link is saved normalized, with the match-type question only past a bare
domain; Back and Cancel around the exceptions keep or drop the draft; the panel's
Edit opens the same card and keeps the saved list; and the panel offers the blocked
list and the stats over the server's real records, filtered by member, dated by a
timestamp each client translates.
"""
import json

import discord
import pytest

from app.core.utils import ml
from app.data import data
from tests.behavioral.flows import block_links
from tests.behavioral.flows.block_links import (
    ANSWER_FIELD,
    CHANNEL_EXCEPTIONS,
    CUSTOM_LINKS,
    DOMAIN_OPTION,
    EXCEPTIONS,
    LINK_FIELD,
)
from tests.behavioral.flows.common import (
    GUILD_ID,
    discard_label,
    keep_label,
    seed_document,
)
from tests.behavioral.harness.copy_keys import copy_text
from tests.generators.events import insert_blocked_link
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

LOCALE = "pt-br"
SECTIONS = "link_settings.sections"
MATCH_TYPE = "custom_links.steps.match_type"
EMPTY = ml("texts.empty_value", LOCALE)


def _step(key):
    return copy_text(f"block_links.form.steps.{key}", LOCALE)


def _label(section):
    return _step(f"{SECTIONS}.{section}.label")


def _site(host):
    return _label(f"allowed_links.options.{host}")


def _rendered_texts(components):
    found = []
    stack = list(components)
    while stack:
        component = stack.pop()
        if component.get("type") == "text":
            found.append(component.get("content") or "")
        stack.extend(component.get("children", []))
    return "\n".join(found)


def _card_texts(scenario):
    return _rendered_texts(scenario.expect_message(components_v2=True)["components"])


def _heading(scenario):
    """The screen's `##` title, without its markdown."""
    line = next(
        line for line in _card_texts(scenario).split("\n") if line.startswith("## ")
    )
    return line[3:]


def _button_labels(scenario):
    return [
        item.label
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


def _shown(name, value):
    return f"**{name}:** {value}"


async def test_happy_path_with_defaults_needs_only_done(scenario_factory):
    """The card ships with mode=block_all and the default answer prefilled:
    accepting the defaults is a single Done click."""
    scenario = await block_links.open_card(scenario_factory, LOCALE)
    default_answer = _step("link_settings.defaults.answer")

    scenario.expect_message(components_v2=True)
    assert scenario.card_state["mode"] == "block_all"
    assert scenario.card_state["answer"] == default_answer

    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", block_links.FORM, {"guild_id": GUILD_ID},
        {"mode": "block_all", "answer": default_answer, "enabled": True},
    )
    assert document["custom_links"]["values"] == []
    await scenario.finish()


async def test_block_all_full_flow_persists_domains_and_custom_entry(scenario_factory):
    scenario = await block_links.open_card(scenario_factory, LOCALE)

    await scenario.click("customize:answer")
    await scenario.submit_modal({ANSWER_FIELD[LOCALE]: "Sem links, {user}!"})
    await scenario.click(EXCEPTIONS)
    await scenario.select_option(["youtube.com", "twitch.tv"], target="allowed_links")
    await scenario.select_option("general", target="allowed_chats")

    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({LINK_FIELD[LOCALE]: "https://Meusite.com.br/promo/"})

    match_text = (scenario.expect_message().get("embed") or {}).get("description") or ""
    assert "meusite.com.br/promo" in match_text, "the question names the link typed"
    assert "https://" not in match_text.split("!")[0]

    await scenario.click(DOMAIN_OPTION[LOCALE])

    scenario.expect_step("link_settings")
    await scenario.click("back")
    await scenario.click("done")
    scenario.expect_step("confirm")

    resume = scenario.expect_message(components_v2=True)
    assert "`meusite.com.br/promo`" in json.dumps(resume, ensure_ascii=False)

    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", block_links.FORM, {"guild_id": GUILD_ID},
        {"mode": "block_all", "answer": "Sem links, {user}!"},
    )
    assert sorted(document["allowed_links"]["values"]) == ["twitch.tv", "youtube.com"]
    general = str(scenario.guild.text_channels[0].id)
    assert document["allowed_chats"] == {"style": "channel", "values": general}
    assert document["allowed_roles"] == {"style": "role", "values": []}
    entry = document["custom_links"]["values"][0]
    assert entry["link"]["value"] == "meusite.com.br/promo"
    assert entry["match_type"]["_raw_value"] == "domain"
    assert entry["match_type"]["value"] == _step(f"{MATCH_TYPE}.options.domain.label")
    await scenario.finish()


async def test_bare_domain_skips_the_match_type_question(scenario_factory):
    """Typing just a domain (youtube.com) IS "the whole website": the
    match-type step only appears when the link goes beyond the domain."""
    scenario = await block_links.open_card(scenario_factory, LOCALE)
    await scenario.click(EXCEPTIONS)
    await scenario.click(CUSTOM_LINKS)

    await scenario.submit_modal({LINK_FIELD[LOCALE]: "https://www.meusite.com.br/"})

    scenario.expect_step("link_settings")
    await scenario.click("back")
    await scenario.click("done")
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", block_links.FORM, {"guild_id": GUILD_ID}, {"mode": "block_all"},
    )
    entry = document["custom_links"]["values"][0]
    assert entry["link"]["value"] == "meusite.com.br"
    assert "match_type" not in entry, "no question, so the whole website"
    await scenario.finish()


async def test_mode_picker_explains_each_mode_in_its_own_line(scenario_factory):
    """Choosing the blocking mode is the most consequential decision of the
    command: the choice must spell out what each mode does instead of showing
    two bare labels. Since beta.2 round 2 (N4) the mode is picked on the card's
    own select, each mode an option with its explanation under its name, and
    the mode in force chosen in it."""
    scenario = await block_links.open_card(scenario_factory, LOCALE)

    mode = next(
        item
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.Select) and item.custom_id.endswith(":mode")
    )
    modes = ("allow_all", "block_all")
    assert [(option.label, option.description) for option in mode.options] == [
        (_label(f"mode.options.{key}"), _step(f"{SECTIONS}.mode.options.{key}.description"))
        for key in modes
    ]
    assert all(option.description for option in mode.options)
    assert [option.default for option in mode.options] == [False, True]
    await scenario.finish()


async def test_allow_all_hides_popular_websites_and_requires_entries(scenario_factory):
    """A section's when: switching the mode removes the popular-websites section
    from the card, and the blocked links are added from the card's own list."""
    scenario = await block_links.open_card(scenario_factory, LOCALE)

    await scenario.select_option("allow_all", target="mode")

    rendered = _card_texts(scenario)
    assert _label("allowed_links") not in rendered, "hidden section must not render"
    assert _step("custom_links.overrides.0.title") in rendered, "the list names the mode"
    await scenario.click(CUSTOM_LINKS)
    scenario.expect_step("link")

    await scenario.submit_modal({LINK_FIELD[LOCALE]: "bit.ly/golpe"})
    await scenario.click(f"option:{_step(f'{MATCH_TYPE}.options.exact.label')}")

    scenario.expect_step("link_settings")
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", block_links.FORM, {"guild_id": GUILD_ID}, {"mode": "allow_all"},
    )
    entry = document["custom_links"]["values"][0]
    assert entry["link"]["value"] == "bit.ly/golpe"
    assert entry["match_type"]["_raw_value"] == "exact"
    await scenario.finish()


async def test_block_all_exceptions_open_on_their_own_screen_and_show_on_the_card(
    scenario_factory,
):
    """Round 5 E (v05, v06, v07): the card asks "Adicionar exceções?" with what is
    chosen as its lines and a Configurar; Configurar opens a screen with only
    the exceptions and one Voltar; a channel and a popular website picked there
    are told on the card after Voltar, on the summary and in the document."""
    scenario = await block_links.open_card(scenario_factory, LOCALE)
    general = str(scenario.guild.text_channels[0].id)
    chats = _label("exempt.selects.allowed_chats")
    exceptions_title = copy_text("vocabulary.texts.exceptions", LOCALE)

    card = _card_texts(scenario)
    assert f"**{_label('exceptions')}**" in card
    assert _step(f"{SECTIONS}.exceptions.description") in card
    assert _shown(chats, EMPTY) in card
    assert _shown(_label("allowed_links"), EMPTY) in card
    assert _shown(_label("own_links"), EMPTY) in card
    assert _label("exempt") not in card, "the sections are on Configurar"
    assert _step(f"{SECTIONS}.exceptions.button.label") in _button_labels(scenario)

    await scenario.click(EXCEPTIONS)

    exceptions = _card_texts(scenario)
    assert _heading(scenario).endswith(exceptions_title)
    for section in ("exempt", "allowed_links", "own_links"):
        assert f"**{_label(section)}**" in exceptions, section
    assert _label("mode") not in exceptions
    moves = [ml(f"buttons.{name}.label", LOCALE) for name in ("back", "done", "cancel")]
    assert [label for label in _button_labels(scenario) if label in moves] == [moves[0]]

    await scenario.select_option("general", target="allowed_chats")
    await scenario.select_option(["youtube.com"], target="allowed_links")
    assert _heading(scenario).endswith(exceptions_title), "a pick keeps the screen"
    await scenario.click("back")

    card = _card_texts(scenario)
    assert _shown(chats, f"<#{general}>") in card
    assert _shown(_label("allowed_links"), _site("youtube.com")) in card
    await scenario.click("done")
    scenario.expect_step("confirm")
    summary = _card_texts(scenario)
    assert f"<#{general}>" in summary
    assert "youtube.com" in summary.lower()
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", block_links.FORM, {"guild_id": GUILD_ID}, {"mode": "block_all"},
    )
    assert document["allowed_chats"] == {"style": "channel", "values": general}
    assert document["allowed_links"]["values"] == ["youtube.com"]
    assert document["custom_links"]["values"] == []
    await scenario.finish()


async def test_block_some_exceptions_are_channels_and_roles_and_the_blocked_links_stay(
    scenario_factory,
):
    """Round 5 E: in "Bloquear alguns" Configurar holds only the channels and
    roles, while "Links personalizados bloqueados" stays on the card."""
    scenario = await block_links.open_card(scenario_factory, LOCALE)
    await scenario.select_option("allow_all", target="mode")
    general = str(scenario.guild.text_channels[0].id)
    blocked = _step("custom_links.overrides.0.title")

    card = _card_texts(scenario)
    assert _step(f"{SECTIONS}.channel_exceptions.description") in card
    assert _label("allowed_links") not in card
    assert f"**{blocked}**" in card

    await scenario.click(CHANNEL_EXCEPTIONS)
    exceptions = _card_texts(scenario)
    assert f"**{_label('exempt')}**" in exceptions
    assert blocked not in exceptions and _label("own_links") not in exceptions
    await scenario.select_option("general", target="allowed_chats")
    await scenario.click("back")

    chats = _label("exempt.selects.allowed_chats")
    assert _shown(chats, f"<#{general}>") in _card_texts(scenario)
    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({LINK_FIELD[LOCALE]: "spam.com"})
    scenario.expect_step("link_settings")
    assert "- `spam.com`" in _card_texts(scenario), "the blocked list is on the card"
    await scenario.click("done")
    summary = _card_texts(scenario)
    assert f"<#{general}>" in summary and "spam.com" in summary
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", block_links.FORM, {"guild_id": GUILD_ID}, {"mode": "allow_all"},
    )
    assert document["allowed_chats"] == {"style": "channel", "values": general}
    assert document["custom_links"]["values"][0]["link"]["value"] == "spam.com"
    await scenario.finish()


async def test_back_and_cancel_around_the_exceptions_keep_or_drop_the_draft(
    scenario_factory,
):
    """Round 5 E: Voltar from the exceptions keeps every pick in the card's draft,
    so does keeping the card after Cancel, the summary's Editar opens the card
    with the exceptions closed and the same Configurar, and discarding saves
    nothing."""
    scenario = await block_links.open_card(scenario_factory, LOCALE)
    await scenario.click(EXCEPTIONS)
    await scenario.select_option(["twitch.tv"], target="allowed_links")
    await scenario.click("back")
    assert scenario.card_state["allowed_links"] == ["twitch.tv"]

    await scenario.click("cancel")
    await scenario.click(keep_label(LOCALE))
    assert scenario.card_state["allowed_links"] == ["twitch.tv"]
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.click("edit:link_settings")
    card = _card_texts(scenario)
    assert f"**{_label('exceptions')}**" in card
    assert _shown(_label("allowed_links"), _site("twitch.tv")) in card
    await scenario.click(EXCEPTIONS)
    assert _heading(scenario).endswith(copy_text("vocabulary.texts.exceptions", LOCALE))
    await scenario.click("back")
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.click("cancel")
    await scenario.click(discard_label(LOCALE))

    assert await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, block_links.FORM) is None
    await scenario.finish()


async def test_the_panel_edit_opens_the_same_card_with_the_same_configure(
    scenario_factory, deps
):
    """Round 5 E: the panel's Editar of "Configurações Gerais" opens the same
    card, and its Configurar the same exceptions screen; Confirmar saves what
    was picked there and keeps the saved list."""
    seed_document(deps, block_links.FORM, {
        "guild_id": GUILD_ID,
        "enabled": True,
        "mode": "block_all",
        "answer": "Sem links!",
        "allowed_chats": {"style": "channel", "values": []},
        "allowed_roles": {"style": "role", "values": []},
        "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
        "custom_links": {
            "style": "composition",
            "values": [{"link": {"value": "meusite.com.br", "style": "code"}}],
        },
    })
    scenario = await scenario_factory(locale=LOCALE).start_command(block_links.FORM)
    sites = _label("allowed_links")

    await scenario.click("section:link_settings")
    card = _card_texts(scenario)
    assert f"**{_label('exceptions')}**" in card
    assert _shown(sites, _site("youtube.com")) in card
    await scenario.click(EXCEPTIONS)
    assert _heading(scenario).endswith(copy_text("vocabulary.texts.exceptions", LOCALE))
    assert f"**{sites}**" in _card_texts(scenario)
    await scenario.select_option(["youtube.com", "spotify.com"], target="allowed_links")
    await scenario.click("back")
    both = f"{_site('youtube.com')}, {_site('spotify.com')}"
    assert _shown(sites, both) in _card_texts(scenario)
    await scenario.click("done")

    scenario.expect_component(label_or_action="pause")
    document = scenario.expect_persisted(
        "guild", block_links.FORM, {"guild_id": GUILD_ID}, {"mode": "block_all"},
    )
    assert sorted(document["allowed_links"]["values"]) == ["spotify.com", "youtube.com"]
    assert document["custom_links"]["values"][0]["link"]["value"] == "meusite.com.br"
    await scenario.finish()


RECORDS_COG = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "mode": "block_all",
    "allowed_links": {"style": "bullet", "values": ["youtube.com"]},
    "custom_links": {"style": "composition", "values": []},
    "answer": "Nada de links por aqui!",
}


def _bm(key: str) -> str:
    return ml(f"block_links.{key}", locale=LOCALE)


def _block(user_id="111", host="spam-site.com", deleted=True):
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


async def _records_panel(scenario_factory, guild=None):
    return await scenario_factory(locale=LOCALE, guild=guild).start_manager(
        block_links.FORM, dict(RECORDS_COG)
    )


async def _blocked_list(scenario_factory, guild=None):
    scenario = await _records_panel(scenario_factory, guild)
    await scenario.click(_bm("ui.buttons.blocked_list.label"))
    return scenario


def _listing(scenario):
    return str(scenario.expect_message(
        title_contains=_bm("ui.records.blocked_list.title")
    ).get("embed"))


def _stats(scenario):
    return scenario.expect_message(
        title_contains=_bm("command.stats.title")
    ).get("embed") or {}


async def test_manager_offers_the_blocks_and_stats_buttons_and_the_app_command_tip(
    scenario_factory,
):
    """The blocked-link records are the raw material of two buttons (the list and
    the stats) and of the app command tip."""
    scenario = await _records_panel(scenario_factory)

    scenario.expect_message(kind="send", ephemeral=True)
    scenario.expect_component(label_or_action=_bm("ui.buttons.blocked_list.label"))
    scenario.expect_component(label_or_action=ml("buttons.stats.label", locale=LOCALE))
    assert _bm("command.panel.info.description") in scenario.rendered_summary, (
        "the manager must teach how to reach the diagnostic app command"
    )


async def test_blocked_list_shows_the_records_of_the_server(scenario_factory):
    _block()
    _block(user_id="222", host="outro-site.com")

    rendered = _listing(await _blocked_list(scenario_factory))

    assert "spam-site.com" in rendered
    assert "outro-site.com" in rendered
    assert "<@111>" in rendered and "<#100>" in rendered


async def test_blocked_list_warns_about_messages_it_could_not_delete(scenario_factory):
    _block(deleted=False)

    rendered = _listing(await _blocked_list(scenario_factory))

    assert _bm("ui.records.blocked_list.lines.not_deleted") in rendered


async def test_blocked_list_says_so_when_there_is_nothing_yet(scenario_factory):
    scenario = await _blocked_list(scenario_factory)

    scenario.expect_message(description_contains=_bm("ui.records.blocked_list.empty"))


async def test_stats_button_reports_the_numbers(scenario_factory):
    _block()
    _block(host="spam-site.com")
    _block(user_id="222", host="outro-site.com")

    scenario = await _records_panel(scenario_factory)
    await scenario.click(ml("buttons.stats.label", locale=LOCALE))

    description = _stats(scenario).get("description") or ""
    assert _bm("command.stats.figures.total") in description
    assert "spam-site.com" in description, "the most blocked website must show up"
    assert "<@111>" in description, "the member with most blocks must show up"


async def test_stats_button_says_so_on_an_empty_server(scenario_factory):
    scenario = await _records_panel(scenario_factory)
    await scenario.click(ml("buttons.stats.label", locale=LOCALE))

    scenario.expect_message(description_contains=_bm("command.stats.empty"))


async def test_blocked_list_can_be_filtered_by_member(scenario_factory):
    """The "search by user" of the feature: pick a member on the native user
    select and the list narrows to that member only."""
    guild = create_guild()
    member = create_member(guild, id=111, name="Culpado", roles=["Member"])
    create_member(guild, id=222, name="Inocente", roles=["Member"])
    _block(user_id=str(member.id), host="spam-site.com")
    _block(user_id="222", host="outro-site.com")

    scenario = await _blocked_list(scenario_factory, guild)
    await scenario.click(_bm("ui.records.blocked_list.filter.label"))

    scenario.expect_message(title_contains=_bm("ui.records.blocked_list.filter.title"))
    await scenario.select_option("Culpado")
    await scenario.confirm()

    filtered = _listing(scenario)
    assert "spam-site.com" in filtered
    assert "outro-site.com" not in filtered, "the filter must exclude other members"


async def test_filter_says_so_when_the_member_has_no_blocks(scenario_factory):
    guild = create_guild()
    create_member(guild, id=222, name="Inocente", roles=["Member"])
    _block(user_id="111", host="spam-site.com")

    scenario = await _blocked_list(scenario_factory, guild)
    await scenario.click(_bm("ui.records.blocked_list.filter.label"))
    await scenario.select_option("Inocente")
    await scenario.confirm()

    scenario.expect_message(
        description_contains=_bm("ui.records.blocked_list.filter.empty")
    )


async def test_filter_button_sits_below_the_pagination_row(scenario_factory):
    """Without an explicit row discord.py first-fits the extra button into the
    free slot of the pagination row, which crowds the arrows."""
    _block()
    scenario = await _blocked_list(scenario_factory)

    rows = [
        [button.label for button in row.children]
        for row in scenario.current_message.components
    ]
    filter_label = _bm("ui.records.blocked_list.filter.label")
    assert filter_label not in rows[0], "the arrows keep their row"
    assert rows[1] == [filter_label], (
        "the member filter belongs on its own row, under the pagination"
    )


async def test_record_uses_a_timestamp_each_client_translates(scenario_factory):
    """A formatted UTC string is never localized; <t:unix:R> is rendered by
    each reader's own client as "2 hours ago" in their language."""
    _block()

    rendered = _listing(await _blocked_list(scenario_factory))

    assert "<t:" in rendered and ":R>" in rendered
    assert "UTC" not in rendered


async def test_stats_and_list_share_the_same_picture(scenario_factory):
    """Two views of one feature: a different thumbnail on each reads as two
    unrelated screens."""
    _block()
    listing = await _blocked_list(scenario_factory)
    list_thumbnail = (listing.expect_message(
        title_contains=_bm("ui.records.blocked_list.title")
    ).get("embed") or {}).get("thumbnail")

    stats = await _records_panel(scenario_factory)
    await stats.click(ml("buttons.stats.label", locale=LOCALE))

    assert list_thumbnail and _stats(stats).get("thumbnail") == list_thumbnail
