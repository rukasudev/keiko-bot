"""Behavioral scenario: /configurar, paged by category, from its first page down.

Every click edits the same message: the first page opens a category, a
category turns its pages and goes back, the server settings save each value
and draw themselves again, and a setup
started here ends back on the category page that holds the feature.
"""
from itertools import cycle, islice

import discord
import pytest

from app.data import data
from app.constants import Commands
from app.core.utils import ml
from app.settings.form.form_state import Manage
from tests.behavioral.golden.paths.common import GUILD_ID
from tests.behavioral.harness import locators
from tests.generators.moderations import recorded_server, seed_cog
from tests.mocks.discord import create_guild, create_member

pytestmark = pytest.mark.behavioral

BASE = "commands.commands.setup"
SETTINGS = f"{BASE}.settings"


def _admin_scenario(scenario_factory, locale="pt-br", denied=()):
    guild = create_guild()
    guild.me.guild_permissions = discord.Permissions.all()
    guild.me.guild_permissions.update(**{name: False for name in denied})
    user = create_member(guild, id=555, name="Admin")
    user.guild_permissions = discord.Permissions.all()
    return scenario_factory(locale=locale, guild=guild, user=user)


async def _configurar(scenario):
    from app.services.setup import open_setup_dashboard

    scenario.store.record("start", actor="user", target="/configurar")
    await open_setup_dashboard(scenario._mint(), "slash")
    return scenario


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


def _beside(message, wanted):
    """The button beside the row whose text holds `wanted`."""
    return next(
        section.accessory for section in _sections(message) if wanted in _text(section)
    )


def _button(message, label):
    return next(
        item
        for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button) and item.label == label
    )


async def _press(scenario, button):
    message = scenario.current_message
    scenario.store.record("click", actor="user", message=message.id, target=button.label)
    await locators.dispatch_click(message.view, button, scenario._mint(message=message))


def _category(name, locale="pt-br"):
    return f"**{ml(f'{BASE}.embed.categories.{name}', locale)}**"


def _feature_name(key, locale="pt-br"):
    spec = next(spec for spec in Commands.SETUP_FEATURES if spec["command_key"] == key)
    return ml(f"buttons.setup.{spec['button_key']}.label", locale)


def _edits(scenario):
    return [
        event for event in scenario.outputs
        if event.get("actor") == "bot" and event.get("components_v2")
    ]


@pytest.fixture
def six_moderations(monkeypatch):
    """Six features in moderations: one page and a second one with two rows."""
    from app.services import setup

    rows = [
        ({**spec, "category": "moderations"}, "pending")
        for spec in islice(cycle(Commands.SETUP_FEATURES), 6)
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

    await _press(scenario, _beside(message, _category("moderations")))

    assert scenario.current_message is message, "the same message"
    assert any("Página 1 de 2" in text for text in _texts(message))
    assert not any("Página 1 de 2s" in text for text in _texts(message))
    assert len(_sections(message)) == 4
    assert [event["kind"] for event in scenario.outputs[-2:]] == ["defer", "edit_original"]
    previous = _button(message, "Anterior")
    assert previous.disabled and previous.style is discord.ButtonStyle.primary

    await _press(scenario, _button(message, "Próxima"))

    assert len(_sections(message)) == 2
    assert _button(message, "Próxima").disabled
    labels = [
        item.label for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button)
    ]
    assert labels[-1] == "Voltar", "Voltar comes last"
    voltar = _button(message, "Voltar")
    assert voltar.style is discord.ButtonStyle.secondary, "grey, always"

    await _press(scenario, _button(message, "Voltar"))

    assert _category("moderations") in _text(_sections(message)[1])
    assert scenario.current_message is message
    assert len([event for event in scenario.outputs if event["kind"] == "send"]) == 1


@pytest.mark.parametrize("label,title,description", [
    (
        "Ajuda",
        "📚 Comandos disponíveis",
        "Aqui estão meus comandos que irão te ajudar",
    ),
    (
        "Histórico",
        "📜 Histórico de alterações",
        "Ainda não tem nenhuma mudança registrada por aqui.",
    ),
    (
        "Permissões",
        "🩺 Permissões do Keiko",
        "Nenhuma funcionalidade configurada ainda, então não tenho o que conferir.",
    ),
])
async def test_each_card_button_answers_privately_with_its_screen(
    scenario_factory, deps, label, title, description
):
    scenario = await _configurar(_admin_scenario(scenario_factory))

    await _press(scenario, _button(scenario.current_message, label))

    answer = scenario.outputs[-1]
    assert answer["ephemeral"] is True
    assert answer["embed"]["title"] == title
    assert answer["embed"]["description"].startswith(description)


