from datetime import datetime

import discord
from discord.ext import commands, tasks

from app import logger
from app.bot import DiscordBot
from app.constants import CogsConstants as cogconstants
from app.constants import Commands as commandsconstants
from app.constants import GuildConstants as constants
from app.constants import LogTypes as logconstants
from app.constants import ViewConstants
from app.decorators import with_error_context
from app.data.moderations import count_moderations_by_owner, find_moderations_by_guild
from app.services import block_links as block_links_service
from app.services import default_roles as default_roles_service
from app.services import stream_elements as stream_elements_service
from app.services import analytics, analytics_reports
from app.services.cache import increment_redis_key, remove_all_cache_by_guild
from app.services.moderations import (
    insert_moderations_by_guild,
    pause_all_moderations_by_guild,
    update_moderations_by_guild,
)
from app.services.utils import cogs_manager, format_relative_time, get_available_roles_by_guild
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
        from app.forms.adapters.discord.entrypoints import RUNTIME

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
            f"🎮 Current Activity: {self.bot.activity.name}\n"
            f"🐶 Current Status: {self.bot.status.name}️\n"
            f"---------------------------------------------------"
        )
        logger.info(ready_message, log_type=logconstants.APPLICATION_STARTUP_TYPE)

    @commands.Cog.listener()
    @with_error_context("on_member_join")
    async def on_member_join(self, member: discord.Member):
        roles = get_available_roles_by_guild(member.guild)
        if roles:
            await default_roles_service.set_on_member_join(member)

        await send_welcome_message(member)

    @commands.Cog.listener()
    @with_error_context("on_message")
    async def on_message(self, message: discord.Message):
        if message.author.bot:
            return

        guild_id = str(message.guild.id)

        if message.content.startswith("ks!"):
            await stream_elements_service.check_message(guild_id, message, "ks!")

        await block_links_service.check_message(guild_id, message)

    @commands.Cog.listener()
    @with_error_context("on_raw_message_edit")
    async def on_raw_message_edit(self, payload: discord.RawMessageUpdateEvent):
        await block_links_service.check_edited_message(self.bot, payload)

    @commands.Cog.listener()
    @with_error_context("on_interaction")
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.application_command:
            return None

        if not interaction.command:
            return None

        # Context menus are ContextMenu, not Command, so they carry no `_attr`.
        feature = getattr(interaction.command, "_attr", None)

        if feature and str(interaction.user.id) != str(self.bot.owner_id):
            increment_redis_key(f"{logconstants.COMMAND_CALL_TYPE}:{feature}")

        analytics.emit(
            "command.invoked",
            guild_id=interaction.guild_id,
            user_id=interaction.user.id,
            command=interaction.command.qualified_name,
            source=analytics.resolve_source(interaction),
            feature=feature,
            is_admin=bool(
                getattr(interaction.user, "guild_permissions", None)
                and interaction.user.guild_permissions.administrator
            ),
        )

    @commands.Cog.listener()
    @with_error_context("on_guild_join")
    async def on_guild_join(self, guild: discord.Guild):
        owner_id = str(guild.owner.id)
        exist = find_moderations_by_guild(guild.id)

        if not exist:
            insert_moderations_by_guild(guild.id, owner_id=owner_id)
        else:
            update_moderations_by_guild(guild.id, constants.IS_BOT_ONLINE, True)
            update_moderations_by_guild(guild.id, "owner_id", owner_id)

        total_servers = count_moderations_by_owner(owner_id)
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

        return await GreetingsView().send(guild)

    @commands.Cog.listener()
    @with_error_context("on_guild_remove")
    async def on_guild_remove(self, guild: discord.Guild):
        moderations = find_moderations_by_guild(guild.id)

        active_commands = []
        duration_info = ""
        if moderations:
            active_commands = [
                key for key in commandsconstants.COMMANDS_LIST
                if moderations.get(key, False)
            ]
            created_at = moderations.get("created_at")
            if created_at:
                duration_info = f"\nLeft after {format_relative_time(created_at).replace(' ago', '')} (added_at: {created_at.strftime('%Y-%m-%d %H:%M:%S')})"

        commands_info = f"\nActive commands: {', '.join(active_commands)}" if active_commands else "\nActive commands: none"

        logger.info(
            f"Left guild by {guild.owner.id}{commands_info}{duration_info}",
            guild_id=guild.id,
            owner_id=guild.owner.id,
            log_type=logconstants.EVENT_LEFT_GUILD_TYPE,
        )

        analytics.emit(
            "guild.removed",
            guild_id=guild.id,
            **analytics_reports.guild_snapshot(str(guild.id)),
        )
        analytics.flush()

        remove_all_cache_by_guild(guild.id)
        return pause_all_moderations_by_guild(guild.id, str(self.bot.user.id))


async def setup(bot: DiscordBot) -> None:
    await bot.add_cog(Events(bot))
