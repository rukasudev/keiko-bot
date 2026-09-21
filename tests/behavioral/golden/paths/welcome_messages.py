"""Welcome messages: channel pick, design gallery (Components V2), optional
custom image upload, an info screen, a five-field modal and a review with
Preview.

The banner renderer is the one boundary faked here: previews come from an
async stub returning a fixed URL, so the gallery renders as in production.
Every step has its own Edit beside its settings on the panel.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tests.behavioral.golden.paths import golden_path
from tests.behavioral.golden.paths.common import (
    GUILD_ID,
    cancel_discard,
    cancel_keep,
    open_manager,
    register_lifecycle,
    seed_document,
)

FORM = "welcome_messages"
PREVIEW_URL = "https://cdn.example.com/previews/welcome-preview.png"
UPLOADED_URL = "https://cdn.discordapp.com/attachments/999/1/custom-banner.png"

ENABLED = {
    "guild_id": GUILD_ID,
    "enabled": True,
    "welcome_messages_channel": {"style": "channel", "values": "101"},
    "welcome_design": "server_blur",
    "welcome_messages_title": "Um novo membro chegou! 🎉",
    "welcome_messages": {
        "style": "bullet",
        "values": ["{user} bem-vindo ao {server}!", "Olha quem chegou! {user}"],
    },
    "welcome_messages_footer": "Divirta-se!",
}
PAUSED = {**ENABLED, "enabled": False}

TITLE_FIELD = {"pt-br": "Título", "en-us": "Title"}
FOOTER_FIELD = {"pt-br": "Rodapé", "en-us": "Footer"}


def _banner_stub():
    return patch(
        "app.services.welcome_messages.create_banner",
        new=AsyncMock(return_value=PREVIEW_URL),
    )


def _dump_channel(deps):
    async def send(*args, **kwargs):
        return SimpleNamespace(attachments=[SimpleNamespace(url=UPLOADED_URL)])

    deps.bot.get_channel = lambda _channel_id: SimpleNamespace(send=send)


async def _to_design_gallery(scenario_factory, locale):
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await scenario.select_option("welcome")
    await scenario.confirm()
    return scenario


async def _messages_to_review(scenario, locale):
    await scenario.confirm()
    await scenario.submit_modal(
        {
            TITLE_FIELD[locale]: "Bem-vindo, {user}!",
            FOOTER_FIELD[locale]: "Leia as regras :)",
        }
    )


@golden_path(FORM, "setup_happy", locales=("pt-br", "en-us"))
async def setup_happy(scenario_factory, deps, locale):
    with _banner_stub():
        scenario = await _to_design_gallery(scenario_factory, locale)
        await scenario.click("design:server_blur")
        await _messages_to_review(scenario, locale)
        await scenario.confirm()
        return scenario


@golden_path(FORM, "setup_custom_image")
async def setup_custom_image(scenario_factory, deps, locale):
    _dump_channel(deps)
    with _banner_stub():
        scenario = await _to_design_gallery(scenario_factory, locale)
        await scenario.click("design:custom_only")
        await scenario.submit_file_upload(
            filename="custom-banner.png", content=b"\x89PNG-fake"
        )
        await _messages_to_review(scenario, locale)
        await scenario.confirm()
        return scenario


@golden_path(FORM, "setup_back")
async def setup_back(scenario_factory, deps, locale):
    with _banner_stub():
        scenario = await _to_design_gallery(scenario_factory, locale)
        await scenario.go_back()
        await scenario.select_option("announcements")
        await scenario.confirm()
        return scenario


@golden_path(FORM, "setup_cancel_keep")
async def setup_cancel_keep(scenario_factory, deps, locale):
    with _banner_stub():
        scenario = await scenario_factory(locale=locale).start_command(FORM)
        await scenario.confirm()
        await cancel_keep(scenario, locale)
        return scenario


@golden_path(FORM, "setup_cancel_discard")
async def setup_cancel_discard(scenario_factory, deps, locale):
    with _banner_stub():
        scenario = await scenario_factory(locale=locale).start_command(FORM)
        await scenario.confirm()
        await cancel_discard(scenario, locale)
        return scenario


@golden_path(FORM, "manager_edit_one_step")
async def manager_edit_one_step(scenario_factory, deps, locale):
    with _banner_stub():
        scenario = await open_manager(
            scenario_factory,
            deps,
            locale,
            FORM,
            lambda d: seed_document(d, FORM, ENABLED),
        )
        await scenario.click("section:welcome_messages_channel")
        await scenario.select_option("announcements")
        await scenario.confirm()
        return scenario


register_lifecycle(
    FORM,
    seed_enabled=lambda deps: seed_document(deps, FORM, ENABLED),
    seed_paused=lambda deps: seed_document(deps, FORM, PAUSED),
)
