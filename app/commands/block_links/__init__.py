"""Block links: the links a server refuses, declared once."""

from app.commands.block_links.cog import Block, LinkCheck
from app.commands.block_links.service import FEATURE, check_edited, check_sent
from app.core.commands import Command, Hooks

COMMAND = Command(
    key="block_links",
    cogs=(Block, LinkCheck),
    feature=FEATURE,
    group="moderations",
    on_setup=True,
    emoji="🚫",
    server_permissions=("manage_messages",),
    hooks=Hooks(message_sent=check_sent, message_edited=check_edited),
)
