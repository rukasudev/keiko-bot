"""Default roles: its saved document and opening its card."""

from tests.behavioral.flows.common import GUILD_ID, seed_document

FORM = "default_roles"

ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "default_roles": {"style": "role", "values": "202"},
    "default_roles_bot": {"style": "role", "values": ["201"]},
}
PAUSED = {**ENABLED, "enabled": False}


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
