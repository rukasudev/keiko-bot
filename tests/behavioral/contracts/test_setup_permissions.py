"""The /setup Permissions button: what Keiko is missing, feature by feature.

A missing permission used to show up only when a feature failed: a welcome that
never arrived, a role that was never given. The report reads each configured
feature's saved answers through the platform (the channels and roles it names),
checks what that feature needs there, and says it in the admin's language.

Guaranteed: a server where Keiko has everything reads as fine for every
configured feature, in declared order; a missing channel permission names the
channel and the permission; a deleted channel is reported; a role above Keiko's
own is reported for auto roles, and a missing server permission is reported;
the exempt channels and roles of block links need nothing; and a server with
nothing set up is told there is nothing to check yet.
"""
from types import SimpleNamespace

import pytest

from app.constants import Commands
from app.services.utils import ml
from tests.behavioral.golden.paths.block_links import ENABLED as BLOCK_LINKS
from tests.behavioral.golden.paths.common import GUILD_ID, seed_document
from tests.behavioral.golden.paths.default_roles import ENABLED as DEFAULT_ROLES
from tests.behavioral.golden.paths.notifications_twitch import ENABLED as TWITCH
from tests.behavioral.golden.paths.welcome_messages import ENABLED as WELCOME

pytestmark = [pytest.mark.behavioral, pytest.mark.shared_contract("setup_dashboard")]

BASE = "commands.commands.setup.permissions"
EVERYTHING = {
    "view_channel", "send_messages", "embed_links", "attach_files",
    "manage_roles", "manage_messages",
}


class Granted(SimpleNamespace):
    def __getattr__(self, name):
        return False


def guild(channels, roles=None, top_role=10, server=EVERYTHING):
    me = SimpleNamespace(
        top_role=SimpleNamespace(position=top_role),
        guild_permissions=Granted(**{name: True for name in server}),
    )

    def get_channel(channel_id):
        if channel_id not in channels:
            return None
        granted = channels[channel_id]
        return SimpleNamespace(
            id=channel_id,
            permissions_for=lambda member: Granted(**{name: True for name in granted}),
        )

    role_objects = [
        SimpleNamespace(id=role_id, name=f"role-{role_id}", position=position, managed=False)
        for role_id, position in (roles or {}).items()
    ]

    def get_role(role_id):
        return next((role for role in role_objects if role.id == role_id), None)

    return SimpleNamespace(
        id=int(GUILD_ID),
        me=me,
        roles=role_objects,
        get_channel=get_channel,
        get_role=get_role,
    )


def configure(deps, *documents):
    deps.mongo_client.guild.moderations.insert_one(
        {"guild_id": GUILD_ID, **{form: True for form, _ in documents}}
    )
    for form, document in documents:
        seed_document(deps, form, document)


async def report(server, locale="pt-br"):
    from app.services.setup import permission_report

    return await permission_report(server, GUILD_ID, "555", locale)


def field_of(embed, command_key, locale="pt-br"):
    spec = next(f for f in Commands.SETUP_FEATURES if f["command_key"] == command_key)
    name = f"{spec['emoji']} {ml(f'buttons.setup.{spec['button_key']}.label', locale)}"
    return next(field.value for field in embed.fields if field.name == name)


def names(permission, locale="pt-br"):
    return ml(f"{BASE}.names.{permission}", locale)


@pytest.fixture
def everything_configured(deps):
    configure(
        deps,
        (Commands.WELCOME_MESSAGES_KEY, WELCOME),
        (Commands.DEFAULT_ROLES_KEY, DEFAULT_ROLES),
        (Commands.BLOCK_LINKS_KEY, BLOCK_LINKS),
        (Commands.NOTIFICATIONS_TWITCH_KEY, TWITCH),
    )


FULL_CHANNELS = {100: EVERYTHING, 101: EVERYTHING, 102: EVERYTHING}
LOW_ROLES = {201: 1, 202: 2}


async def test_a_server_where_keiko_has_everything_reads_as_fine(everything_configured):
    embed = await report(guild(FULL_CHANNELS, LOW_ROLES))

    ok = ml(f"{BASE}.ok", "pt-br")
    assert [field.value for field in embed.fields] == [ok, ok, ok, ok]
    assert len(embed.fields) == 4, "only configured features are checked"


async def test_a_missing_channel_permission_names_the_channel_and_the_permission(
    everything_configured,
):
    channels = {**FULL_CHANNELS, 101: EVERYTHING - {"embed_links"}}

    value = field_of(await report(guild(channels, LOW_ROLES)), Commands.WELCOME_MESSAGES_KEY)

    assert "<#101>" in value and names("embed_links") in value


async def test_a_deleted_channel_is_reported(everything_configured):
    channels = {100: EVERYTHING, 101: EVERYTHING}

    value = field_of(await report(guild(channels, LOW_ROLES)), Commands.NOTIFICATIONS_TWITCH_KEY)

    assert ml(f"{BASE}.channel-gone", "pt-br") in value


async def test_a_role_above_keiko_and_a_missing_server_permission_are_reported(
    everything_configured,
):
    server = guild(FULL_CHANNELS, {201: 1, 202: 20}, server=EVERYTHING - {"manage_roles"})

    value = field_of(await report(server), Commands.DEFAULT_ROLES_KEY)

    assert "<@&202>" in value, "a role above mine is one I cannot give"
    assert "<@&201>" not in value
    assert names("manage_roles") in value


async def test_the_exempt_channels_and_roles_of_block_links_need_nothing(deps):
    configure(deps, (Commands.BLOCK_LINKS_KEY, BLOCK_LINKS))

    embed = await report(guild({100: set()}, {201: 99}))

    assert field_of(embed, Commands.BLOCK_LINKS_KEY) == ml(f"{BASE}.ok", "pt-br")


async def test_block_links_without_manage_messages_is_reported(deps):
    configure(deps, (Commands.BLOCK_LINKS_KEY, BLOCK_LINKS))

    embed = await report(guild({100: set()}, server=EVERYTHING - {"manage_messages"}))

    assert names("manage_messages") in field_of(embed, Commands.BLOCK_LINKS_KEY)


async def test_a_server_with_nothing_set_up_has_nothing_to_check(deps):
    embed = await report(guild({}))

    assert embed.fields == []
    assert embed.description == ml(f"{BASE}.nothing", "pt-br")
