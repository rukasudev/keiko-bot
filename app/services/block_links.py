import discord

from app.constants import Commands as constants
from app.services import cache
from app.services.moderations import (
    send_command_form_message,
    send_command_manager_message,
)

from .utils import check_message_has_link, check_two_lists_intersection, list_roles_id


async def check_message(guild_id: str, message: discord.Message) -> None:
    """Command service to check if exists link on message"""
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY)

    if not cogs:
        return

    message_author_has_allowed_role = check_two_lists_intersection(
        list_roles_id(message.author.roles),
        cogs[constants.BLOCK_LINKS_ALLOWED_ROLES_KEY].get("values"),
    )

    if message_author_has_allowed_role:
        return

    allowed_chats = cogs[constants.BLOCK_LINKS_ALLOWED_CHATS_KEY].get("values")
    allowed_links = cogs[constants.BLOCK_LINKS_ALLOWED_LINKS_KEY]

    message_has_link = check_message_has_link(message, allowed_links)
    message_chat = str(message.channel.id)

    if message_has_link and message_chat not in allowed_chats:
        await message.delete()
        await message.channel.send(
            cogs[constants.BLOCK_LINKS_ANSWER_KEY], delete_after=5
        )


async def manager(interaction: discord.Interaction, guild_id: str) -> None:
    cogs = cache.get_cog_data_or_populate(guild_id, constants.BLOCK_LINKS_KEY, manager=True)

    if cogs == None:
        return await send_command_form_message(interaction, constants.BLOCK_LINKS_KEY)

    await send_command_manager_message(interaction, constants.BLOCK_LINKS_KEY, cogs)
