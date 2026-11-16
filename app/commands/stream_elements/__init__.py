"""StreamElements: a streamer's chat commands answered on the prefix, declared once."""

from app.commands.stream_elements.cog import StreamElementsCommands
from app.commands.stream_elements.service import FEATURE, answer_prefix
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="stream_elements_commands",
    cogs=(StreamElementsCommands,),
    feature=FEATURE,
    group="integrations",
    on_setup=True,
    emoji="🎮",
    server_permissions=("send_messages",),
    hooks=Hooks(prefixed_message=answer_prefix),
)
