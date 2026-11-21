"""A server as Keiko's permission report reads it: what Keiko may do in the server and
in each channel, and the roles with their positions."""
from types import SimpleNamespace

from tests.behavioral.flows.common import GUILD_ID

EVERYTHING = {
    "view_channel", "send_messages", "embed_links", "attach_files",
    "manage_roles", "manage_messages",
}


class Granted(SimpleNamespace):
    """Permissions where every one not named is denied."""

    def __getattr__(self, name):
        return False


def guild(channels, roles=None, top_role=10, server=EVERYTHING):
    """A server where Keiko holds `server` and, in each of `channels`, its own set."""
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
