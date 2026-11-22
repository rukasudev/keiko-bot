"""Behavioral scenarios: /configurar, paged by category, from its first page down.

Every click edits the same message: the first page opens a category, a category
turns its pages and goes back, the server settings save each value and draw
themselves again (in the new language at once), every screen speaks the server's
language and only an admin changes anything, and a setup started here ends back on
the category page that holds the feature, marked as just turned on, while the
first screen of that setup offers Voltar back to the page it came from.
"""
from dataclasses import replace
from itertools import cycle, islice

import discord
import pytest

from app.commands.setup.service import category_page, open_setup_dashboard, settings_screen
from app.constants import Commands
from app.core import cache
from app.core.commands import setup_features
from app.core.utils import command_text, ml
from app.data import data
from tests.behavioral.flows.common import GUILD_ID
from tests.behavioral.harness import locators
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

LOCALE = "pt-br"
BASE = "setup"
SETTINGS = f"{BASE}.ui.cards.settings"
BACK = ml("buttons.back.label", LOCALE)
MANAGE = ml(f"{BASE}.ui.buttons.manage.label", LOCALE)
ACTIVATED = ml("commands.responses.enabled.title", LOCALE)
JUST_TURNED_ON = ml(f"{BASE}.ui.texts.states.activated", LOCALE)
ACTIVATION_LINE = ml("commands.responses.enabled.description", LOCALE).split("**")[0]


def _admin_scenario(scenario_factory, locale=LOCALE, denied=()):
    guild = create_guild()
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.guild_permissions.update(**{name: False for name in denied})
    user = create_member(guild, id=555, name="Admin")
    user.guild_permissions = discord.Permissions.all()
    return scenario_factory(locale=locale, guild=guild, user=user)


async def _configurar(scenario):
    return await scenario.run(open_setup_dashboard, "slash", name="/configurar")


def _sections(message):
    return [
        item
        for item in locators.walk_items(message.view)
        if isinstance(item, discord.ui.Section)
        and isinstance(item.accessory, discord.ui.Button)
    ]


def _text(section):
    return "\n".join(
        child.content
        for child in section.children
        if isinstance(child, discord.ui.TextDisplay)
    )


def _texts(message):
    return [
        item.content
        for item in locators.walk_items(message.view)
        if isinstance(item, discord.ui.TextDisplay)
    ]


def _labels(message):
    return [
        item.label
        for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button)
    ]


def _beside(message, wanted):
    """The button beside the row whose text holds `wanted`."""
    return next(
        section.accessory for section in _sections(message) if wanted in _text(section)
    )


def _beside_section(message, wanted):
    return next(section for section in _sections(message) if wanted in _text(section))


def _button(message, label):
    return next(
        item
        for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button) and item.label == label
    )


def _category(name, locale=LOCALE):
    return f"**{ml(f'{BASE}.ui.texts.categories.{name}', locale)}**"


def _category_title(name, locale=LOCALE):
    return ml(f"{BASE}.ui.texts.categories.{name}", locale)


def _page_note(page, pages, locale=LOCALE):
    note = ml(f"{BASE}.ui.cards.category.note", locale)
    return note.replace("$pages", str(pages)).replace("$page", str(page))


def _feature_name(key, locale=LOCALE):
    spec = next(spec for spec in setup_features() if spec.key == key)
    return command_text(spec, "command.setup.label", locale)


def _admin_only(locale=LOCALE):
    return ml("commands.responses.admin_only.title", locale)


@pytest.fixture
def six_moderations(monkeypatch):
    """Six features in moderations: one page and a second one with two rows."""
    from app.commands.setup import service as setup

    rows = [
        (replace(spec, category="moderations"), "pending")
        for spec in islice(cycle(setup_features()), 6)
    ]

    async def states(guild_id):
        return rows

    monkeypatch.setattr(setup, "_states", states)
    return rows


