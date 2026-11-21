"""Block links: configuration card (mode, answer, "Adicionar exceções?"), the
exceptions screen its Configurar opens (exempt channels and roles, popular
websites and the custom links in block-all; channels and roles in allow-all,
where the blocked links stay on the card), review."""
from tests.behavioral.flows.block_links import (
    ANSWER_FIELD,
    CHANNEL_EXCEPTIONS,
    CUSTOM_LINKS,
    DOMAIN_OPTION,
    EXCEPTIONS,
    FORM,
    LINK_FIELD,
    open_card,
    seed,
    seed_paused,
)
from tests.behavioral.flows.common import cancel_discard, cancel_keep, open_manager
from tests.behavioral.golden.paths import golden_book, golden_path
from tests.behavioral.golden.paths.common import register_lifecycle

golden_book(FORM, rendered=("form.panel", "form.review"))


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await scenario.click(EXCEPTIONS)
    await scenario.select_option("general", target="allowed_chats")
    await scenario.go_back()
    await scenario.click("done")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_custom_links")
async def setup_custom_links(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.select_option("allow_all", target="mode")
    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({LINK_FIELD[locale]: "bit.ly/golpe"})
    await scenario.click(DOMAIN_OPTION[locale])
    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({LINK_FIELD[locale]: "spam.com"})
    await scenario.click("done")
    await scenario.confirm()
    return scenario


@golden_path(FORM, "setup_exceptions_screen", locales=("pt-br", "en-us"))
async def setup_exceptions_screen(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.click(EXCEPTIONS)
    await scenario.select_option("general", target="allowed_chats")
    await scenario.select_option(["youtube.com"], target="allowed_links")
    await scenario.go_back()
    await scenario.select_option("allow_all", target="mode")
    await scenario.click(CHANNEL_EXCEPTIONS)
    await scenario.go_back()
    await scenario.click("done")
    return scenario


@golden_path(FORM, "setup_choose_mode")
async def setup_choose_mode(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await scenario.select_option("allow_all", target="mode")
    return scenario


@golden_path(FORM, "setup_validation_error_and_recover")
async def setup_validation_error_and_recover(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.click(EXCEPTIONS)
    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({LINK_FIELD[locale]: "isso nao e um link"})
    await scenario.click("customize:link")
    await scenario.submit_modal({LINK_FIELD[locale]: "https://meusite.com.br/promo/"})
    await scenario.click(DOMAIN_OPTION[locale])
    return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await scenario.click(EXCEPTIONS)
    await scenario.select_option(["youtube.com"], target="allowed_links")
    await scenario.click(CUSTOM_LINKS)
    await scenario.submit_modal({LINK_FIELD[locale]: "youtube.com/watch?v=abc123"})
    await scenario.go_back()
    await scenario.submit_modal({LINK_FIELD[locale]: "spam.com"})
    await scenario.go_back()
    await scenario.click("done")
    return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.select_option("allow_all", target="mode")
    await cancel_keep(scenario, locale)
    return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    scenario = await open_card(scenario_factory, locale)
    await scenario.select_option("allow_all", target="mode")
    await cancel_discard(scenario, locale)
    return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("section:link_settings")
    await scenario.click("customize:answer")
    await scenario.submit_modal({ANSWER_FIELD[locale]: "Sem links por aqui!"})
    await scenario.click("done")
    return scenario


@golden_path(FORM, "manager_add_item")
async def manager_add_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("add")
    await scenario.submit_modal({LINK_FIELD[locale]: "spam.example.com"})
    return scenario


@golden_path(FORM, "manager_remove_item")
async def manager_remove_item(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("remove")
    await scenario.select_option("custom_links$0")
    await scenario.click("remove_items")
    return scenario


@golden_path(FORM, "manager_remove_several")
async def manager_remove_several(scenario_factory, deps, locale):
    scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
    await scenario.click("remove:custom_links")
    await scenario.select_option(["custom_links$0", "custom_links$1"])
    await scenario.click("remove_items")
    return scenario


register_lifecycle(FORM, seed_enabled=seed, seed_paused=seed_paused)
