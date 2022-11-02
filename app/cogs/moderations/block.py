from app.bot import DiscordBot
from app.logger import logger
from app.services import block_links as block_links_service
from discord import app_commands
from discord.ext import commands
import discord

class Block(app_commands.Group, name="bloquear"):
    @commands.Cog.listener()
    async def on_message(self, message):
        guild_id = str(message.guild.id)

        if not message.author.bot:
            await block_links_service.check_message(guild_id, message)

    @app_commands.command(
        name="links",
        description="Adicionar bloqueador de links ao servidor?",
    )
    async def _block_links(self, ctx: discord.Interaction):
        guild_id = str(ctx.guild.id)
        
        if not ctx.user.guild_permissions.administrator:
            await ctx.response.send_message("Você não tem permissão pra rodar esse comando :/", ephemeral=True)
        
        await block_links_service.manager(ctx=ctx, guild_id=guild_id)
