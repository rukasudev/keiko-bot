"""/help and /ping, and Keiko's own prefix commands, declared once."""

from app.commands.help.cog import Help
from app.commands.help.ping import Ping
from app.commands.help.service import answer_own_prefix, open_help, send_prefix_hint
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="help",
    cogs=(Help, Ping),
    hooks=Hooks(prefixed_message=answer_own_prefix, message_sent=send_prefix_hint),
    opens=open_help,
)
