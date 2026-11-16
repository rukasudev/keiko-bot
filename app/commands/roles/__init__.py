"""Default roles: the roles a new member gets, declared once."""

from app.commands.roles.cog import Roles
from app.commands.roles.service import FEATURE, give_roles
from app.core.commands import Command, Hooks, JoinStage

COMMAND = Command(
    key="default_roles",
    cogs=(Roles,),
    feature=FEATURE,
    group="moderations",
    on_setup=True,
    emoji="👩‍🎓",
    server_permissions=("manage_roles",),
    assigns_roles=True,
    hooks=Hooks(member_joined=give_roles, join_stage=JoinStage.CHANGES_MEMBER),
)
