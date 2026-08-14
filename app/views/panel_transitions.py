"""Leaving a Components V2 message: its flags are fixed at send time, so any
screen that replaces a panel with an embed must delete and resend.
"""
from typing import Optional

import discord


def is_layout_message(message: Optional[discord.Message]) -> bool:
    flags = getattr(message, "flags", None)
    return bool(getattr(flags, "components_v2", False))


async def transition_to_embed(
    interaction: discord.Interaction,
    embed: Optional[discord.Embed],
    view: Optional[discord.ui.View],
    *,
    deferred: bool = False,
    from_layout: bool = False,
) -> None:
    """Show `embed`/`view` in place of whatever the interaction came from."""
    if from_layout or is_layout_message(interaction.message):
        if not deferred:
            await interaction.response.defer()
        await interaction.followup.delete_message(interaction.message.id)
        await interaction.followup.send(embed=embed, view=view, ephemeral=True)
        return

    if deferred:
        await interaction.followup.edit_message(
            message_id=interaction.message.id, embed=embed, view=view
        )
        return

    await interaction.response.edit_message(embed=embed, view=view)


async def close_panel(interaction: discord.Interaction,
                      view: Optional[discord.ui.View] = None) -> None:
    """Acknowledge the interaction and take the panel off the screen."""
    if is_layout_message(interaction.message):
        await interaction.response.defer()
        try:
            await interaction.followup.delete_message(interaction.message.id)
        except discord.HTTPException:
            pass
        return

    await interaction.response.edit_message(view=view)
