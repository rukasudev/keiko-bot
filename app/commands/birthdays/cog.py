"""/birthday: a member saves their own birthday; the reminders it missed are retried."""

from collections.abc import Mapping
from typing import Any

import discord
from discord import app_commands
from discord.ext import tasks

from app import logger
from app.bot import DiscordBot
from app.commands.birthdays import service as birthdays_service
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.core.calendar import parse_date_parts
from app.core.dates import format_mm_dd_label, get_month_choices
from app.core.trace import settle
from app.core.utils import parse_locale
from app.decorators import keiko_command
from app.translator import locale_str
from app.types.cogs import Cog
from app.ui.confirm_action import ConfirmActionView
from app.ui.embed import response_embed, response_error_embed


@app_commands.guild_only()
class Birthday(
    Cog,
    name=locale_str(
        "birthday", type="name", namespace="reminders_birthday.slash.commands.personal"
    ),
):
    """The cog of /birthday and of the reminder reconciliation."""

    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def cog_load(self) -> None:
        """Start the reconciliation once the bot adds the cog."""
        self.reconcile_reminders.start()

    async def cog_unload(self) -> None:
        """Stop the reconciliation."""
        self.reconcile_reminders.cancel()

    @tasks.loop(minutes=commands_constants.BIRTHDAY_RECONCILE_MINUTES)
    async def reconcile_reminders(self) -> None:
        """Create the reminders of the birthdays whose reminder could not be created."""
        try:
            repaired = await birthdays_service.reconcile_missing_reminders(
                commands_constants.BIRTHDAY_RECONCILE_BATCH
            )
        except Exception as error:
            logger.warn(
                f"Birthday reminder reconciliation failed: "
                f"{type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return

        if repaired:
            logger.info(
                f"Created {repaired} birthday reminder(s) that had been left pending",
                log_type=logconstants.BOT_ACTION_TYPE,
            )

    @reconcile_reminders.before_loop
    async def before_reconcile(self) -> None:
        """Wait for the bot to be ready."""
        await self.bot.wait_until_ready()

    @keiko_command(
        name=locale_str(
            "birthday",
            type="name",
            namespace="reminders_birthday.slash.commands.personal",
        ),
        description=locale_str(
            "desc",
            type="description",
            namespace="reminders_birthday.slash.commands.personal",
        ),
    )
    @app_commands.rename(
        month=locale_str(
            "month", type="params.month.name", namespace="reminders_birthday.slash"
        ),
        day=locale_str(
            "day", type="params.day.name", namespace="reminders_birthday.slash"
        ),
    )
    @app_commands.describe(
        month=locale_str(
            "month-desc",
            type="params.month.description",
            namespace="reminders_birthday.slash",
        ),
        day=locale_str(
            "day-desc",
            type="params.day.description",
            namespace="reminders_birthday.slash",
        ),
    )
    @app_commands.choices(month=get_month_choices())
    async def birthday_personal(
        self, interaction: discord.Interaction, month: int, day: int
    ) -> None:
        """Save the member's own birthday, asking first before replacing one."""
        date = parse_date_parts(day, month)
        if not date:
            _refused("invalid-date")
            embed = response_error_embed(
                "invalid_date", interaction.locale, footer=True
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        if not await birthdays_service.accepts_birthdays(guild_id):
            _refused("reminders-birthdays-disabled")
            embed = response_error_embed(
                "reminders_birthday.errors.disabled", interaction.locale, footer=True
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = await birthdays_service.find_birthday(
            guild_id, str(interaction.user.id)
        )
        if existing:
            await _ask_to_replace(interaction, guild_id, date, existing)
            return

        await birthdays_service.upsert_birthday(
            guild_id=guild_id,
            user_id=str(interaction.user.id),
            mm_dd=date,
            display_name=interaction.user.display_name,
        )
        settle("registered")
        logger.info("🎂 birthday registered", log_type=logconstants.COMMAND_INFO_TYPE)

        embed = response_embed(
            "reminders_birthday.responses.personal",
            interaction.locale,
            values={"date": format_mm_dd_label(date, interaction.locale)},
            thumbnail=KeikoIcons.IMAGE_03,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


async def _ask_to_replace(
    interaction: discord.Interaction,
    guild_id: str,
    date: str,
    existing: Mapping[str, Any],
) -> None:
    if not birthdays_service.can_self_edit_birthday(existing):
        _refused("reminders-birthdays-self-edit-limit")
        embed = response_error_embed(
            "reminders_birthday.errors.self_edit_limit",
            interaction.locale,
            footer=True,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)
        return

    current_date = existing.get("date")
    current_date_text = (
        format_mm_dd_label(str(current_date), interaction.locale)
        if current_date
        else ""
    ) or "-"
    embed = response_embed(
        "reminders_birthday.responses.overwrite_confirm",
        interaction.locale,
        image=True,
        values={
            "current_date": current_date_text,
            "new_date": format_mm_dd_label(date, interaction.locale),
        },
    )

    async def confirm_overwrite(confirm_interaction: discord.Interaction) -> None:
        await birthdays_service.upsert_birthday(
            guild_id=guild_id,
            user_id=str(interaction.user.id),
            mm_dd=date,
            increment_self_edit=True,
            display_name=interaction.user.display_name,
        )
        settle("replaced")
        logger.info("🎂 birthday replaced", log_type=logconstants.COMMAND_INFO_TYPE)
        response = response_embed(
            "reminders_birthday.responses.overwrite",
            interaction.locale,
            values={"date": format_mm_dd_label(date, interaction.locale)},
            thumbnail=KeikoIcons.IMAGE_03,
        )
        await confirm_interaction.response.edit_message(embed=response, view=None)

    view = ConfirmActionView(
        on_confirm=confirm_overwrite,
        locale=parse_locale(interaction.locale),
        trace_name=interaction.command.qualified_name if interaction.command else None,
    )
    settle("asked")
    logger.info(
        "🎂 asked to replace the saved birthday",
        log_type=logconstants.COMMAND_INFO_TYPE,
    )
    await interaction.followup.send(embed=embed, view=view, ephemeral=True)


def _refused(reason: str) -> None:
    settle("refused")
    logger.warn(f"birthday refused: {reason}", log_type=logconstants.COMMAND_WARN_TYPE)


async def setup(bot: DiscordBot) -> None:
    """Add the /birthday cog."""
    await bot.add_cog(Birthday(bot))
