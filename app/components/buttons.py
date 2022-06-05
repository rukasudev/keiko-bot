import discord


class FormQuestion(discord.ui.Button):
    def __init__(self, ctx, bot, questions):
        self.ctx = ctx
        self.bot = bot
        self._index = 0
        self._questions = questions
        self.options = {}
        self.options_index = 0
        self.texts = {}
        self.text_index = 0
        super().__init__(label="✔️", style=discord.ButtonStyle.green)

    def _update_form_counter(func):
        async def update_counter(self, args):
            self._index += 1
            await func(self, args)

        return update_counter

    @_update_form_counter
    async def callback(self, interaction):
        view = discord.ui.View.from_message(interaction.message)
        view.clear_items()

        current_question = self._questions[self._index]

        if current_question["action"] == "text":
            await interaction.message.edit(embed=current_question["message"], view=view)
            await interaction.response.defer()

            answer = await interaction.client.wait_for(
                "message", check=lambda message: message.author == self.ctx.author
            )

            self.text_index += 1
            self.texts["$text" + str(self.text_index)] = answer.content

            self._index += 1
            current_question = self._questions[self._index]

            await answer.delete()

        if current_question["action"] == "options":
            await interaction.response.defer()

            for index, option in enumerate(current_question["options"]):
                option_button = OptionsButton(
                    options_custom_id=str(index),
                    options_label=str(option),
                    confirm_button=self,
                )
                view.add_item(option_button)

            self.custom_id = str(len(current_question["options"]))
            self.options_index += 1
            view.add_item(self)

        if current_question["action"] == "recap-confirm":
            message_desc = current_question["message"].description

            for key in self.options.keys():
                message_desc = message_desc.replace(key, ", ".join(self.options[key]))

            for key in self.texts.keys():
                message_desc = message_desc.replace(key, self.texts[key])

            current_question["message"].description = message_desc
            view.add_item(self)

        if current_question["action"] == "confirm":
            guild_id = self.ctx.guild.id
            table = current_question["table"]

            self.bot.mongo_db.moderations.insert_parameters_by_guild(
                guild_id=guild_id,
                table=table,
                texts=self.texts,
                options=self.options,
                guild=self.ctx.guild,
            )

            return

        await interaction.message.edit(embed=current_question["message"], view=view)


class OptionsButton(discord.ui.Button):
    def __init__(self, options_label, options_custom_id, confirm_button):
        self.confirm_button = confirm_button
        super().__init__(
            label=options_label,
            style=discord.ButtonStyle.gray,
            custom_id=options_custom_id,
        )

    async def callback(self, interaction):
        index = int(interaction.data["custom_id"])

        view = discord.ui.View.from_message(interaction.message)
        view.children[index].style = discord.ButtonStyle.primary
        view.remove_item(view.children[-1])

        for button in view.children:
            button.callback = self.callback

        view.add_item(self.confirm_button)

        if self.confirm_button.options.get(
            "$options" + str(self.confirm_button.options_index)
        ):
            self.confirm_button.options[
                "$options" + str(self.confirm_button.options_index)
            ].append(view.children[index].label)
        else:
            self.confirm_button.options[
                "$options" + str(self.confirm_button.options_index)
            ] = [view.children[index].label]

        await interaction.response.edit_message(view=view)
