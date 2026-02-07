from typing import Dict, Final, List

import discord


class Style:
    RED_COLOR: Final[str] = "ff0000"
    BACKGROUND_COLOR: Final[str] = "4F97F9"


class DBConfigs:
    KEIKO_ACTIVITY: Final[str] = "activity"
    KEIKO_STATUS: Final[str] = "status"
    KEIKO_DESCRIPTION: Final[str] = "description"
    KEIKO_OWNER_ID: Final[str] = "owner_id"
    KEIKO_PREFIX: Final[str] = "prefix"

    INTEGRATION_NOTION: Final[str] = "notion"
    INTEGRATION_NOTION_ENABLED: Final[bool] = "enabled"
    INTEGRATION_NOTION_TOKEN: Final[str] = "token"
    INTEGRATION_NOTION_DATABASE_ID: Final[str] = "database_id"

    INTEGRATION_OPENAI: Final[str] = "openai"
    INTEGRATION_OPENAI_ENABLED: Final[bool] = "enabled"
    INTEGRATION_OPENAI_API_KEY: Final[str] = "openai_api_key"

    ADMIN_GUILD_ID: Final[str] = "admin_guild_id"
    ADMIN_REPORTS_CHANNEL_ID: Final[str] = "admin_reports_channel_id"
    ADMIN_LOGS_CHANNEL_ID: Final[str] = "admin_logs_channel_id"
    ADMIN_LOGS_COMMAND_CALL_ID: Final[str] = "admin_logs_command_call_id"
    ADMIN_LOGS_ERROR_CHANNEL_ID: Final[str] = "admin_logs_error_channel_id"
    ADMIN_LOGS_FILES_CHANNEL_ID: Final[str] = "admin_logs_files_channel_id"
    ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID: Final[str] = "admin_logs_bot_actions_channel_id"
    ADMIN_DUMP_CHANNEL_ID: Final[str] = "admin_dump_channel_id"

    ADMIN_CONFIGS_LIST: Final[List] = [
        ADMIN_GUILD_ID,
        ADMIN_LOGS_CHANNEL_ID,
        ADMIN_LOGS_ERROR_CHANNEL_ID,
        ADMIN_LOGS_FILES_CHANNEL_ID,
        ADMIN_LOGS_BOT_ACTIONS_CHANNEL_ID,
        ADMIN_DUMP_CHANNEL_ID
    ]


class Commands:
    ENABLED_KEY: Final[str] = "enabled"
    EDITED_KEY: Final[str] = "edited"
    PAUSED_KEY: Final[str] = "paused"
    UNPAUSED_KEY: Final[str] = "unpaused"
    DISABLED_KEY: Final[str] = "disabled"
    ADDED_KEY: Final[str] = "added"
    REMOVED_KEY: Final[str] = "removed"

    # block links
    BLOCK_LINKS_KEY: Final[str] = "block_links"
    BLOCK_LINKS_ALLOWED_CHATS_KEY: Final[str] = "allowed_chats"
    BLOCK_LINKS_ALLOWED_ROLES_KEY: Final[str] = "allowed_roles"
    BLOCK_LINKS_ALLOWED_LINKS_KEY: Final[str] = "allowed_links"
    BLOCK_LINKS_ANSWER_KEY: Final[str] = "answer"

    # moderations
    MODERATIONS_KEY: Final[str] = "moderations"
    WELCOME_MESSAGES_KEY: Final[str] = "welcome_messages"
    DEFAULT_ROLES_KEY: Final[str] = "default_roles"
    DEFAULT_ROLES_BOT_KEY: Final[str] = "default_roles_bot"

    # notifications
    NOTIFICATIONS_KEY: Final[str] = "notifications"
    NOTIFICATIONS_TWITCH_KEY: Final[str] = "notifications_twitch"

    NOTIFICATIONS_TWITCH_STREAM_STATUS_ONLINE: Final[str] = "online"
    NOTIFICATIONS_TWITCH_STREAM_STATUS_OFFLINE: Final[str] = "offline"

    NOTIFICATIONS_YOUTUBE_VIDEO_KEY: Final[str] = "notifications_youtube_video"

    INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY: Final[str] = "stream_elements_commands"

    COMMANDS_LIST: Final[List[str]] = [
        BLOCK_LINKS_KEY,
        DEFAULT_ROLES_KEY,
        NOTIFICATIONS_TWITCH_KEY,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        WELCOME_MESSAGES_KEY,
    ]

    COMMAND_KEY_TO_COMPOSITION_KEY: Final[Dict[str, str]] = {
        NOTIFICATIONS_TWITCH_KEY: NOTIFICATIONS_KEY,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY: NOTIFICATIONS_KEY,
    }

    COMPOSITION_COMMANDS_LIST: Final[List[str]] = [
        NOTIFICATIONS_TWITCH_KEY,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
    ]

    COMPOSITION_MAX_LENGTH: Final[Dict[str, int]] = {
        NOTIFICATIONS_TWITCH_KEY: 3,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY: 2,
    }