async def test_the_first_page_opens_a_category_turns_its_pages_and_comes_back(
    scenario_factory, six_moderations
):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = scenario.current_message

    await scenario.click(_beside(message, _category("moderations")))

    assert scenario.current_message is message, "the same message"
    assert any(_page_note(1, 2) in text for text in _texts(message))
    assert not any(f"{_page_note(1, 2)}s" in text for text in _texts(message))
    assert len(_sections(message)) == 4
    assert [event["kind"] for event in scenario.outputs[-2:]] == ["defer", "edit_original"]
    previous = _button(message, ml(f"{BASE}.ui.buttons.previous.label", LOCALE))
    assert previous.disabled and previous.style is discord.ButtonStyle.primary

    following = ml(f"{BASE}.ui.buttons.next.label", LOCALE)
    await scenario.click(_button(message, following))

    assert len(_sections(message)) == 2
    assert _button(message, following).disabled
    assert _labels(message)[-1] == BACK, "Voltar comes last"
    assert _button(message, BACK).style is discord.ButtonStyle.secondary, "grey, always"

    await scenario.click(_button(message, BACK))

    assert _category("moderations") in _text(_sections(message)[1])
    assert scenario.current_message is message
    assert len([event for event in scenario.outputs if event["kind"] == "send"]) == 1


@pytest.mark.parametrize("label,title,description", [
    ("buttons.help.label", "help.ui.records.commands.title",
     "help.ui.records.commands.description"),
    ("buttons.history.label", "setup.ui.records.history.title",
     "setup.ui.records.history.empty"),
    ("setup.ui.buttons.permissions.label", "setup.ui.responses.permissions.title",
     "setup.ui.responses.permissions.empty"),
], ids=["help", "history", "permissions"])
async def test_each_card_button_answers_privately_with_its_screen(
    scenario_factory, deps, label, title, description
):
    scenario = await _configurar(_admin_scenario(scenario_factory))

    await scenario.click(_button(scenario.current_message, ml(label, LOCALE)))

    answer = scenario.outputs[-1]
    assert answer["ephemeral"] is True
    assert answer["embed"]["title"] == ml(title, LOCALE)
    first_line = ml(description, LOCALE).split("\n")[0].strip()
    assert answer["embed"]["description"].startswith(first_line)


async def test_a_category_with_one_page_offers_only_voltar(scenario_factory, deps):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = scenario.current_message

    await scenario.click(_beside(message, _category("integrations")))

    assert not any(_page_note(1, 1) in text for text in _texts(message)), (
        "one page, no count"
    )
    rows = [
        item for item in message.view.children if isinstance(item, discord.ui.ActionRow)
    ]
    assert [button.label for button in rows[-1].children] == [BACK]
    assert rows[-1].children[0].style is discord.ButtonStyle.secondary, "grey, always"


async def test_every_configurar_screen_speaks_the_server_language(
    scenario_factory, deps
):
    """Lucas (PR15 round 2, f01): inside a server every Keiko string follows the
    language the server saved, not the language of the admin's Discord."""
    scenario = _admin_scenario(scenario_factory, locale="en-us")
    recorded_server(deps.mongo_client, scenario.guild.id, language="pt-br")
    scenario = await _configurar(scenario)
    message = scenario.current_message

    assert ml(f"{BASE}.ui.cards.dashboard.title", LOCALE) in _texts(message)[0]
    await scenario.click(_beside(message, _category("moderations")))
    assert _category_title("moderations") in _texts(message)[0]
    await scenario.click(_button(message, BACK))
    await _open_settings(scenario)
    assert ml(f"{SETTINGS}.title", LOCALE) in _texts(message)[0]


async def test_a_member_is_told_in_the_server_language_that_configurar_needs_an_admin(
    scenario_factory, deps
):
    guild = create_guild()
    member = create_member(guild, id=556, name="Member")
    member.guild_permissions = discord.Permissions.none()
    scenario = scenario_factory(locale="en-us", guild=guild, user=member)
    recorded_server(deps.mongo_client, guild.id, language="pt-br")

    await _configurar(scenario)

    assert scenario.outputs[-1]["embed"]["title"] == _admin_only("pt-br")


async def _open_settings(scenario):
    message = scenario.current_message
    await scenario.click(
        _beside(message, ml(f"{BASE}.ui.cards.dashboard.rows.settings.title", LOCALE))
    )
    return message


