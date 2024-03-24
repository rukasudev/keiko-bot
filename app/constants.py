from typing import Dict, Final, List

import discord


class Style:
    RED_COLOR: Final[str] = "ff0000"
    BACKGROUND_COLOR: Final[str] = "3aaaff"


class DBConfigs:
    KEIKO_ACTIVITY: Final[str] = "activity"
    KEIKO_STATUS: Final[str] = "status"
    KEIKO_DESCRIPTION: Final[str] = "description"

    ADMIN_GUILD_ID: Final[str] = "admin_guild_id"
    ADMIN_LOGS_CHANNEL_ID: Final[str] = "admin_logs_channel_id"
    ADMIN_LOGS_ERROR_CHANNEL_ID: Final[str] = "admin_logs_error_channel_id"
    ADMIN_LOGS_FILES_CHANNEL_ID: Final[str] = "admin_logs_files_channel_id"

    ADMIN_CONFIGS_LIST: Final[List] = [
        ADMIN_GUILD_ID,
        ADMIN_LOGS_CHANNEL_ID,
        ADMIN_LOGS_ERROR_CHANNEL_ID,
        ADMIN_LOGS_FILES_CHANNEL_ID,
    ]


class Commands:
    # block links
    BLOCK_LINKS_KEY: Final[str] = "block_links"
    BLOCK_LINKS_ALLOWED_CHATS_KEY: Final[str] = "allowed_chats"
    BLOCK_LINKS_ALLOWED_ROLES_KEY: Final[str] = "allowed_roles"
    BLOCK_LINKS_ALLOWED_LINKS_KEY: Final[str] = "allowed_links"
    BLOCK_LINKS_ANSWER_KEY: Final[str] = "answer"

    # moderations
    MODERATIONS_KEY: Final[str] = "moderations"
    STREAM_MONITOR_KEY: Final[str] = "stream_monitor"
    WELCOME_MESSAGES_KEY: Final[str] = "welcome_messages"
    DEFAULT_ROLES_KEY: Final[str] = "default_roles"


class CogsConstants:
    ADMIN_COGS: Final[str] = "admin"
    MODERATIONS_COGS: Final[str] = "moderations"
    CONFIG_COGS: Final[str] = "config"

    LAZY_LOAD_COGS: Final[List] = [ADMIN_COGS, CONFIG_COGS]


class GuildConstants:
    IS_BOT_ONLINE: Final[str] = "is_bot_online"
    COGS_MODERATIONS_COMMANDS_DEFAULT: Final[Dict] = {
        IS_BOT_ONLINE: True,
        Commands.STREAM_MONITOR_KEY: False,
        Commands.WELCOME_MESSAGES_KEY: False,
        Commands.DEFAULT_ROLES_KEY: False,
        Commands.BLOCK_LINKS_KEY: False,
    }


class FormConstants:
    MODAL_ACTION_KEY: Final[str] = "modal"
    OPTIONS_ACTION_KEY: Final[str] = "options"
    ROLES_ACTION_KEY: Final[str] = "roles"
    AVAILABLE_ROLES_ACTION_KEY: Final[str] = "available_roles"
    CHANNELS_ACTION_KEY: Final[str] = "channels"
    RESUME_ACTION_KEY: Final[str] = "resume"


class LogTypes:
    APPLICATION_STARTUP_TYPE: Final[str] = "application.startup"
    APPLICATION_STARTUP_TITLE: Final[str] = "🚀 Application Startup"

    APPLICATION_ERROR_TYPE: Final[str] = "application.error"
    APPLICATION_ERROR_TITLE: Final[str] = "💀 Application Error"

    EVENT_JOIN_GUILD_TYPE: Final[str] = "event.join_guild"
    EVENT_JOIN_GUILD_TITLE: Final[str] = "➡️ Joined Guild"

    EVENT_LEFT_GUILD_TYPE: Final[str] = "event.left_guild"
    EVENT_LEFT_GUILD_TITLE: Final[str] = "🚪 Left Guild"

    COMMAND_CALL_TYPE: Final[str] = "command.call"
    COMMAND_CALL_TITLE: Final[str] = "▶️ Command Call"

    COMMAND_INFO_TYPE: Final[str] = "command.info"
    COMMAND_INFO_TITLE: Final[str] = "ℹ️ Command Info"

    COMMAND_WARN_TYPE: Final[str] = "command.warn"
    COMMAND_WARN_TITLE: Final[str] = "⚠️ Command Warning"

    COMMAND_ERROR_TYPE: Final[str] = "command.error"
    COMMAND_ERROR_TITLE: Final[str] = "❌ Command Error"

    LOG_TYPE_MAP: Final[Dict] = {
        APPLICATION_STARTUP_TYPE: (
            APPLICATION_STARTUP_TITLE,
            discord.Color.teal(),
        ),
        APPLICATION_ERROR_TYPE: (
            APPLICATION_ERROR_TITLE,
            discord.Color.from_rgb(0, 0, 0),
        ),
        EVENT_JOIN_GUILD_TYPE: (
            EVENT_JOIN_GUILD_TITLE,
            discord.Color.green(),
        ),
        EVENT_LEFT_GUILD_TYPE: (
            EVENT_LEFT_GUILD_TITLE,
            discord.Color.dark_gray(),
        ),
        COMMAND_CALL_TYPE: (
            COMMAND_CALL_TITLE,
            discord.Color.blue(),
        ),
        COMMAND_INFO_TYPE: (
            COMMAND_INFO_TITLE,
            discord.Color.light_grey(),
        ),
        COMMAND_WARN_TYPE: (
            COMMAND_WARN_TITLE,
            discord.Color.gold(),
        ),
        COMMAND_ERROR_TYPE: (
            COMMAND_ERROR_TITLE,
            discord.Color.red(),
        ),
    }


class Emojis:
    FRISBEE_EMOJI: Final[str] = ":flying_disc:"
    EDIT_EMOJI: Final[str] = ":pencil:"


GUILD_ID_KEY: Final[str] = "guild_id"
