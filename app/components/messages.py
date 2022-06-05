from components.buttons import FormQuestion
import discord


class CreateFormQuestions(discord.ui.View):
    def __init__(self, ctx, bot, questions: discord.Embed):
        super().__init__()
        self.add_item(FormQuestion(ctx, bot, questions))


# class MessageUtils:
#     async def check_answer_message(message):
#         return message.author == ctx.author and m.channel == ctx.channel
