"""Behavioral scenarios: /moderations block links (card-first redesign).

Setup flow: intro -> configuration card (mode + answer + "Adicionar exceções?",
with visible defaults) -> resume. Configurar opens the exceptions on a screen of
their own (round 5 E): the exempt channels and roles, the popular websites and
the custom links in block_all, the channels and roles in allow_all, where the
blocked links stay on the card. The custom links are added from the list on
the screen that draws it (bl-7, beta.2): its Add opens the link sub-form and
comes back to that screen. Two modes: block_all (allowlist) and allow_all
(blocklist). Everything runs the real YAML + engine + matcher offline.
"""
import json
import pytest

from app.core.utils import ml

pytestmark = pytest.mark.behavioral

GUILD_ID = "123456789"

EXCEPTIONS = "customize:2"
CHANNEL_EXCEPTIONS = "customize:3"
CUSTOM_LINKS = "customize:6"


async def test_happy_path_with_defaults_needs_only_done(scenario_factory):
    """The card ships with mode=block_all and the default answer prefilled:
    accepting the defaults is a single Done click."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    scenario.expect_message(components_v2=True)
    assert scenario.card_state["mode"] == "block_all"
    assert scenario.card_state["answer"] == "Nada de links por aqui! :p"

    await scenario.click("done")
    scenario.expect_step("confirm")               # no gate, no list walked

    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID},
        {"mode": "block_all", "answer": "Nada de links por aqui! :p", "enabled": True},
    )
    assert document["custom_links"]["values"] == []
    await scenario.finish()


async def test_block_all_full_flow_persists_domains_and_custom_entry(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    await scenario.click("customize:1")           # answer modal-input
    await scenario.submit_modal({"Digite minha resposta": "Sem links, {user}!"})
    await scenario.click(EXCEPTIONS)              # Configurar
    await scenario.select_option(["youtube.com", "twitch.tv"], target="allowed_links")
    await scenario.select_option("general", target="allowed_chats")

    await scenario.click(CUSTOM_LINKS)            # the list's Add
    await scenario.submit_modal({"Digite o link ou site": "https://Meusite.com.br/promo/"})

    # The match-type step explains the choice using the link the user JUST
    # typed ({response:link} token), normalized (no scheme/www/trailing /).
    match_step = scenario.expect_message()
    match_text = (match_step.get("embed") or {}).get("description") or ""
    assert "meusite.com.br/promo" in match_text
    assert "https://" not in match_text.split("!")[0]

    await scenario.click("option:🌐 Todos os links desse site")

    scenario.expect_step("link_settings")         # back on the exceptions
    await scenario.click("back")
    await scenario.click("done")
    scenario.expect_step("confirm")

    # Custom link values render monospaced in the resume (style: code),
    # matching the popular-websites code block.
    resume = scenario.expect_message(components_v2=True)
    assert "`meusite.com.br/promo`" in json.dumps(resume, ensure_ascii=False)

    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID},
        {"mode": "block_all", "answer": "Sem links, {user}!"},
    )
    assert sorted(document["allowed_links"]["values"]) == ["twitch.tv", "youtube.com"]
    general = str(scenario.guild.text_channels[0].id)
    assert document["allowed_chats"] == {"style": "channel", "values": general}
    assert document["allowed_roles"] == {"style": "role", "values": []}
    entry = document["custom_links"]["values"][0]
    assert entry["link"]["value"] == "meusite.com.br/promo"   # normalized
    assert entry["match_type"]["_raw_value"] == "domain"
    assert entry["match_type"]["value"] == "🌐 Todos os links desse site"
    await scenario.finish()


async def test_bare_domain_skips_the_match_type_question(scenario_factory):
    """Typing just a domain (youtube.com) IS "the whole website": the
    match-type step only appears when the link goes beyond the domain."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click(EXCEPTIONS)
    await scenario.click(CUSTOM_LINKS)

    await scenario.submit_modal({"Digite o link ou site": "https://www.meusite.com.br/"})

    scenario.expect_step("link_settings")         # no match-type question
    await scenario.click("back")
    await scenario.click("done")
    await scenario.confirm()                      # resume -> finish

    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"mode": "block_all"},
    )
    entry = document["custom_links"]["values"][0]
    assert entry["link"]["value"] == "meusite.com.br"          # normalized
    assert "match_type" not in entry                            # defaults to domain
    await scenario.finish()


