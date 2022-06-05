import discord


class CustomEmbeds:
    def block_link_message_with_title(
        self, title: str, desc_type: int = 0, desc_text: str = ""
    ) -> discord.Embed:
        question = discord.Embed(
            title=title,
            color=0x00FF00,
        )

        description = False
        footer = "Em caso de problemas, utilize o comando !report bl <seu_report>"

        if desc_type == 0:
            footer = None

        if desc_type == 1:
            description = """
                A função bloqueador de links faz o BOT analisar as mensagens enviadas no servidor e excluí-las caso a mensagem contenha algum link.
                
                **Configurações** 📝
                • Chats permitidos: Configurar chats que são permitidos enviar links.
                • Sites permitidos: Configurar links que são permitidos serem enviados.
                • Resposta: Mensagem do BOT no chat ao excluir uma mensagem com link.               
            """
            question.set_footer(
                text="Em caso de problemas, utilize o comando !report bl <seu_report>"
            )
        if desc_type == 2:
            description = """
                A função bloqueador de links faz o BOT analisar as mensagens enviadas no servidor e excluí-las caso a mensagem contenha algum link.
                
                **Configurações** 📝
                • Chats permitidos: **$options1**
                • Links permitidos: **$options2**
                • Resposta: **$text1**
            """

        if desc_type == 3:
            description = desc_text
            footer = None

        question.description = description
        question.set_footer(
            text="Em caso de problemas, utilize o comando !report bl <seu_report>"
        ) if footer else None

        question.set_thumbnail(url="https://c.tenor.com/6q4MAQrO28cAAAAC/cat-stop.gif")

        return question
