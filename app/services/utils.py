import discord


def get_text_channels_id(guild, channels: list) -> list:
    return [
        str(discord.utils.get(guild.channels, name=channel).id) for channel in channels
    ]
