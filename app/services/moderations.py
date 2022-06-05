from components.messages import CreateFormQuestions
from components.embed import CustomEmbeds
from logger import logger
import re


class Moderation:
    def __init__(self, bot):
        self.bot = bot
        self.embed = CustomEmbeds()

    async def block_links_on_message(self, guild_id, message):
        enabled = self.bot.mongo_db.moderations.find_parameter_by_guild(
            guild_id, "block_links"
        )

        if enabled:
            parameters = self.bot.mongo_db.moderations.find_blocked_links_by_guild(
                guild_id
            )
            permited_chats = parameters["unblocked_chats"]
            permited_links = parameters["permited_links"]
            message_has_link = self.check_message_has_link(message, permited_links)
            message_chat = str(message.channel.id)

            if message_has_link and message_chat not in permited_chats:
                await message.delete()
                await message.channel.send(parameters["message"], delete_after=5)

    async def block_links_manager(self, ctx, guild_id):
        enabled = self.bot.mongo_db.moderations.find_parameters_by_guild(guild_id)[
            "block_links"
        ]

        if not enabled:
            questions = [
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Deseja ativar o bloqueador de links?", desc_type=1
                    ),
                    "action": "button",
                },
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Escolha abaixo quais chats serão permitidos links",
                        desc_text="Clique nos botões abaixo para selecionar os chats que serão liberados enviar links",
                        desc_type=3,
                    ),
                    "action": "options",
                    "options": self.get_all_guild_channels(ctx.guild),
                },
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Links permitidos",
                        desc_text="Escolha abaixo alguns sites que o envio de links será permitido",
                        desc_type=3,
                    ),
                    "action": "options",
                    "options": [
                        "Youtube",
                        "Spotify",
                        "Twitter",
                        "Facebook",
                        "Instagram",
                        "Twitch",
                        "Tiktok",
                        "Reddit",
                    ],
                },
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Resposta do bot ao bloquear links",
                        desc_text="""
                            Digite abaixo a mensagem a ser enviada pelo bot ao bloquear link 
                            (exemplo: Links não são permitidos nesse chat.)
                        """,
                        desc_type=3,
                    ),
                    "action": "text",
                },
                {
                    "message": self.embed.block_link_message_with_title(
                        title="Tudo certo?", desc_type=2
                    ),
                    "action": "recap-confirm",
                },
                {
                    "action": "confirm",
                    "table": "block_links",
                },
            ]

            embeded_form = CreateFormQuestions(ctx, self.bot, questions)

            await ctx.send(embed=questions[0]["message"], view=embeded_form)

    def check_message_has_link(self, message, permited_links):
        links = re.findall(
            "http[s]?://(?:[a-zA-Z]|[0-9]|[$-_@.&+]|[!*\(\),]|(?:%[0-9a-fA-F][0-9a-fA-F]))+",
            message.content.lower(),
        )

        for permited in permited_links:
            for index, link in enumerate(links):
                if permited in link:
                    links.pop(index)

        return links

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
