"""Behavioral scenarios: /moderations block links (card-first redesign).

Setup flow: intro -> configuration card (mode + popular websites + answer,
with visible defaults) -> optional custom-links composition -> exempt
channels/roles -> resume. Two modes: block_all (allowlist) and allow_all
(blocklist). Everything runs the real YAML + engine + matcher offline.
"""
import pytest

from app.services.utils import ml

pytestmark = pytest.mark.behavioral

GUILD_ID = "123456789"

LATER = {"pt-br": "Depois", "en-us": "Later"}
YES = {"pt-br": "Sim", "en-us": "Yes"}


async def test_happy_path_with_defaults_needs_only_done(scenario_factory):
    """The card ships with mode=block_all and the default answer prefilled:
    accepting the defaults is a single Done click."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    scenario.expect_message(components_v2=True)
    assert scenario.card_state["mode"] == "block_all"
    assert scenario.card_state["answer"] == "Nada de links por aqui! :p"

    await scenario.click("done")
    scenario.expect_step("add_custom")
    await scenario.click(LATER["pt-br"])          # skip custom links

    scenario.expect_step("permissions")
    await scenario.confirm()
    scenario.expect_step("confirm")

    # Transient gate steps (hidden: true) must not leak into the resume —
    # clicking "Depois" used to show up as a bogus "Sim" line.
    resume = scenario.expect_message()
    resume_text = (resume.get("embed") or {}).get("description") or ""
    assert "Adicionar Seus Próprios Links" not in resume_text

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

    await scenario.click("customize:1")           # popular websites multi-select
    await scenario.select_option(["youtube.com", "twitch.tv"])
    await scenario.click("customize:2")           # answer modal-input
    await scenario.submit_modal({"Digite minha resposta": "Sem links, {user}!"})
    await scenario.click("done")

    await scenario.click(YES["pt-br"])            # add custom links
    await scenario.submit_modal({"Digite o link ou site": "https://Meusite.com.br/promo/"})

    # The match-type step explains the choice using the link the user JUST
    # typed ({response:link} token), normalized (no scheme/www/trailing /).
    match_step = scenario.expect_message()
    match_text = (match_step.get("embed") or {}).get("description") or ""
    assert "meusite.com.br/promo" in match_text
    assert "https://" not in match_text.split("!")[0]

    await scenario.click("option:🌐 Todos os links desse site")

    scenario.expect_step("permissions")
    await scenario.select_option("general", target="allowed_chats")
    await scenario.confirm()
    scenario.expect_step("confirm")

    # Custom link values render monospaced in the resume (style: code),
    # matching the popular-websites code block.
    resume = scenario.expect_message()
    assert "`meusite.com.br/promo`" in ((resume.get("embed") or {}).get("description") or "")

    await scenario.confirm()

    document = scenario.expect_persisted(
        "guild", "block_links", {"guild_id": GUILD_ID},
        {"mode": "block_all", "answer": "Sem links, {user}!"},
    )
    assert sorted(document["allowed_links"]["values"]) == ["twitch.tv", "youtube.com"]
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
    await scenario.click("done")
    await scenario.click(YES["pt-br"])

    await scenario.submit_modal({"Digite o link ou site": "https://www.meusite.com.br/"})

    scenario.expect_step("permissions")           # no match-type question
    await scenario.confirm()
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
    command: the picker must spell out what each mode does, one line per
    button led by that button's emoji, instead of showing two bare labels."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:0")

    picker = scenario.expect_message(components_v2=True)
    rendered = _rendered_texts(picker["components"])

    lines = rendered.split("\n")
    for emoji, label in (("🚫", "Bloquear todos"),
                         ("✅", "Permitir todos")):
        index = next(
            (i for i, line in enumerate(lines)
             if label in line and line.lstrip().startswith(emoji)),
            None,
        )
        assert index is not None, \
            f"mode picker must title {label!r} on its own {emoji} line"
        assert not lines[index].lstrip().startswith("- "), \
            f"a bullet in front of {emoji} is visual noise"
        explanation = lines[index + 1]
        assert explanation and label not in explanation, (
            f"{label!r} must be followed by an explanation line, like a field "
            f"name and its value, got {explanation!r}"
        )
    await scenario.finish()


async def test_allow_all_hides_popular_websites_and_requires_entries(scenario_factory):
    """visible-when: switching the mode removes the popular-websites section
    from the card, the add_custom gate is skipped, and the composition runs
    unconditionally (blocklist entries are the whole point of the mode)."""
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()

    await scenario.click("customize:0")           # mode picker
    await scenario.click("✅ Permitir todos")

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

    await scenario.click("done")
    scenario.expect_step("link")                  # gate skipped -> composition

    await scenario.submit_modal({"Digite o link ou site": "bit.ly/golpe"})
    await scenario.click("option:🔗 Só esse link específico")

    scenario.expect_step("permissions")
    await scenario.confirm()
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
    await scenario.click("done")
    await scenario.click(YES["pt-br"])

    await scenario.submit_modal({"Digite o link ou site": "isso nao e um link"})
    expected = ml("errors.link-not-recognized.message", locale="pt-br")
    scenario.expect_error(expected.split(".")[0])

    # The form stays on the same step; clicking the gate again re-opens the modal.
    scenario.expect_step("link")
    await scenario.click(YES["pt-br"])
    await scenario.submit_modal({"Digite o link ou site": "spam.com"})
    scenario.expect_step("permissions")           # bare domain: no question
    await scenario.finish()


async def test_back_from_gate_returns_to_card_with_state(scenario_factory):
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:1")
    await scenario.select_option(["youtube.com"])
    await scenario.click("done")
    scenario.expect_step("add_custom")

    await scenario.go_back()
    scenario.expect_message(components_v2=True)
    assert scenario.card_state["allowed_links"] == ["youtube.com"], \
        "card state must survive back-navigation"
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
    await blocking.click("done")                  # keeps the default block_all
    await blocking.click(YES["pt-br"])
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
    await allowing.click("customize:0")
    await allowing.click("✅ Permitir todos")
    await allowing.click("done")                  # gate is skipped in allow_all
    await allowing.submit_modal({"Digite o link ou site": "twitch.tv/jway"})
    text = (allowing.expect_message().get("embed") or {}).get("description") or ""
    assert text.count("sempre bloqueio") == 2
    assert "libero" not in text
    await allowing.finish()
