import discord

from app import bot
from app.views.log_inspection import LogInspectionView


def get_guild(guild_id: int) -> discord.Guild:
    guild = bot.get_guild(int(guild_id))
    return guild

def get_user(user_id: int) -> discord.Member:
    user = bot.get_user(int(user_id))
    return user

async def parse_log_message_to_embed(interaction: discord.Interaction, message: discord.Message) -> discord.Embed:
    for item in message.embeds[0].fields:
        if item.name == "Guild ID":
            guild_id = item.value
        elif item.name == "User ID":
            user_id = item.value.replace("<@", "").replace(">", "")

    guild = get_guild(guild_id)
    user = get_user(user_id)

    view = LogInspectionView(user, guild)

    await view.send(interaction)
