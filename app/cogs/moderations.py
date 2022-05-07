from logger import logger
from datetime import datetime
from discord.ext import commands
from components.embed import CustomEmbeds
from components.messages import CreateFormQuestions

import discord
import re


class Moderation:
    def __init__(self, bot):
        self.bot = bot

    async def block_links(self, message, guild_id):
        enabled = None

        try:
            enabled = self.bot.mongo_db.find_parameters_by_guild(guild_id)[
                "block_links"
            ]
        except Exception:
            pass

        if enabled:
            parameters = self.bot.mongo_db.find_blocked_links_by_guild(guild_id)
            permited_chats = parameters["unblocked_chats"]
            has_links = self.check_message_has_link(message)

            if len(has_links) > 0 and not str(message.channel.id) in permited_chats:
                await message.delete()
                await message.channel.send(parameters["message"])

    def check_message_has_link(self, message):
        return re.findall(
            "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
            message.content.lower(),
        )

    def get_all_guild_channels(self, guild):
        return [channel for channel in guild.text_channels]

    def apply_default_role_all_members():
        pass
        # role = discord.utils.get(guild.roles, id=838123185978998788)

        # for member in guild.members:
        #     if not role in member.roles:
        #         print(""")
        #         await member.add_roles(role)
        # print("Os cargos estão atualizados corretamente!")

    def apply_role_in_member(guild, role_id):
        pass
        # role = discord.utils.get(guild.roles, id=role_id)
        # await member.add_roles(role)

    async def send_welcome_message():
        pass
        # random_number = random.randint(0, 19)
        # rules_channel = self.bot.get_channel(838125350185074758)
        # channel = self.bot.get_channel(838123186142052442)

        # embed = discord.Embed(
        #     title=random.choice(self.config.welcome_messages_title).replace(
        #         "{person_name}", member.name
        #     ),
        #     description=random.choice(
        #         self.config.welcome_messages_descriptions
        #     ).replace("{channel_mention}", rules_channel.mention),
        #     color=0xFFCFFF,
        # )
        # embed.set_thumbnail(url=member.avatar_url)
        # embed.set_footer(text="")

        # if random_number == 15:
        #     embed.set_image(url="")

        # await channel.send(embed=embed)


class ModerationCogs(commands.Cog, name="Moderations"):
    def __init__(self, bot):
        self.bot = bot
        self.moderation = Moderation(bot)
        self.embed = CustomEmbeds()

    @commands.command(
        name="block_links",
        aliases=["bl"],
        # description="", //TODO: Add descriptions in commands
    )
    async def _block_links(self, ctx):
        guild_id = str(ctx.guild.id)
        enabled = self.bot.mongo_db.find_parameters_by_guild(guild_id).get(
            "block_links"
        )

        if enabled:
            questions = [
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Deseja ativar o bloqueador de links?", desc=True
                    ),
                    "action": "button",
                },
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Escolha abaixo quais chats serão permitidos links"
                    ),
                    "action": "choices",
                    "options": self.moderation.get_all_guild_channels(ctx.guild),
                },
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Insira a mensagem a ser enviada pelo bot ao bloquear link"
                    ),
                    "action": "confirm",
                },
            ]

            teste = CreateFormQuestions(questions, "teste")

            await ctx.send("Teste", view=teste)

        else:
            pass


def setup(bot):
    bot.add_cog(ModerationCogs(bot))
