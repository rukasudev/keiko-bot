import discord
from discord import app_commands
from discord.ext import tasks

from app.bot import DiscordBot
from app.components.embed import response_embed, response_error_embed
from app import logger
from app.constants import Commands as commands_constants
from app.constants import KeikoIcons
from app.constants import LogTypes as logconstants
from app.data import birthdays as birthdays_data
from app.decorators import keiko_command
from app.services import reminders_birthdays as birthdays_service
from app.services.dates import format_mm_dd_label, get_month_choices, parse_date_parts
from app.services.trace import settle
from app.services.utils import parse_locale
from app.translator import locale_str
from app.types.cogs import Cog
from app.views.confirm_action import ConfirmActionView


@app_commands.guild_only()
class Birthday(Cog, name=locale_str("birthday", type="name", namespace="birthday-personal")):
    def __init__(self, bot: DiscordBot):
        self.bot = bot
        super().__init__()
        self.reconcile_reminders.start()

    async def cog_unload(self) -> None:
        self.reconcile_reminders.cancel()

    @tasks.loop(minutes=commands_constants.BIRTHDAY_RECONCILE_MINUTES)
    async def reconcile_reminders(self) -> None:
        """Finish the birthdays whose reminder could not be created at the time.

        A refusal from the reminders API used to be stored as a null id and
        forgotten, so the birthday was kept and simply never fired. This is the
        pass that closes that, including for the records already saved that way.
        """
        try:
            repaired = birthdays_service.reconcile_missing_reminders(
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
        await self.bot.wait_until_ready()

    @keiko_command(
        name=locale_str("birthday", type="name", namespace="birthday-personal"),
        description=locale_str("desc", type="desc", namespace="birthday-personal"),
    )
    @app_commands.rename(
        month=locale_str("month", type="birthday-params.month.name", namespace="commons"),
        day=locale_str("day", type="birthday-params.day.name", namespace="commons"),
    )
    @app_commands.describe(
        month=locale_str("month-desc", type="birthday-params.month.desc", namespace="commons"),
        day=locale_str("day-desc", type="birthday-params.day.desc", namespace="commons"),
    )
    @app_commands.choices(month=get_month_choices())
    async def birthday_personal(self, interaction: discord.Interaction, month: int, day: int) -> None:
        date = parse_date_parts(day, month)
        if not date:
            _refused("invalid-date")
            embed = response_error_embed("invalid-date", interaction.locale, footer=True)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        guild_id = str(interaction.guild.id)
        if not birthdays_data.is_birthday_enabled(guild_id) or not birthdays_data.find_birthday_config(guild_id):
            _refused("reminders-birthdays-disabled")
            embed = response_error_embed("reminders-birthdays-disabled", interaction.locale, footer=True)
            return await interaction.response.send_message(embed=embed, ephemeral=True)

        await interaction.response.defer(ephemeral=True, thinking=True)

        existing = birthdays_data.find_birthday_item(guild_id, str(interaction.user.id))
        if existing:
            if not birthdays_service.can_self_edit_birthday(existing):
                _refused("reminders-birthdays-self-edit-limit")
                embed = response_error_embed("reminders-birthdays-self-edit-limit", interaction.locale, footer=True)
                return await interaction.followup.send(embed=embed, ephemeral=True)

            current_date = existing.get("date")
            current_date_text = format_mm_dd_label(current_date, interaction.locale) or "-"
            embed = response_embed(
                "commands.commands.birthday-personal.overwrite-confirm",
                interaction.locale,
                footer=True,
                image=True,
            )
            embed.description = (
                embed.description
                .replace("{current_date}", current_date_text)
                .replace("{new_date}", format_mm_dd_label(date, interaction.locale))
            )
            async def confirm_overwrite(confirm_interaction: discord.Interaction) -> None:
                birthdays_service.upsert_birthday(
                    guild_id=guild_id,
                    user_id=str(interaction.user.id),
                    mm_dd=date,
                    increment_self_edit=True,
                )
                settle("replaced")
                logger.info("🎂 birthday replaced", log_type=logconstants.COMMAND_INFO_TYPE)
                response = response_embed(
                    "commands.commands.birthday-personal.overwrite-response",
                    interaction.locale,
                    footer=True,
                    image=True,
                )
                response.set_thumbnail(url=KeikoIcons.IMAGE_03)
                response.description = response.description.replace(
                    "{date}",
                    format_mm_dd_label(date, interaction.locale),
                )
                await confirm_interaction.response.edit_message(embed=response, view=None)

            view = ConfirmActionView(
                on_confirm=confirm_overwrite,
                locale=parse_locale(interaction.locale),
                trace_name=interaction.command.qualified_name,
            )
            settle("asked")
            logger.info(
                "🎂 asked to replace the saved birthday",
                log_type=logconstants.COMMAND_INFO_TYPE,
            )
            await interaction.followup.send(embed=embed, view=view, ephemeral=True)
            return

        birthdays_service.upsert_birthday(
            guild_id=guild_id,
            user_id=str(interaction.user.id),
            mm_dd=date,
        )
        settle("registered")
        logger.info("🎂 birthday registered", log_type=logconstants.COMMAND_INFO_TYPE)

        embed = response_embed(
            "commands.commands.birthday-personal.response",
            interaction.locale,
            footer=True,
            image=True,
        )
        embed.set_thumbnail(url=KeikoIcons.IMAGE_03)
        embed.description = embed.description.replace(
            "{date}",
            format_mm_dd_label(date, interaction.locale),
        )

        await interaction.followup.send(embed=embed, ephemeral=True)


def _refused(reason: str) -> None:
    settle("refused")
    logger.warn(f"birthday refused: {reason}", log_type=logconstants.COMMAND_WARN_TYPE)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Birthday(bot))
