"""Welcome messages: one card (channel, banner design, custom image, messages)
and a review with Preview.

The banner renderer is the one boundary faked here: previews come from an
async stub returning a fixed URL, so the banner under the card's design and the
gallery opened from the panel's Edit render as in production. Every card line
has its own Edit on the panel.
"""

from tests.behavioral.flows.common import cancel_discard, cancel_keep, open_manager
from tests.behavioral.flows.welcome import (
    FORM,
    banner_stub,
    dump_channel,
    open_card,
    seed,
    seed_paused,
    submit_messages,
)
from tests.behavioral.golden.paths import golden_book, golden_path
from tests.behavioral.golden.paths.common import register_lifecycle

golden_book(FORM, rendered=("form.panel", "form.review"))


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    with banner_stub():
        scenario = await open_card(scenario_factory, locale)
        await submit_messages(scenario, locale)
        await scenario.click("done")
        await scenario.confirm()
        return scenario


@golden_path(FORM, "setup_custom_image")
async def setup_custom_image(scenario_factory, deps, locale):
    dump_channel(deps)
    with banner_stub():
        scenario = await open_card(scenario_factory, locale)
        await scenario.select_option("custom_only", target="welcome_design")
        await scenario.click("customize:image")
        await scenario.submit_file_upload(
            filename="custom-banner.png", content=b"\x89PNG-fake"
        )
        await submit_messages(scenario, locale)
        await scenario.click("done")
        await scenario.confirm()
        return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    with banner_stub():
        scenario = await open_card(scenario_factory, locale)
        await scenario.select_option("custom_blur", target="welcome_design")
        await scenario.select_option("server_blur", target="welcome_design")
        await scenario.select_option(
            "announcements", target="welcome_messages_channel"
        )
        await scenario.click("done")
        return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    with banner_stub():
        scenario = await open_card(scenario_factory, locale)
        await cancel_keep(scenario, locale)
        return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    with banner_stub():
        scenario = await open_card(scenario_factory, locale)
        await cancel_discard(scenario, locale)
        return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    with banner_stub():
        scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
        await scenario.click("section:welcome_config/channel")
        await scenario.select_option("announcements")
        return scenario


@golden_path(FORM, "manager_edit_design")
async def manager_edit_design(scenario_factory, deps, locale):
    with banner_stub():
        scenario = await open_manager(scenario_factory, deps, locale, FORM, seed)
        await scenario.click("section:welcome_config/design")
        await scenario.click("design:custom_blur")
        return scenario


register_lifecycle(FORM, seed_enabled=seed, seed_paused=seed_paused)
