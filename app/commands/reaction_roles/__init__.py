"""Reaction roles: menus of role buttons Keiko posts in a channel, declared once."""

from app.commands.reaction_roles.cog import ReactionRoles
from app.commands.reaction_roles.service import FEATURE, reaction_role_buttons
from app.constants import FeaturePermissions
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="reaction_roles",
    cogs=(ReactionRoles,),
    feature=FEATURE,
    group="moderations",
    on_setup=True,
    emoji="🏷️",
    channel_permissions=FeaturePermissions.SENDS_TO_A_CHANNEL,
    server_permissions=("manage_roles",),
    assigns_roles=True,
    hooks=Hooks(dynamic_items=reaction_role_buttons),
)
