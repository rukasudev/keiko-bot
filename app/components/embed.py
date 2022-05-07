import discord


class CustomEmbeds:
    def block_link_message_with_title(
        self, title: str, desc: bool = False
    ) -> discord.Embed:
        question = discord.Embed(
            title=title,
            color=0x00FF00,
        )

        description = """
            A função bloqueador de links faz o BOT analisar as mensagens enviadas no servidor e excluí-las caso a mensagem contenha algum link.
            
            **Configurações** 📝
            • Chats permitidos: Configurar chats que são permitidos enviar links.
            • Links permitidos: Configurar links que são permitidos serem enviados.
            • Resposta: Mensagem do BOT no chat ao excluir uma mensagem com link.               
        """

        question.description = description if desc else False

        question.set_thumbnail(url="https://im5.ezgif.com/tmp/ezgif-5-a5617b47ae.gif")
        question.set_footer(
            text="Em caso de problemas, utilize o comando !report bl <seu_report>"
        )
        return question
