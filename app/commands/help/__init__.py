"""/help and /ping, Keiko's own prefix commands and its reaction to a mention, declared
once."""

from app.commands.help.cog import Help
from app.commands.help.ping import Ping
from app.commands.help.service import answer_own_prefix, react_to_mention, send_help
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="help",
    cogs=(Help, Ping),
    hooks=Hooks(prefixed_message=answer_own_prefix, message_sent=react_to_mention),
    opens=send_help,
)
