import discord


def parse_dict_to_embed(data: dict[str, str]) -> discord.Embed:
    """Parse dictionary to discord Embed object"""

    embed = discord.Embed(
        color=int(data["color"], base=16),
        title=data["title"],
        description=data["description"],
    )

    if data.get("emoji"):
        embed.title = f"{data.get('emoji')} {embed.title}"

    if data.get("thumbnail"):
        embed.set_thumbnail(url=data["thumbnail"])

    if data.get("image"):
        embed.set_image(url=data["image"])

    if data.get("footer"):
        embed.set_footer(text=data["footer"])

    return embed