class CogsConstants:
    ADMIN_COGS: Final[str] = "admin"
    MODERATIONS_COGS: Final[str] = "moderations"
    NOTIFICATIONS_COGS: Final[str] = "notifications"
    CONFIG_COGS: Final[str] = "config"
    PROMETHEUS_COGS: Final[str] = "prometheus"
    EVENTS_COGS: Final[str] = "events"
    ERRORS_COGS: Final[str] = "errors"
    HELP_COGS: Final[str] = "help"

    COGS_LIST: Final[List[str]] = [
        ADMIN_COGS,
        MODERATIONS_COGS,
        PROMETHEUS_COGS,
        CONFIG_COGS,
        EVENTS_COGS,
        ERRORS_COGS,
        HELP_COGS,
    ]
    LAZY_LOAD_COGS: Final[List[str]] = [ADMIN_COGS, CONFIG_COGS]

    INTERACTION_COGS: Final[List[str]] = [
        NOTIFICATIONS_COGS,
        MODERATIONS_COGS,
        HELP_COGS,
    ]


class GuildConstants:
    IS_BOT_ONLINE: Final[str] = "is_bot_online"
    COGS_MODERATIONS_COMMANDS_DEFAULT: Final[Dict] = {
        IS_BOT_ONLINE: True,
        Commands.NOTIFICATIONS_TWITCH_KEY: False,
        Commands.NOTIFICATIONS_YOUTUBE_VIDEO_KEY: False,
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
    BUTTON_ACTION_KEY: Final[str] = "button"
    FORM_ACTION_KEY: Final[str] = "form"
    COMPOSITION_ACTION_KEY: Final[str] = "composition"

    NO_ACTION_LIST: Final[List[str]] = [FORM_ACTION_KEY, BUTTON_ACTION_KEY, RESUME_ACTION_KEY]


class KeikoIcons:
    IMAGE_01: Final[str] = (
        "https://cdn.discordapp.com/attachments/927208560360820766/1246698994160242739/KEIKO_DEFAULT.png?ex=665d566a&is=665c04ea&hm=5b3fe0e80b2a83e0e88b5bded1db17a6b98b4661e175b460b3e6e4a27d59711e&"
    )
    IMAGE_02: Final[str] = (
        "https://cdn.discordapp.com/attachments/927208560360820766/1246698993086627911/KEIKO_NERD.png?ex=665d566a&is=665c04ea&hm=bcfc30b8f00134ece0572b601edf2eaf3a2c556a270a554f716e242aa0cbfbb6&"
    )
    IMAGE_03: Final[str] = (
        "https://cdn.discordapp.com/attachments/927208560360820766/1246698992000307280/KEIKO_BOLO.png?ex=665d566a&is=665c04ea&hm=387e5a325c03d564fb8594db3dcc492980cb8357c345e9b1e1756df9184d0dde&"
    )
    ICONS_LIST: List[str] = [IMAGE_01, IMAGE_02, IMAGE_03]
    ACTION_IMAGE: Dict[str, str] = {
        FormConstants.RESUME_ACTION_KEY: IMAGE_03,
        FormConstants.BUTTON_ACTION_KEY: IMAGE_02,
    }


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
    UNKNOWN_COMMAND: Final[str] = "unknown_command"


class Emojis:
    FRISBEE_EMOJI: Final[str] = ":flying_disc:"
    EDIT_EMOJI: Final[str] = ":pencil:"

default_allowed_links: Final[Dict[str, str]] = {
    "facebook": "https://facebook.com",
    "instagram": "https://instagram.com",
    "twitter": "https://twitter.com",
    "twitch": "https://twitch.tv",
    "youtube": "https://youtube.com",
    "discord": "https://discord.gg",
    "spotify": "https://open.spotify.com",
    "tiktok": "https://tiktok.com",
    "reddit": "https://reddit.com",
}

supported_locales = [
    discord.Locale.american_english.value,
    discord.Locale.brazil_portuguese.value,
]
