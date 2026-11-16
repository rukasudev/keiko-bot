"""/setup and the greeting that leads a new server to it, declared once."""

from app.commands.setup.cog import Setup
from app.commands.setup.service import (
    greet_server,
    greeting_views,
    open_setup_dashboard,
)
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="setup",
    cogs=(Setup,),
    hooks=Hooks(guild_joined=greet_server, persistent_views=greeting_views),
    opens=open_setup_dashboard,
)