def _rendered_texts(components):
    found = []
    stack = list(components)
    while stack:
        component = stack.pop()
        if component.get("type") == "text":
            found.append(component.get("content") or "")
        stack.extend(component.get("children", []))
    return "\n".join(found)


async def test_mode_picker_explains_each_mode_in_its_own_line(scenario_factory):
    """Choosing the blocking mode is the most consequential decision of the
    command: the choice must spell out what each mode does instead of showing
    two bare labels. Since beta.2 round 2 (N4) the mode is picked on the card's
    own select, each mode an option with its explanation under its name, and
    the mode in force chosen in it."""
    import discord

    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    mode = next(
        item
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.Select) and item.custom_id.endswith(":mode")
    )
    assert [option.label for option in mode.options] == [
        "🔗 Bloquear alguns",
        "🚫 Bloquear todos",
    ]
    for option in mode.options:
        assert option.description, option.label
        assert option.label.split(" ", 1)[1] not in option.description
    assert [option.default for option in mode.options] == [False, True]
    await scenario.finish()


async def test_allow_all_hides_popular_websites_and_requires_entries(scenario_factory):
    """visible-when: switching the mode removes the popular-websites section
    from the card, and the blocked links are added from the card's own list."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    await scenario.select_option("allow_all", target="mode")   # 🔗 Bloquear alguns

    card_event = scenario.expect_message(components_v2=True)

    def _texts(components):
        found = []
        stack = list(components)
        while stack:
            component = stack.pop()
            if component.get("type") == "text":
                found.append(component.get("content") or "")
            stack.extend(component.get("children", []))
        return " ".join(found)

    rendered = _texts(card_event["components"])
    assert "Sites populares" not in rendered, "hidden section must not render"

    assert "Links personalizados bloqueados" in rendered, "the list names the mode"
    await scenario.click(CUSTOM_LINKS)
    scenario.expect_step("link")

    await scenario.submit_modal({"Digite o link ou site": "bit.ly/golpe"})
    await scenario.click("option:🔗 Só esse link específico")

    scenario.expect_step("link_settings")
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.confirm()                      # resume -> finish

    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"mode": "allow_all"},
    )
    entry = document["custom_links"]["values"][0]
    assert entry["link"]["value"] == "bit.ly/golpe"
    assert entry["match_type"]["_raw_value"] == "exact"
    await scenario.finish()


async def test_invalid_link_shows_error_and_recovers(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click(EXCEPTIONS)
    await scenario.click(CUSTOM_LINKS)

    await scenario.submit_modal({"Digite o link ou site": "isso nao e um link"})
    expected = ml("errors.link-not-recognized.message", locale="pt-br")
    scenario.expect_error(expected.split(".")[0])

    # The form stays on the same step; the refusal's Edit re-opens the modal.
    scenario.expect_step("link")
    await scenario.click("customize:0")
    await scenario.submit_modal({"Digite o link ou site": "spam.com"})
    scenario.expect_step("link_settings")         # bare domain: no question
    await scenario.finish()


async def test_a_link_added_from_the_card_keeps_the_card_state(scenario_factory):
    """Formerly the Back from the add_custom gate: the card's draft survives a
    trip to the link sub-form, and the link lands on the card's list."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click(EXCEPTIONS)
    await scenario.select_option(["youtube.com"], target="allowed_links")
    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({"Digite o link ou site": "spam.com"})

    scenario.expect_message(components_v2=True)
    assert scenario.card_state["allowed_links"] == ["youtube.com"], \
        "card state must survive the list's sub-form"
    assert "- `spam.com`" in _rendered_texts(
        scenario.expect_message(components_v2=True)["components"]
    )
    await scenario.finish()


