"""Block links: its saved document, its card's sections by key, and opening the card."""

from tests.behavioral.flows.common import GUILD_ID, seed_document

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

EXCEPTIONS = "customize:exceptions"
CHANNEL_EXCEPTIONS = "customize:channel_exceptions"
CUSTOM_LINKS = "customize:own_links"


async def open_card(scenario_factory, locale):
    """The setup opened and its intro confirmed: the card on screen."""
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    return scenario


def seed(deps):
    """The enabled document saved."""
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The paused document saved."""
    seed_document(deps, FORM, PAUSED)
