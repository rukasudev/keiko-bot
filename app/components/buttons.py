import discord
import mongo


class RedButtonToCancelMessage(discord.ui.Button):
    def __init__(self):
        super().__init__(label="❌", style=discord.ButtonStyle.red)

    async def callback(self, interaction):
        pass


class GreenButtonToConfirmMessage(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✔️", style=discord.ButtonStyle.green)

    async def callback(self, interaction):
        pass


class GreenButtonToConfirmOptions(discord.ui.Button):
    def __init__(self):
        super().__init__(label="✔️", style=discord.ButtonStyle.green)

    async def callback(self, interaction):
        pass


class OptionsButton(discord.ui.Button):
    def __init__(self, options_label, options_custom_id):
        super().__init__(
            label=options_label,
            style=discord.ButtonStyle.gray,
            custom_id=options_custom_id,
        )

    async def callback(self, interaction):
        index = int(interaction.data["custom_id"])
        view = discord.ui.View.from_message(interaction.message)
        view.children[index].style = discord.ButtonStyle.primary

        for button in view.children:
            button.callback = self.callback

        await interaction.response.edit_message(view=view)
