from components.buttons import OptionsButton
import discord


class CreateFormQuestions(discord.ui.View):
    def __init__(self, questions: discord.Embed):
        self.index = 1
        self.questions = questions
        super().__init__()

    @discord.ui.button(label="✔️", style=discord.ButtonStyle.green)
    async def initial_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        actual_question = self.questions[self.index]

        if actual_question["action"] == "options":
            self.remove_item(button)

            for index, option in enumerate(actual_question["options"]):
                option_button = OptionsButton(
                    options_custom_id=str(index), options_label=str(option)
                )
                self.add_item(option_button)

            self.add_item(button)

        if actual_question["action"] == "text":
            pass
        if actual_question["action"] == "confirm":
            pass

        await interaction.message.edit(
            embed=self.questions[self.index + 1]["message"], view=self
        )

        self.index += 1