def _saved(deps):
    document = deps.mongo_client.guild.settings.find_one({"_id": GUILD_ID})
    return document["options"]


def _selects(message):
    return [
        item for item in locators.walk_items(message.view)
        if isinstance(item, discord.ui.Select)
    ]


def _placeholders(locale):
    return [
        ml(f"{SETTINGS}.rows.{row}.placeholder", locale)
        for row in ("language", "timezone", "prefix")
    ]


async def test_the_settings_screen_quotes_each_value_under_its_name(scenario_factory, deps):
    """Lucas (PR15 round 2, e12 and e17): values read as quotes, as the
    configuration cards show them, each with one line on what it is for."""
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    assert _texts(message)[2:5] == [
        "🌐 **Idioma**\n> 🇧🇷 Português\n-# A língua que eu falo neste servidor",
        "🕒 **Fuso horário**\n> 🇧🇷 Brasil (UTC-3)\n"
        "-# O fuso que eu sigo nos novos lembretes, como os de aniversário",
        "⌨️ **Prefixo**\n> `ks!`\n-# Com esse prefixo eu respondo no chat a comandos"
        " como `ks!ping`. O resto fica nos comandos com barra (/).",
    ]


@pytest.mark.parametrize("locale,line", [
    (
        "pt-br",
        "⌨️ **Prefixo**\n> `k?`\n-# Com esse prefixo eu respondo no chat a comandos"
        " como `k?ping`. O resto fica nos comandos com barra (/).",
    ),
    (
        "en-us",
        "⌨️ **Prefix**\n> `k?`\n-# With this prefix I answer commands like `k?ping`"
        " in the chat. Everything else lives in the slash (/) commands.",
    ),
])
async def test_the_prefix_line_names_the_servers_own_prefix(scenario_factory, deps, locale, line):
    recorded_server(deps.mongo_client, GUILD_ID, language=locale, prefix="k?")
    cache.reset()

    view = await settings_screen(GUILD_ID)

    texts = [
        item.content for item in locators.walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    ]
    assert texts[4] == line


async def test_the_settings_screen_has_no_mention_row(scenario_factory, deps):
    """Lucas (PR15 round 2, e15): who a message mentions is chosen per
    notification, so the server settings no longer hold a mention."""
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    assert not any("@everyone" in text for text in _texts(message))
    assert not [select for select in _selects(message) if isinstance(select, discord.ui.RoleSelect)]
    assert [select.placeholder for select in _selects(message)] == _placeholders(LOCALE)


async def test_the_prefix_is_picked_from_the_closed_list_in_order(scenario_factory, deps):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    prefix = _selects(message)[-1]
    assert [option.value for option in prefix.options] == list(Commands.SERVER_PREFIXES)
    assert [option.label for option in prefix.options] == list(Commands.SERVER_PREFIXES)
    assert _labels(message) == [BACK], "no modal to type a prefix"


@pytest.mark.parametrize("target,value,key,shown", [
    ("language", "en-us", Commands.SERVER_LANGUAGE_KEY, "> 🇺🇸 English"),
    ("timezone", "Asia/Tokyo", Commands.SERVER_TIMEZONE_KEY, "> 🇯🇵 Japão (UTC+9)"),
    ("prefix", "ke?", Commands.SERVER_PREFIX_KEY, "> `ke?`"),
])
async def test_the_settings_screen_saves_a_value_and_draws_it_again(
    scenario_factory, deps, analytics_events, target, value, key, shown
):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    await scenario.select_option(value, target=ml(f"{SETTINGS}.rows.{target}.placeholder", LOCALE))

    assert _saved(deps)[key] == value
    assert scenario.current_message is message
    assert any(shown in text for text in _texts(message)), "the new value is shown"
    changed = [event for event in analytics_events if event["type"] == "config.changed"]
    assert [(event["cog"], event["props"]) for event in changed] == [
        (
            Commands.SERVER_SETTINGS_KEY,
            {"source": "setup_dashboard", "changed_keys": [key], "changed_count": 1},
        )
    ]
    history = deps.mongo_client.events.changes.find(
        {"guild_id": GUILD_ID, "cog": Commands.SERVER_SETTINGS_KEY}, {"_id": False}
    )
    assert [record["type"] for record in history] == ["config.changed"]


