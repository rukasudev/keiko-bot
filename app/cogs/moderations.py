from logger import logger
from discord.ext import commands
from services.moderations import Moderation


class ModerationCogs(commands.Cog, name="Moderations"):
    def __init__(self, bot):
        self.bot = bot
        self.moderations_service = Moderation(bot)

    @commands.Cog.listener()
    async def on_message(self, message):
        guild_id = str(message.guild.id)

        if not message.author.bot:
            await self.moderations_service.block_links_on_message(guild_id, message)

    @commands.command(
        name="block_links",
        aliases=["bl"],
        description="Adicionar bloqueador de links ao servidor?",
    )
    async def _block_links(self, ctx):
        guild_id = str(ctx.guild.id)

        await self.moderations_service.block_links_manager(ctx, guild_id)


async def setup(bot):
    await bot.add_cog(ModerationCogs(bot))