async def test_a_category_with_one_page_offers_only_voltar(scenario_factory, deps):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = scenario.current_message

    await _press(scenario, _beside(message, _category("notifications")))

    assert not any("Página" in text for text in _texts(message)), "one page, no count"
    rows = [
        item for item in message.view.children if isinstance(item, discord.ui.ActionRow)
    ]
    assert [button.label for button in rows[-1].children] == ["Voltar"]
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

    assert "## 🐶 Configuração do Servidor" in _texts(message)[0]
    await _press(scenario, _beside(message, _category("moderations")))
    assert _texts(message)[0] == "## 🛡️ Moderação"
    await _press(scenario, _button(message, "Voltar"))
    await _open_settings(scenario)
    assert "## ⚙️ Configurações do Servidor" in _texts(message)[0]


async def test_a_member_is_told_in_the_server_language_that_configurar_needs_an_admin(
    scenario_factory, deps
):
    guild = create_guild()
    member = create_member(guild, id=556, name="Member")
    member.guild_permissions = discord.Permissions.none()
    scenario = scenario_factory(locale="en-us", guild=guild, user=member)
    recorded_server(deps.mongo_client, guild.id, language="pt-br")

    await _configurar(scenario)

    assert scenario.outputs[-1]["embed"]["title"] == "⚠️ Apenas Admins"


async def _open_settings(scenario):
    message = scenario.current_message
    await _press(scenario, _beside(message, ml(f"{SETTINGS}.row", "pt-br")))
    return message


def _saved(deps):
    document = deps.mongo_client.guild.settings.find_one({"_id": GUILD_ID})
    return document["options"]


def _selects(message):
    return [
        item for item in locators.walk_items(message.view)
        if isinstance(item, discord.ui.Select)
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
    from app.core import cache
    from app.services.setup import settings_screen

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

    assert not any("Menção" in text or "@everyone" in text for text in _texts(message))
    assert not [select for select in _selects(message) if isinstance(select, discord.ui.RoleSelect)]
    assert [select.placeholder for select in _selects(message)] == [
        "Alterar idioma", "Alterar fuso horário", "Alterar prefixo",
    ]


async def test_the_prefix_is_picked_from_the_closed_list_in_order(scenario_factory, deps):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    prefix = _selects(message)[-1]
    assert [option.value for option in prefix.options] == list(Commands.SERVER_PREFIXES)
    assert [option.label for option in prefix.options] == [
        "k!", "ke!", "ke?", "ke$", "kb!", "kb?", "kb$", "k?", "k$",
    ]
    assert not [
        item for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button) and item.label == "Alterar"
    ], "no modal to type a prefix"


@pytest.mark.parametrize("target,value,key,shown", [
    ("language-placeholder", "en-us", Commands.SERVER_LANGUAGE_KEY, "> 🇺🇸 English"),
    ("timezone-placeholder", "Asia/Tokyo", Commands.SERVER_TIMEZONE_KEY, "> 🇯🇵 Japão (UTC+9)"),
    ("prefix-placeholder", "ke?", Commands.SERVER_PREFIX_KEY, "> `ke?`"),
])
async def test_the_settings_screen_saves_a_value_and_draws_it_again(
    scenario_factory, deps, analytics_events, target, value, key, shown
):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    await scenario.select_option(value, target=ml(f"{SETTINGS}.{target}", "pt-br"))

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

    await scenario.select_option("en-us", target="Alterar idioma")

    texts = _texts(message)
    assert "## ⚙️ Server Settings" in texts[0]
    assert texts[2:5] == [
        "🌐 **Language**\n> 🇺🇸 English\n-# The language I speak in this server",
        "🕒 **Time zone**\n> 🇧🇷 Brazil (UTC-3)\n"
        "-# The time zone I follow in new reminders, like birthday ones",
        "⌨️ **Prefix**\n> `ks!`\n-# With this prefix I answer commands like `ks!ping`"
        " in the chat. Everything else lives in the slash (/) commands.",
    ]
    assert [select.placeholder for select in _selects(message)] == [
        "Change language", "Change time zone", "Change prefix",
    ]
    assert [
        item.label for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button)
    ] == ["Back"]