async def test_a_new_language_redraws_the_settings_screen_in_it_at_once(
    scenario_factory, deps
):
    """Lucas (PR15 round 2, e12): after picking English the admin reads the
    screen in English, without opening it again."""
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    await scenario.select_option("en-us", target=_placeholders(LOCALE)[0])

    texts = _texts(message)
    assert ml(f"{SETTINGS}.title", "en-us") in texts[0]
    assert texts[2:5] == [
        "🌐 **Language**\n> 🇺🇸 English\n-# The language I speak in this server",
        "🕒 **Time zone**\n> 🇧🇷 Brazil (UTC-3)\n"
        "-# The time zone I follow in new reminders, like birthday ones",
        "⌨️ **Prefix**\n> `ks!`\n-# With this prefix I answer commands like `ks!ping`"
        " in the chat. Everything else lives in the slash (/) commands.",
    ]
    assert [select.placeholder for select in _selects(message)] == _placeholders("en-us")
    assert _labels(message) == [ml("buttons.back.label", "en-us")]


async def test_only_an_admin_changes_a_setting(scenario_factory, deps):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)
    scenario.user.guild_permissions = discord.Permissions.none()

    await scenario.select_option("en-us", target=_placeholders(LOCALE)[0])

    assert _saved(deps)[Commands.SERVER_LANGUAGE_KEY] == "pt-br"
    assert scenario.outputs[-1]["embed"]["title"] == _admin_only()
    assert scenario.current_message is message


async def test_voltar_on_the_settings_screen_goes_back_to_the_first_page(
    scenario_factory, deps
):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    await scenario.click(_button(message, BACK))

    assert _category("moderations") in _text(_sections(message)[1])


def _configure(deps, key, document=None, enabled=True):
    seed_cog(
        deps.mongo_client,
        key,
        {**(document or {}), "guild_id": GUILD_ID, Commands.ENABLED_KEY: enabled},
    )


async def test_a_category_row_is_its_name_and_what_it_does_without_a_health_line(
    scenario_factory, deps
):
    """Lucas (PR15 round 2, e10): the health line crowded the rows; how a
    feature did in the last days moves to its manager's statistics."""
    _configure(deps, "block_links")
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = scenario.current_message

    await scenario.click(_beside(message, _category("moderations")))

    row = _text(_beside_section(message, _feature_name("block_links")))
    assert row == (
        "🚫 **Bloquear Links ✅**\n"
        "-# Bloqueia links indesejados no servidor · `/moderações bloquear links`"
    )


async def test_a_paused_feature_offers_no_despausar_on_its_category_page(
    scenario_factory, deps
):
    """Lucas (PR15 round 2, e10): pausing and unpausing live on the feature's
    panel only; the category page keeps Gerenciar beside a paused feature."""
    _configure(deps, "block_links", enabled=False)
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = scenario.current_message

    await scenario.click(_beside(message, _category("moderations")))

    assert ml("buttons.unpause.label", LOCALE) not in _labels(message)
    assert _beside(message, _feature_name("block_links")).label == MANAGE


async def _set_up_default_roles(scenario):
    await scenario.confirm()
    await scenario.select_option("Member", target="default_roles")
    await scenario.select_option("Moderator", target="default_roles_bot")
    await scenario.click("done")
    await scenario.click("pick:False")
    await scenario.confirm()


def _title_line(message, key):
    return _text(_beside_section(message, _feature_name(key))).split("\n")[0]


