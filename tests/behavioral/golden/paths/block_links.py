"""Block links: configuration card (mode, popular websites, answer), the
custom-links gate and composition, exempt channels and roles, review."""
from tests.behavioral.golden.paths import golden_path
from tests.behavioral.golden.paths.common import (
    GUILD_ID,
    cancel_discard,
    cancel_keep,
    label,
    open_manager,
    register_lifecycle,
    seed_document,
)

FORM = "block_links"

ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "mode": "block_all",
    "allowed_chats": {"style": "channel", "values": "100"},
    "allowed_roles": {"style": "role", "values": "201"},
    "allowed_links": {"style": "bullet", "values": ["youtube.com", "twitch.tv"]},
    "custom_links": {"style": "composition", "values": [
        {"link": {"value": "meusite.com.br", "title": "Link ou Site", "style": "code"},
         "match_type": {"value": "🌐 Todos os links desse site", "_raw_value": "domain",
                        "title": "Como Devo Considerar?"}},
        {"link": {"value": "docs.example.org", "title": "Link ou Site", "style": "code"}},
    ]},
    "answer": "Nada de links aqui! :p",
}
PAUSED = {**ENABLED, "enabled": False}

LINK_FIELD = {"pt-br": "Digite o link ou site", "en-us": "Type the link or website"}
ANSWER_FIELD = {"pt-br": "Digite minha resposta", "en-us": "Type my answer"}
DOMAIN_OPTION = {"pt-br": "option:🌐 Todos os links desse site",
                 "en-us": "option:🌐 Every link from this website"}


async def _to_gate(scenario_factory, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await scenario.click("done")
    return scenario


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await _to_gate(scenario_factory, locale)
    await scenario.click(label("later", locale))
    await scenario.select_option("general", target="allowed_chats")
    await scenario.confirm()
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await _to_gate(scenario_factory, locale)
    await scenario.click(label("yes", locale))
    await scenario.submit_modal({LINK_FIELD[locale]: "isso nao e um link"})
    await scenario.click(label("yes", locale))
    await scenario.submit_modal({LINK_FIELD[locale]: "https://meusite.com.br/promo/"})
    await scenario.click(DOMAIN_OPTION[locale])
    return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await scenario.click("customize:1")
    await scenario.select_option(["youtube.com"])
    await scenario.click("done")
    await scenario.go_back()
    await scenario.click("done")
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await _to_gate(scenario_factory, locale)
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await _to_gate(scenario_factory, locale)
    await cancel_discard(scenario, locale)
    return scenario


def _seed(deps):
    seed_document(deps, FORM, ENABLED)


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("section:link_settings")
    await scenario.click("customize:2")
    await scenario.submit_modal({ANSWER_FIELD[locale]: "Sem links por aqui!"})
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD[locale]: "spam.example.com"})
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, _seed)
    await scenario.click("remove")
    await scenario.select_option("custom_links$0")
    return scenario


register_lifecycle(
    FORM,
    seed_enabled=_seed,
    seed_paused=lambda deps: seed_document(deps, FORM, PAUSED),
)