async def test_only_an_admin_changes_a_setting(scenario_factory, deps):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)
    scenario.user.guild_permissions = discord.Permissions.none()

    await scenario.select_option("en-us", target=ml(f"{SETTINGS}.language-placeholder", "pt-br"))

    assert _saved(deps)[Commands.SERVER_LANGUAGE_KEY] == "pt-br"
    assert scenario.outputs[-1]["embed"]["title"] == "⚠️ Apenas Admins"
    assert scenario.current_message is message


async def test_voltar_on_the_settings_screen_goes_back_to_the_first_page(
    scenario_factory, deps
):
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = await _open_settings(scenario)

    await _press(scenario, _button(message, "Voltar"))

    assert _category("moderations") in _text(_sections(message)[1])


def _configure(deps, key, document=None, enabled=True):
    seed_cog(deps.mongo_client, key, {**(document or {}), "guild_id": GUILD_ID, Commands.ENABLED_KEY: enabled})


def _beside_section(message, wanted):
    return next(section for section in _sections(message) if wanted in _text(section))


async def test_a_category_row_is_its_name_and_what_it_does_without_a_health_line(
    scenario_factory, deps
):
    """Lucas (PR15 round 2, e10): the health line crowded the rows; how a
    feature did in the last days moves to its manager's statistics."""
    _configure(deps, Commands.BLOCK_LINKS_KEY)
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = scenario.current_message

    await _press(scenario, _beside(message, _category("moderations")))

    row = _text(_beside_section(message, _feature_name(Commands.BLOCK_LINKS_KEY)))
    assert row == (
        "🚫 **Bloquear Links ✅**\n"
        "-# Bloqueia links indesejados no servidor · `/moderações bloquear links`"
    )


async def test_a_paused_feature_offers_no_despausar_on_its_category_page(
    scenario_factory, deps
):
    """Lucas (PR15 round 2, e10): pausing and unpausing live on the feature's
    panel only; the category page keeps Gerenciar beside a paused feature."""
    _configure(deps, Commands.BLOCK_LINKS_KEY, enabled=False)
    scenario = await _configurar(_admin_scenario(scenario_factory))
    message = scenario.current_message

    await _press(scenario, _beside(message, _category("moderations")))

    labels = [
        item.label for item in locators.clickable_items(message)
        if isinstance(item, discord.ui.Button)
    ]
    assert "Despausar" not in labels
    assert _beside(message, _feature_name(Commands.BLOCK_LINKS_KEY)).label == "Gerenciar"


async def _set_up_default_roles(scenario):
    await scenario.confirm()
    await scenario.select_option("Member", target="default_roles")
    await scenario.select_option("Moderator", target="default_roles_bot")
    await scenario.click("done")
    await scenario.click("pick:False")
    await scenario.confirm()


async def test_a_setup_from_configurar_ends_on_its_category_marked_just_turned_on(
    scenario_factory, deps
):
    """Lucas (PR15 round 3, item 4): no "Ativei" line and no suggestion on the
    first page; the admin lands back on the category page that holds the
    feature, whose title says it was just turned on, for that render only."""
    scenario = await _configurar(_admin_scenario(scenario_factory))
    await _press(scenario, _beside(scenario.current_message, _category("moderations")))
    await _press(scenario, _beside(scenario.current_message, _feature_name("default_roles")))

    await _set_up_default_roles(scenario)

    message = scenario.current_message
    live = [message for message in scenario.store.messages.values() if not message.deleted]
    assert live == [message], "the admin keeps one message"
    texts = _texts(message)
    assert texts[0] == "## 🛡️ Moderação", "the category page"
    row = _text(_beside_section(message, _feature_name("default_roles")))
    assert row.split("\n")[0] == "👩‍🎓 **Cargos Padrão (Recentemente Ativado 🎉)**"
    assert _beside(message, _feature_name("default_roles")).label == "Gerenciar"
    assert not any("Ativei" in text or text.startswith("💡") for text in texts)
    title = "🎉 Funcionalidade ativada com sucesso!"
    assert not [
        event for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == title
    ]
    assert await data().guild_cogs.find_cog_by_guild_id(GUILD_ID, "default_roles")

    await _press(scenario, _button(message, "Voltar"))
    await _press(scenario, _beside(message, _category("moderations")))

    row = _text(_beside_section(message, _feature_name("default_roles")))
    assert row.split("\n")[0] == "👩‍🎓 **Cargos Padrão ✅**", "not stored"