async def test_custom_links_copy_says_what_the_list_does_in_each_mode(scenario_factory):
    """description-when: the very same composition step must tell the user
    whether the links being typed are the ones I always ALLOW (block-all mode)
    or the ones I always BLOCK (allow-all mode).

    Reported from a live test: the mode-neutral copy ("depending on the mode
    you chose, these are the links I'll always allow or always block") left
    the user unsure whether an entry was an exception or a rule."""
    blocking = await scenario_factory(locale="pt-br").start("block_links")
    await blocking.confirm()
    await blocking.click(EXCEPTIONS)              # keeps the default block_all
    await blocking.click(CUSTOM_LINKS)
    await blocking.submit_modal({"Digite o link ou site": "twitch.tv/jway"})
    text = (blocking.expect_message().get("embed") or {}).get("description") or ""
    assert text.count("sempre libero") == 2, (
        "each option repeats what the rule does, not only the opening line"
    )
    # Each option echoes the user's own data at its own scope: the website on
    # the domain option, the exact address on the specific-link option.
    assert "`twitch.tv`" in text and "`twitch.tv/jway`" in text
    await blocking.finish()

    allowing = await scenario_factory(locale="pt-br").start("block_links")
    await allowing.confirm()
    await allowing.select_option("allow_all", target="mode")   # a draft
    await allowing.click(CUSTOM_LINKS)            # on the card, reads the draft
    await allowing.submit_modal({"Digite o link ou site": "twitch.tv/jway"})
    text = (allowing.expect_message().get("embed") or {}).get("description") or ""
    assert text.count("sempre bloqueio") == 2
    assert "libero" not in text
    await allowing.finish()


def _card_texts(scenario):
    return _rendered_texts(scenario.expect_message(components_v2=True)["components"])


def _button_labels(scenario):
    import discord

    return [
        item.label
        for item in scenario.current_message.view.walk_children()
        if isinstance(item, discord.ui.Button)
    ]


