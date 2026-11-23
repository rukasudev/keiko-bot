"""Auto roles: its saved documents, opening its card and adding a rung."""

from tests.behavioral.flows.common import GUILD_ID, seed_document
from tests.generators.moderations import ladder_rung
from tests.mocks.discord import MockRole, create_guild

FORM = "default_roles"

ENABLED = {
    "guild_id": GUILD_ID, "enabled": True,
    "default_roles": {"style": "role", "values": "202"},
    "default_roles_bot": {"style": "role", "values": ["201"]},
}
PAUSED = {**ENABLED, "enabled": False}
LADDER_ONLY = {
    "guild_id": GUILD_ID, "enabled": True,
    "default_roles": {"style": "role", "values": []},
    "default_roles_bot": {"style": "role", "values": []},
    "role_ladder": {"style": "composition", "values": [ladder_rung(7, "202")]},
}


async def open_card(scenario_factory, locale):
    """The setup opened and its intro confirmed: the card on screen."""
    scenario = await scenario_factory(locale=locale).start_command(FORM)
    await scenario.confirm()
    return scenario


def server_where_keiko_gives_every_role():
    """A mock server whose Keiko role sits above every other role."""
    guild = create_guild()
    top = max(role.position for role in guild.roles) + 1
    guild.me.roles = [MockRole(id=999, name="Bot", position=top)]
    return guild


def add_roles(guild, names, first_id):
    """One role per name added to `guild`, below Keiko's, their ids from `first_id`."""
    added = [
        MockRole(id=first_id + index, name=name, position=2, guild=guild)
        for index, name in enumerate(names)
    ]
    guild.roles.extend(added)
    return added


def seed(deps):
    """The enabled document saved."""
    seed_document(deps, FORM, ENABLED)


def seed_paused(deps):
    """The paused document saved."""
    seed_document(deps, FORM, PAUSED)


LADDER_SCREENS = {"setup": "customize:ladder_screen", "panel": "edit:group:ladder"}


async def submit_time(scenario, amount, unit="days"):
    """The time role's time typed as `amount` and its `unit` chosen, in its modal."""
    await scenario.click("customize:time")
    number, chosen = scenario.pending_modal_fields()
    await scenario.submit_modal({number: str(amount), chosen: unit})


async def add_rung(scenario, time, role, where="setup"):
    """A time role of `time` (a number of days, or an amount and a unit) and `role`
    added with Add on the ladder's own screen, the same screen `where` it opens: the
    card's Configurar in the setup, back on the card after; the panel's Edit, where
    the screen stays."""
    amount, unit = time if isinstance(time, tuple) else (time, "days")
    await scenario.click(LADDER_SCREENS[where])
    await scenario.click("add:role_ladder")
    await submit_time(scenario, amount, unit)
    await scenario.select_option(role, target="role")
    await scenario.click("done")
    if where == "setup":
        await scenario.click("back")


def seed_ladder_only(deps, enabled=True):
    """A document with one rung and no join roles saved, paused unless `enabled`."""
    seed_document(deps, FORM, {**LADDER_ONLY, "enabled": enabled})


def daily_pass(scenario, guild):
    """The schedule of the server's daily pass of the roles by time, None without one."""
    found = {"_id": f"role_ladder:{guild.id}"}
    return scenario.get_persisted("keiko", "schedules", found)


def seed_member_roles_only(deps):
    """A document with only the members' join roles saved."""
    seed_document(deps, FORM, {**ENABLED, "default_roles_bot": {"style": "role", "values": []}})
