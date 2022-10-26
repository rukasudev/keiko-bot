import discord

from re import findall


def get_guild_text_channels_id(guild, channels: list) -> list:
    return [
        str(discord.utils.get(guild.channels, name=channel).id) for channel in channels
    ]


def get_guild_text_channels(guild):
    return [channel for channel in guild.text_channels]


def check_message_has_link(self, message: str, allowed_links: list[str]) -> list[str]:
    links = findall(
        "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
        message.content.lower(),
    )

    for allowed in allowed_links:
        for index, link in enumerate(links):
            if allowed in link:
                links.pop(index)

    return links


def check_answer_message(ctx, message):
    return message.author == ctx.author and message.channel == ctx.channel
