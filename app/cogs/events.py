import asyncio
from datetime import datetime

import discord
from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import CogsConstants as cogconstants
from app.constants import Commands as commandsconstants
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants
from app.data.moderations import find_moderations_by_guild
from app.services import block_links as block_links_service
from app.services import default_roles as default_roles_service
from app.services import stream_elements as stream_elements_service
from app.services import analytics, analytics_reports, work
from app.services.cache import increment_redis_key
from app.services.cogs import features_on
from app.services.guilds import join_guild
from app.services.moderations import leave_guild
from app.services.utils import (
    cogs_manager,
    format_relative_time,
    get_available_roles_by_guild,
    is_guild_admin,
)
from app.services.welcome_messages import send_welcome_message
from app.types.cogs import Cog
from app.views.greetings import GreetingsView


class Events(Cog, name="events"):
    def __init__(self, bot: DiscordBot) -> None:
        self.bot = bot
        super().__init__()

    async def cog_load(self) -> None:
        self.sweep_forms.start()

    async def cog_unload(self) -> None:
        self.sweep_forms.cancel()

    @tasks.loop(seconds=ViewConstants.FORM_SWEEP_SECONDS)
    async def sweep_forms(self) -> None:
        """Close the forms nobody finished and forget the ones that ended."""
        from app.settings.discord.callbacks import RUNTIME

        try:
            await RUNTIME.sweep()
        except Exception as error:
            logger.warn(
                f"Form sweep failed: {type(error).__name__}: {error}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )

    @sweep_forms.before_loop
    async def before_sweep_forms(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        await self.bot.wait_until_ready()

        if not self.bot.synced:
            await cogs_manager(self.bot, "load", cogconstants.LAZY_LOAD_COGS)
            await self.bot.tree.sync()
            await self.bot.tree.sync(
                guild=discord.Object(self.bot.config.ADMIN_GUILD_ID)
            )
            self.bot.synced = True

        self.bot.add_view(GreetingsView())

        self.bot.ready_time = datetime.now()

        ready_message = (
            f"\n---------------------------------------------------\n"
            f"🎉 Keiko Initialized Successfully!\n"
            f"⏰ Ready Time: {self.bot.ready_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"🔁 Synced with Tree: {'Yes' if self.bot.synced else 'No'}\n"
            f"🤖 Bot Name: {self.bot.application.name}\n"
            f"👤 Author: {self.bot.application.owner.name}\n"
            f"🏠 Total Guilds: {len(self.bot.guilds)}\n"
            f"👥 Total Users: {len(self.bot.users)}\n"
            f"📌 Prefix: {self.bot.command_prefix}\n"
            f"🏷️ Version: {self.bot.config.APP_VERSION}\n"
            f"🎮 Current Activity: {self.bot.activity.name}\n"
            f"🐶 Current Status: {self.bot.status.name}️\n"
            f"---------------------------------------------------"
        )
        logger.info(ready_message, log_type=logconstants.APPLICATION_STARTUP_TYPE)

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        async with work.listener(
            "on_member_join", member.guild.id, user_id=member.id
        ) as joined:
            if get_available_roles_by_guild(member.guild):
                await joined.run(
                    commandsconstants.DEFAULT_ROLES_KEY,
                    default_roles_service.set_on_member_join(member),
                )

            await joined.run(
                commandsconstants.WELCOME_MESSAGES_KEY, send_welcome_message(member)
            )

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return

        guild_id = str(message.guild.id)
        prefix = self.bot.config.PREFIX

        async with work.listener(
            "on_message",
            message.guild.id,
            user_id=message.author.id,
            channel_id=message.channel.id,
            message_length=len(message.content or ""),
        ) as checked:
            if message.content.startswith(prefix):
                await checked.run(
                    commandsconstants.INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
                    stream_elements_service.check_message(guild_id, message, prefix),
                )

            await checked.run(
                commandsconstants.BLOCK_LINKS_KEY,
                block_links_service.check_message(guild_id, message),
            )

    @commands.Cog.listener()
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        async with work.listener(
            "on_raw_message_edit", payload.guild_id, channel_id=payload.channel_id
        ) as checked:
            await checked.run(
                commandsconstants.BLOCK_LINKS_KEY,
                block_links_service.check_edited_message(self.bot, payload),
            )

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.application_command:
            return None

        if not interaction.command:
            return None

        async with work.listener(
            "on_interaction",
            interaction.guild_id,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id,
        ) as invoked:
            await invoked.run("count", self._count_invocation(interaction))

    async def _count_invocation(self, interaction: discord.Interaction) -> None:
        # Context menus are ContextMenu, not Command, so they carry no `_attr`.
        feature = getattr(interaction.command, "_attr", None)

        if feature and str(interaction.user.id) != str(self.bot.owner_id):
            await asyncio.to_thread(
                increment_redis_key, f"{logconstants.COMMAND_CALL_TYPE}:{feature}"
            )

        analytics.emit(
            "command.invoked",
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            command=interaction.command.qualified_name,
            source=analytics.resolve_source(interaction),
            feature=feature,
            is_admin=is_guild_admin(interaction.user),
        )

    @commands.Cog.listener()
    async def on_guild_join(self, guild: discord.Guild):
        async with work.listener("on_guild_join", guild.id) as joined:
            await joined.run("record", self._record_join(guild))
            await joined.run("greeting", GreetingsView().send(guild))

    async def _record_join(self, guild: discord.Guild) -> None:
        owner_id = str(guild.owner.id)
        exist, total_servers = await asyncio.to_thread(join_guild, guild.id, owner_id)
        action = "Joined new guild" if not exist else "Joined again"

        total_guilds = len(self.bot.guilds)

        analytics.emit(
            "guild.joined",
            guild_id=guild.id,
            returning=bool(exist),
            size_bucket=analytics.bucket_size(guild.member_count),
            owner_guilds=total_servers,
        )

        logger.info(
            f"{action} by {guild.owner.mention}\nInvited by: {guild.owner.mention} ({total_servers} server{'s' if total_servers != 1 else ''} total)\nTotal servers: {total_guilds}",
            guild_id=guild.id,
            owner_id=guild.owner.id,
            log_type=logconstants.EVENT_JOIN_GUILD_TYPE,
        )

    @commands.Cog.listener()
    async def on_guild_remove(self, guild: discord.Guild):
        async with work.listener("on_guild_remove", guild.id) as left:
            await left.run("report", self._report_leaving(guild))
            await left.run("snapshot", self._snapshot_leaving(guild))
            await left.run(
                "leave", asyncio.to_thread(leave_guild, guild.id, str(self.bot.user.id))
            )

    async def _report_leaving(self, guild: discord.Guild) -> None:
        moderations = await asyncio.to_thread(find_moderations_by_guild, guild.id)
        active_commands = await asyncio.to_thread(features_on, str(guild.id))

        duration_info = ""
        created_at = (moderations or {}).get("created_at")
        if created_at:
            duration_info = f"\nLeft after {format_relative_time(created_at).replace(' ago', '')} (added_at: {created_at.strftime('%Y-%m-%d %H:%M:%S')})"

        commands_info = f"\nActive commands: {', '.join(active_commands)}" if active_commands else "\nActive commands: none"

        logger.info(
            f"Left guild by {guild.owner.id}{commands_info}{duration_info}",
            guild_id=guild.id,
            owner_id=guild.owner.id,
            log_type=logconstants.EVENT_LEFT_GUILD_TYPE,
        )

    async def _snapshot_leaving(self, guild: discord.Guild) -> None:
        snapshot = await asyncio.to_thread(analytics_reports.guild_snapshot, str(guild.id))
        analytics.emit("guild.removed", guild_id=guild.id, **snapshot)
        await asyncio.to_thread(analytics.flush)


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Events(bot))
