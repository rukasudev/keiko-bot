"""Welcome messages: the saved document, the banner renderer faked, and the card's
steps (the channel, the title and footer)."""

from unittest.mock import AsyncMock, patch

from tests.behavioral.flows.common import (  # noqa: F401
    GUILD_ID,
    UPLOADED_URL,
    dump_channel,
    seed_document,
)

FORM = "welcome_messages"
PREVIEW_URL = "https://cdn.example.com/previews/welcome-preview.png"

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


def banner_stub():
    """The banner renderer answering a fixed preview URL, as a context manager."""
    return patch(
        "app.commands.welcome.service.create_banner",
        new=AsyncMock(return_value=PREVIEW_URL),
    )


async def open_card(scenario_factory, locale):
    """The setup opened, its intro confirmed and the welcome channel picked."""
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    await scenario.select_option("welcome", target="welcome_messages_channel")
    return scenario


async def submit_messages(scenario, locale):
    """The messages section opened and its title and footer typed."""
    await scenario.click("customize:messages")
    await scenario.submit_modal(
        {
            TITLE_FIELD[locale]: "Bem-vindo, {user}!",
            FOOTER_FIELD[locale]: "Leia as regras :)",
        }
    )


def seed(deps):
    """The enabled document saved."""
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The paused document saved."""
    seed_document(deps, FORM, PAUSED)