async def test_a_setup_from_configurar_ends_on_its_category_marked_just_turned_on(
    scenario_factory, deps
):
    """Lucas (PR15 round 3, item 4): no "Ativei" line and no suggestion on the
    first page; the admin lands back on the category page that holds the
    feature, whose title says it was just turned on, for that render only."""
    scenario = await _configurar(_admin_scenario(scenario_factory))
    await scenario.click(_beside(scenario.current_message, _category("moderations")))
    await scenario.click(_beside(scenario.current_message, _feature_name("default_roles")))

    await _set_up_default_roles(scenario)

    message = scenario.current_message
    assert scenario.live_messages == [message], "the admin keeps one message"
    texts = _texts(message)
    assert _category_title("moderations") in texts[0], "the category page"
    assert JUST_TURNED_ON in _title_line(message, "default_roles")
    assert _beside(message, _feature_name("default_roles")).label == MANAGE
    assert not any(ACTIVATION_LINE in text or text.startswith("💡") for text in texts)
    assert not [
        event for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == ACTIVATED
    ]
    assert await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles")

    await scenario.click(_button(message, BACK))
    await scenario.click(_beside(message, _category("moderations")))

    assert JUST_TURNED_ON not in _title_line(message, "default_roles"), "not stored"
    assert "✅" in _title_line(message, "default_roles")


async def test_the_ending_opens_the_page_of_its_category_that_holds_the_feature(
    scenario_factory, six_moderations
):
    activated = six_moderations[-1][0].key
    view = await category_page(GUILD_ID, "moderations", activated=activated)

    texts = "\n".join(
        item.content for item in locators.walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    )
    assert _page_note(2, 2) in texts
    assert f"{_feature_name(activated)} {JUST_TURNED_ON}" in texts


async def test_the_last_setup_left_ends_on_its_category_then_the_first_page_congratulates(
    scenario_factory, deps
):
    """Lucas (PR15 round 3): the last feature set up ends on its category page
    like the others, and Voltar shows the first page congratulating the admin."""
    for spec in setup_features():
        if spec.key != "default_roles":
            _configure(deps, spec.key)
    scenario = await _configurar(_admin_scenario(scenario_factory))
    await scenario.click(_beside(scenario.current_message, _category("moderations")))
    await scenario.click(_beside(scenario.current_message, _feature_name("default_roles")))

    await _set_up_default_roles(scenario)

    texts = _texts(scenario.current_message)
    assert _category_title("moderations") in texts[0]
    assert not any(ACTIVATION_LINE in text or "tudo" in text.lower() for text in texts)
    assert JUST_TURNED_ON in _title_line(scenario.current_message, "default_roles")

    await scenario.click(_button(scenario.current_message, BACK))

    assert _texts(scenario.current_message)[:2] == [
        "## 🐶 Configuração do Servidor",
        "Parabéns, tudo configurado por aqui! Use os botões para ajustar o que quiser ✨",
    ]


async def test_a_setup_from_its_own_command_still_ends_on_its_panel(scenario_factory, deps):
    scenario = await _admin_scenario(scenario_factory).start_command("default_roles")

    await _set_up_default_roles(scenario)

    assert [
        event for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == ACTIVATED
    ]
    scenario.expect_mode("manage")
    assert not any(
        ml(f"{BASE}.ui.cards.dashboard.title", LOCALE) in text
        for text in _texts(scenario.current_message)
    )


async def test_voltar_on_a_setups_first_screen_returns_to_the_page_it_came_from(
    scenario_factory, six_moderations
):
    """Lucas (PR15 round 5, k03): a setup opened from /configurar offers Voltar,
    not Cancelar, on its first screen, and Voltar draws the category page the
    admin came from again, on its page, in the same message."""
    scenario = await _configurar(_admin_scenario(scenario_factory))
    await scenario.click(_beside(scenario.current_message, _category("moderations")))
    await scenario.click(
        _button(scenario.current_message, ml(f"{BASE}.ui.buttons.next.label", LOCALE))
    )

    await scenario.click(_sections(scenario.current_message)[0].accessory)

    labels = _labels(scenario.current_message)
    assert labels[-1] == BACK and ml("buttons.cancel.label", LOCALE) not in labels

    await scenario.click("back")

    message = scenario.current_message
    texts = _texts(message)
    assert _category_title("moderations") in texts[0], "the category page"
    assert any(_page_note(2, 2) in text for text in texts), "on the page it came from"
    assert scenario.live_messages == [message], "the admin keeps one message"
    await scenario.finish()