async def test_block_all_exceptions_open_on_their_own_screen_and_show_on_the_card(
    scenario_factory,
):
    """Round 5 E (v05, v06, v07): the card asks "Adicionar exceções?" with what is
    chosen as its lines and a Configurar; Configurar opens a screen with only
    the exceptions and one Voltar; a channel and a popular website picked there
    are told on the card after Voltar, on the summary and in the document."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    general = str(scenario.guild.text_channels[0].id)

    card = _card_texts(scenario)
    assert "🛡️ **Adicionar exceções?**" in card
    assert "Canais, cargos, sites e links que eu nunca apago." in card
    assert "> #️⃣ **Canais permitidos:** Em branco" in card
    assert "> 🔗 **Sites populares permitidos:** Em branco" in card
    assert "> 📌 **Links personalizados permitidos:** Em branco" in card
    assert "Canais e cargos permitidos" not in card, "the sections are on Configurar"
    assert "Configurar" in _button_labels(scenario)

    await scenario.click(EXCEPTIONS)

    exceptions = _card_texts(scenario)
    assert "## 🛡️ Exceções" in exceptions
    assert "🔓 **Canais e cargos permitidos**" in exceptions
    assert "🔗 **Sites populares permitidos**" in exceptions
    assert "📌 **Links personalizados permitidos**" in exceptions
    assert "Modo de bloqueio" not in exceptions
    assert [label for label in _button_labels(scenario) if label in (
        "Voltar", "Avançar", "Cancelar"
    )] == ["Voltar"]

    await scenario.select_option("general", target="allowed_chats")
    await scenario.select_option(["youtube.com"], target="allowed_links")
    assert "## 🛡️ Exceções" in _card_texts(scenario), "a pick keeps the screen"
    await scenario.click("back")

    card = _card_texts(scenario)
    assert f"> #️⃣ **Canais permitidos:** <#{general}>" in card
    assert "> 🔗 **Sites populares permitidos:** Youtube" in card
    await scenario.click("done")
    scenario.expect_step("confirm")
    summary = _card_texts(scenario)
    assert f"<#{general}>" in summary
    assert "youtube.com" in summary.lower()
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"mode": "block_all"},
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
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.select_option("allow_all", target="mode")
    general = str(scenario.guild.text_channels[0].id)

    card = _card_texts(scenario)
    assert "Canais e cargos onde eu nunca apago links." in card
    assert "Sites populares" not in card
    assert "📌 **Links personalizados bloqueados**" in card

    await scenario.click(CHANNEL_EXCEPTIONS)
    exceptions = _card_texts(scenario)
    assert "🔓 **Canais e cargos permitidos**" in exceptions
    assert "Links personalizados" not in exceptions
    await scenario.select_option("general", target="allowed_chats")
    await scenario.click("back")

    assert f"> #️⃣ **Canais permitidos:** <#{general}>" in _card_texts(scenario)
    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({"Digite o link ou site": "spam.com"})
    scenario.expect_step("link_settings")
    assert "- `spam.com`" in _card_texts(scenario), "the blocked list is on the card"
    await scenario.click("done")
    summary = _card_texts(scenario)
    assert f"<#{general}>" in summary and "spam.com" in summary
    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"mode": "allow_all"},
    )
    assert document["allowed_chats"] == {"style": "channel", "values": general}
    assert document["custom_links"]["values"][0]["link"]["value"] == "spam.com"
    await scenario.finish()


async def test_back_and_cancel_around_the_exceptions_keep_or_drop_the_draft(
    scenario_factory, deps
):
    """Round 5 E: Voltar from the exceptions keeps every pick in the card's draft,
    so does keeping the card after Cancel, the summary's Editar opens the card
    with the exceptions closed and the same Configurar, and discarding saves
    nothing."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click(EXCEPTIONS)
    await scenario.select_option(["twitch.tv"], target="allowed_links")
    await scenario.click("back")
    assert scenario.card_state["allowed_links"] == ["twitch.tv"]

    await scenario.click("cancel")
    await scenario.click("Continuar configurando")
    assert scenario.card_state["allowed_links"] == ["twitch.tv"]
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.click("edit:link_settings")
    card = _card_texts(scenario)
    assert "🛡️ **Adicionar exceções?**" in card
    assert "> 🔗 **Sites populares permitidos:** Twitch" in card
    await scenario.click(EXCEPTIONS)
    assert "## 🛡️ Exceções" in _card_texts(scenario)
    await scenario.click("back")
    await scenario.click("done")
    scenario.expect_step("confirm")
    await scenario.click("cancel")
    await scenario.click("Descartar tudo")

    assert deps.mongo_client.guild["block_links"].find_one(
        {"guild_id": GUILD_ID}
    ) is None
    await scenario.finish()


async def test_the_panel_edit_opens_the_same_card_with_the_same_configure(
    scenario_factory, deps
):
    """Round 5 E: the panel's Editar of "Configurações Gerais" opens the same
    card, and its Configurar the same exceptions screen; Confirmar saves what
    was picked there and keeps the saved list."""
    saved = {
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
    }
    deps.mongo_client.guild["block_links"].insert_one(dict(saved))
    scenario = await scenario_factory(locale="pt-br").start_manager(
        "block_links", saved
    )

    await scenario.click("section:link_settings")
    card = _card_texts(scenario)
    assert "🛡️ **Adicionar exceções?**" in card
    assert "> 🔗 **Sites populares permitidos:** Youtube" in card
    await scenario.click(EXCEPTIONS)
    exceptions = _card_texts(scenario)
    assert "## 🛡️ Exceções" in exceptions
    assert "🔗 **Sites populares permitidos**" in exceptions
    await scenario.select_option(["youtube.com", "spotify.com"], target="allowed_links")
    await scenario.click("back")
    assert "> 🔗 **Sites populares permitidos:** Youtube, Spotify" in _card_texts(
        scenario
    )
    await scenario.click("done")

    scenario.expect_component(label_or_action="pause")
    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID}, {"mode": "block_all"},
    )
    assert sorted(document["allowed_links"]["values"]) == ["spotify.com", "youtube.com"]
    assert document["custom_links"]["values"][0]["link"]["value"] == "meusite.com.br"
    await scenario.finish()