async def test_the_ending_opens_the_page_of_its_category_that_holds_the_feature(
    scenario_factory, six_moderations
):
    from app.services.setup import category_page

    view = await category_page(
        GUILD_ID, "moderations", activated=Commands.REMINDERS_BIRTHDAY_KEY
    )

    texts = [
        item.content for item in locators.walk_items(view)
        if isinstance(item, discord.ui.TextDisplay)
    ]
    assert any("Página 2 de 2" in text for text in texts)
    assert "🎂 **Lembretes de Aniversário (Recentemente Ativado 🎉)**" in "\n".join(texts)


async def test_the_last_setup_left_ends_on_its_category_then_the_first_page_congratulates(
    scenario_factory, deps
):
    """Lucas (PR15 round 3): the last feature set up ends on its category page
    like the others, and Voltar shows the first page congratulating the admin."""
    for spec in Commands.SETUP_FEATURES:
        if spec["command_key"] != "default_roles":
            _configure(deps, spec["command_key"])
    scenario = await _configurar(_admin_scenario(scenario_factory))
    await _press(scenario, _beside(scenario.current_message, _category("moderations")))
    await _press(scenario, _beside(scenario.current_message, _feature_name("default_roles")))

    await _set_up_default_roles(scenario)

    texts = _texts(scenario.current_message)
    assert texts[0] == "## 🛡️ Moderação"
    assert not any("Ativei" in text or "tudo" in text.lower() for text in texts)
    row = _text(_beside_section(scenario.current_message, _feature_name("default_roles")))
    assert row.split("\n")[0] == "👩‍🎓 **Cargos Padrão (Recentemente Ativado 🎉)**"

    await _press(scenario, _button(scenario.current_message, "Voltar"))

    assert _texts(scenario.current_message)[:2] == [
        "## 🐶 Configuração do Servidor",
        "Parabéns, tudo configurado por aqui! Use os botões para ajustar o que quiser ✨",
    ]


async def test_a_setup_from_its_own_command_still_ends_on_its_panel(scenario_factory, deps):
    scenario = await _admin_scenario(scenario_factory).start_command("default_roles")

    await _set_up_default_roles(scenario)

    title = "🎉 Funcionalidade ativada com sucesso!"
    assert [
        event for event in scenario.outputs
        if (event.get("embed") or {}).get("title") == title
    ]
    assert isinstance(scenario.session.mode, Manage), "the panel below it"
    assert not any(
        "Configuração do Servidor" in text for text in _texts(scenario.current_message)
    )


async def test_voltar_on_a_setups_first_screen_returns_to_the_page_it_came_from(
    scenario_factory, six_moderations
):
    """Lucas (PR15 round 5, k03): a setup opened from /configurar offers Voltar,
    not Cancelar, on its first screen, and Voltar draws the category page the
    admin came from again, on its page, in the same message."""
    scenario = await _configurar(_admin_scenario(scenario_factory))
    await _press(scenario, _beside(scenario.current_message, _category("moderations")))
    await _press(scenario, _button(scenario.current_message, "Próxima"))

    await _press(scenario, _sections(scenario.current_message)[0].accessory)

    labels = [
        item.label for item in locators.clickable_items(scenario.current_message)
        if isinstance(item, discord.ui.Button)
    ]
    assert labels[-1] == "Voltar" and "Cancelar" not in labels

    await scenario.click("back")

    message = scenario.current_message
    texts = _texts(message)
    assert texts[0] == "## 🛡️ Moderação", "the category page"
    assert any("Página 2 de 2" in text for text in texts), "on the page it came from"
    live = [item for item in scenario.store.messages.values() if not item.deleted]
    assert live == [message], "the admin keeps one message"
    await scenario.finish()
