"""Behavioral scenarios: a list drawn inside the card (P4, tw-8, bd-6 of beta.2).

Add sits beside the list's title, Remove under it and picks several items,
a confirmation names them before anything is saved, each item that previews
has its own Pré-visualizar, and the birthday panel searches one member and
keeps its settings behind Configurar.
"""
import json

import pytest

from tests.behavioral.golden.paths import notifications_twitch as twitch
from tests.behavioral.golden.paths.common import seed_document
from tests.behavioral.golden.paths.reminders_birthday import _open_manager as birthdays

pytestmark = pytest.mark.behavioral


def _sent(scenario):
    return [event for event in scenario.outputs if event.get("kind") == "followup_send"]


def _shown(event):
    return json.dumps(event, ensure_ascii=False)


async def test_each_streamer_on_the_panel_previews_its_own_announcement(
    scenario_factory, deps
):
    twitch._known_streamers(deps)
    seed_document(deps, twitch.FORM, twitch.ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command(twitch.FORM)

    await scenario.click("aside:preview:notifications$1")

    preview = _shown(_sent(scenario)[-1])
    assert "cellbit" in preview and "gaules" not in preview
    assert "Descrição da live aqui" in preview
    scenario.expect_component(label_or_action="aside:preview:notifications$0")
    await scenario.finish()


async def test_each_streamer_of_the_summary_previews_its_own_announcement(
    scenario_factory, deps
):
    twitch._known_streamers(deps)
    scenario = await scenario_factory(locale="pt-br").start_command(twitch.FORM)
    await scenario.confirm()
    for streamer in ("gaules", "cellbit"):
        await twitch._channel(scenario)
        await twitch._submit_streamer(scenario, streamer)
        await scenario.click("done")
        if streamer == "gaules":
            await scenario.click("add:notifications")

    await scenario.click("aside:preview:notifications$1")

    assert "cellbit" in _shown(_sent(scenario)[-1])
    labels = [
        getattr(item, "label", None)
        for item in scenario.current_message.view.walk_children()
    ]
    assert labels.count("Pré-visualizar") == 2, "one beside each Editar, none below"
    await scenario.finish()


async def test_back_on_the_confirmation_keeps_every_item(scenario_factory, deps):
    from tests.behavioral.golden.paths import block_links

    seed_document(deps, block_links.FORM, block_links.ENABLED)
    scenario = await scenario_factory(locale="pt-br").start_command(block_links.FORM)

    await scenario.click("remove:custom_links")
    await scenario.select_option(["custom_links$0", "custom_links$1"])
    await scenario.go_back()

    saved = scenario.get_persisted("guild", block_links.FORM, {})
    assert len(saved["custom_links"]["values"]) == 2
    scenario.expect_component(label_or_action="remove:custom_links")
    await scenario.finish()


async def test_the_summary_removes_several_items_at_once(scenario_factory):
    """The birthday list this ran on left the setup (bd-3); the block links
    summary keeps the contract: one confirmation names every item chosen.
    Since bl-7 the links are added on the card, and the review removes them."""
    link = "Digite o link ou site"
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")
    await scenario.click("customize:6")
    await scenario.submit_modal({link: "meusite.com.br"})
    await scenario.click("back")
    await scenario.click("done")
    await scenario.click("add:custom_links")
    await scenario.submit_modal({link: "docs.example.org"})

    await scenario.click("remove:custom_links")
    await scenario.select_option(["custom_links$0", "custom_links$1"])
    confirmation = _shown(scenario.outputs[-1])
    assert "meusite.com.br" in confirmation and "docs.example.org" in confirmation
    await scenario.click("remove_items")

    assert scenario.answers["custom_links"] == ()
    await scenario.finish()


async def test_the_card_removes_the_links_it_lists_and_stays_on_screen(
    scenario_factory,
):
    """bl-7: the card's own list removes too, and the screen that draws it stays
    in front (the exceptions screen since round 5 E)."""
    link = "Digite o link ou site"
    scenario = await scenario_factory(locale="pt-br").start("block_links")
    await scenario.confirm()
    await scenario.click("customize:2")
    for typed in ("meusite.com.br", "docs.example.org"):
        await scenario.click("customize:6")
        await scenario.submit_modal({link: typed})

    await scenario.click("remove:custom_links")
    await scenario.select_option(["custom_links$1"])
    await scenario.click("remove_items")

    scenario.expect_step("link_settings")
    assert "## 🛡️ Exceções" in _shown(scenario.expect_message(components_v2=True))
    names = [item["link"] for item in scenario.answers["custom_links"]]
    assert [answer.raw for answer in names] == ["meusite.com.br"]
    await scenario.finish()


async def test_a_birthday_found_by_search_is_removed_from_its_own_screen(
    scenario_factory, deps
):
    scenario = await birthdays(scenario_factory, deps, "pt-br", members=("555", "777"))

    await scenario.click("search")
    await scenario.select_option("Amiga")
    await scenario.click("remove_this:reminders_birthday$1")
    await scenario.click("remove_items")

    from app.services.reminders_birthdays import find_birthdays_by_guild

    assert [item["user_id"] for item in find_birthdays_by_guild("123456789")] == [
        "555"
    ]
    await scenario.finish()


async def test_configurar_edits_the_reminder_settings(scenario_factory, deps):
    scenario = await birthdays(scenario_factory, deps, "pt-br")

    await scenario.click("edit:group:settings")
    await scenario.click("section:birthday_config")

    scenario.expect_message(components_v2=True)
    assert scenario.session.mode.kind == "edit"
    await scenario.finish()


async def test_the_birthday_panel_lists_the_next_birthdays_as_bullets(
    scenario_factory, deps
):
    """Broke as: the counts block read "Próximos aniversários: 1. <@555>: 12 de
    maio" and "2. <@777>: 12 de junho" on a bare next line, a numbered text glued
    to its label, while every other list of the panel is bullets under its title
    (P4). The next birthdays are a list of values, drawn like one."""
    scenario = await birthdays(scenario_factory, deps, "pt-br", members=("555", "777"))

    panel = _shown(scenario.outputs[-1])
    assert (
        "**Próximos aniversários:**\\n- <@555>: 12 de maio\\n- <@777>: 12 de junho"
        in panel
    ), panel
    await scenario.finish()


async def test_a_birthday_search_opens_the_member_with_picture_and_messages(
    scenario_factory, deps
):
    """N2 and B5 (round 3): picking the member on Pesquisar opens them at once,
    with no Confirmar, under "🎉 Configurações de aniversário de @membro" and a
    separator: the birthday, the messages that will celebrate them (Keiko's
    default here) in a code block, then their picture big below. B3 (round 4):
    no picture beside the title, so the title sits right above the separator."""
    scenario = await birthdays(scenario_factory, deps, "pt-br", members=("555", "777"))

    await scenario.click("search")
    await scenario.select_option("Amiga")

    found = next(
        event for event in reversed(scenario.outputs) if event.get("components")
    )
    header, lines, picture, *_rest = found["components"][0]["children"]
    assert header == {
        "type": "text",
        "content": "## 🎉 Configurações de aniversário de <@777>",
    }, header
    assert lines["content"] == (
        "📅 **Dia do aniversário:** 12 de junho\n"
        "💬 **Mensagens de parabéns (padrão do Keiko):**\n"
        "```• 🎂 É aniversário do {user}!\n"
        "• Feliz aniversário, {user}! 🎉 Que seu dia seja cheio de alegria e mimos! ✨```"
    ), lines
    assert picture == {"type": "media", "count": 1}, _shown(found)
    drawn = [
        type(item).__name__
        for item in scenario.current_message.view.children[0].children
    ]
    assert drawn[:4] == ["TextDisplay", "Separator", "TextDisplay", "MediaGallery"]
    gallery = scenario.current_message.view.children[0].children[3]
    assert [item.media.url for item in gallery.items] == [
        "https://cdn.discordapp.com/avatars/777/fake.png"
    ], "the picture is the member's own, read from the server's members"
    scenario.expect_component(label_or_action="remove_this:reminders_birthday$1")
    await scenario.finish()


@pytest.mark.parametrize("members", [("555",), ()], ids=["one", "none"])
async def test_the_birthday_panel_adds_beside_the_list_title(
    scenario_factory, deps, members
):
    """N2: the birthdays block reads "Aniversários" in date order with
    Adicionar beside its title, even with no birthday, and the panel's bottom
    row keeps no Adicionar."""
    scenario = await birthdays(scenario_factory, deps, "pt-br", members=members)

    container, *rows = scenario.outputs[-1]["components"]
    heads = [
        child
        for child in container["children"]
        if child["type"] == "section"
        and child["children"][0]["content"].startswith("### Aniversários\n")
    ]
    assert heads, _shown(scenario.outputs[-1])
    assert heads[0]["children"][-1]["label"] == "Adicionar"
    below = [child["label"] for row in rows for child in row["children"]]
    assert "Adicionar" not in below, below
    await scenario.finish()
