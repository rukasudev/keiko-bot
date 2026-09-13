from typing import Dict, Final, FrozenSet, List

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
    BLOCK_LINKS_MODE_KEY: Final[str] = "mode"
    BLOCK_LINKS_CUSTOM_LINKS_KEY: Final[str] = "custom_links"
    BLOCK_LINKS_LINK_KEY: Final[str] = "link"
    BLOCK_LINKS_MATCH_TYPE_KEY: Final[str] = "match_type"
    BLOCK_LINKS_ADD_CUSTOM_KEY: Final[str] = "add_custom"

    BLOCK_LINKS_DIAGNOSTIC_MAX_LINKS: Final[int] = 5
    BLOCK_LINKS_EVENTS_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 90
    BLOCK_LINKS_EVENTS_MAX_PER_MESSAGE: Final[int] = 3
    BLOCK_LINKS_EVENTS_READ_LIMIT: Final[int] = 200
    REDIS_BLOCK_LINKS_COUNTER_TOTAL: Final[str] = "guild:{guild_id}:block_links:total"
    REDIS_BLOCK_LINKS_COUNTER_HOST: Final[str] = "guild:{guild_id}:block_links:host:{value}"
    REDIS_BLOCK_LINKS_COUNTER_USER: Final[str] = "guild:{guild_id}:block_links:user:{value}"

    # analytics
    ANALYTICS_EVENTS_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 90
    ANALYTICS_MONTHLY_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 400
    ANALYTICS_PROFILE_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 365
    ANALYTICS_QUEUE_MAXSIZE: Final[int] = 5000
    ANALYTICS_FLUSH_SECONDS: Final[float] = 5.0
    ANALYTICS_FLUSH_BATCH: Final[int] = 200
    ANALYTICS_FLUSH_MAX_BATCHES: Final[int] = 25
    ANALYTICS_MAX_PROPS: Final[int] = 12
    ANALYTICS_MAX_VALUE_LENGTH: Final[int] = 64
    ANALYTICS_HOT_WINDOW_SECONDS: Final[int] = 60 * 60 * 24 * 8
    ANALYTICS_ATTEMPTS_WINDOW_SECONDS: Final[int] = 60 * 60 * 24
    ANALYTICS_TIMELINE_READ_LIMIT: Final[int] = 25
    ANALYTICS_DIGEST_HOUR: Final[int] = 9
    ANALYTICS_DIGEST_WEEKDAY: Final[int] = 0
    ANALYTICS_DIGEST_WINDOW_DAYS: Final[int] = 7
    ANALYTICS_JOURNEY_HISTORY_LIMIT: Final[int] = 4
    ANALYTICS_JOURNEY_DEBOUNCE_SECONDS: Final[float] = 1.0
    REDIS_ANALYTICS_DAILY: Final[str] = "guild:{guild_id}:analytics:{date}:{metric}"
    REDIS_ANALYTICS_ATTEMPTS: Final[str] = "guild:{guild_id}:analytics:attempts:{feature}"

    # debug logs — the hot window of "why did it break?", queried directly in
    # Mongo. Anything older lives in the daily file on the Discord logs channel.
    DEBUG_LOGS_TTL_SECONDS: Final[int] = 60 * 60 * 24 * 30
    DEBUG_LOGS_QUEUE_MAXSIZE: Final[int] = 5000
    DEBUG_LOGS_FLUSH_BATCH: Final[int] = 200
    DEBUG_LOGS_FLUSH_MAX_BATCHES: Final[int] = 25
    DEBUG_LOGS_MESSAGE_MAX_LENGTH: Final[int] = 8000
    DEBUG_LOGS_TRACEBACK_MAX_LENGTH: Final[int] = 16000
    DEBUG_LOGS_EXPORT_HOUR: Final[int] = 0
    DEBUG_LOGS_EXPORT_MINUTE: Final[int] = 30

    # Reminders that failed to be created are retried on a slow loop: the
    # reason one fails is rarely fixed within a minute, and the birthday it
    # belongs to is usually months away.
    BIRTHDAY_RECONCILE_MINUTES: Final[int] = 30
    BIRTHDAY_RECONCILE_BATCH: Final[int] = 50

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

    REMINDERS_BIRTHDAY_KEY: Final[str] = "reminders_birthday"
    REMINDER_TYPE_BIRTHDAY: Final[str] = "reminders_birthday"
    REMINDER_API_TITLE_BIRTHDAY: Final[str] = "birthday_reminder"

    LIFECYCLE_EDIT: Final[str] = "edit"
    LIFECYCLE_PAUSE: Final[str] = "pause"
    LIFECYCLE_UNPAUSE: Final[str] = "unpause"
    LIFECYCLE_DISABLE: Final[str] = "disable"
    LIFECYCLE_ADD_ITEM: Final[str] = "add_item"
    LIFECYCLE_REMOVE_ITEM: Final[str] = "remove_item"

    BIRTHDAY_CONFIG_CHANNEL: Final[str] = "channel"
    BIRTHDAY_CONFIG_MENTION_EVERYONE: Final[str] = "mention_everyone"
    BIRTHDAY_CONFIG_TIMEZONE: Final[str] = "timezone"
    BIRTHDAY_CONFIG_NOTIFICATION_TIME: Final[str] = "notification_time"
    BIRTHDAY_CONFIG_DEFAULT_MESSAGE_MODE: Final[str] = "default_message_mode"
    BIRTHDAY_CONFIG_DEFAULT_MESSAGE_TITLE: Final[str] = "default_message_title"
    BIRTHDAY_CONFIG_DEFAULT_MESSAGE_CONTENT: Final[str] = "default_message_content"
    BIRTHDAY_CONFIG_REGISTER_NOW: Final[str] = "register_now"

    SELF_BIRTHDAY_EDIT_LIMIT: Final[int] = 1

    COMMANDS_LIST: Final[List[str]] = [
        BLOCK_LINKS_KEY,
        DEFAULT_ROLES_KEY,
        NOTIFICATIONS_TWITCH_KEY,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        REMINDERS_BIRTHDAY_KEY,
        WELCOME_MESSAGES_KEY,
    ]

    COMMAND_KEY_TO_COMPOSITION_KEY: Final[Dict[str, str]] = {
        NOTIFICATIONS_TWITCH_KEY: NOTIFICATIONS_KEY,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY: NOTIFICATIONS_KEY,
        REMINDERS_BIRTHDAY_KEY: REMINDERS_BIRTHDAY_KEY,
        BLOCK_LINKS_KEY: BLOCK_LINKS_CUSTOM_LINKS_KEY,
    }

    COMMAND_SERVICES: Final[Dict[str, str]] = {
        WELCOME_MESSAGES_KEY: "app.services.welcome_messages",
        DEFAULT_ROLES_KEY: "app.services.default_roles",
        BLOCK_LINKS_KEY: "app.services.block_links",
        NOTIFICATIONS_TWITCH_KEY: "app.services.notifications_twitch",
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY: "app.services.notifications_youtube_video",
        REMINDERS_BIRTHDAY_KEY: "app.services.reminders_birthdays",
        INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY: "app.services.stream_elements",
    }

    COMPOSITION_COMMANDS_LIST: Final[List[str]] = [
        NOTIFICATIONS_TWITCH_KEY,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        REMINDERS_BIRTHDAY_KEY,
        BLOCK_LINKS_KEY,
    ]

    FORM_ENGINE_V2_KEYS: Final[FrozenSet[str]] = frozenset({
        DEFAULT_ROLES_KEY,
        INTEGRATIONS_STREAM_ELEMENTS_COMMANDS_KEY,
        WELCOME_MESSAGES_KEY,
        BLOCK_LINKS_KEY,
        NOTIFICATIONS_TWITCH_KEY,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY,
        REMINDERS_BIRTHDAY_KEY,
    })

    COMPOSITION_MAX_LENGTH: Final[Dict[str, int]] = {
        NOTIFICATIONS_TWITCH_KEY: 3,
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY: 2,
        REMINDERS_BIRTHDAY_KEY: 25,
        BLOCK_LINKS_KEY: 25,
    }

    SETUP_FEATURES: Final[List[Dict[str, str]]] = [
        {"command_key": WELCOME_MESSAGES_KEY, "button_key": "welcome-messages", "emoji": "🎉"},
        {"command_key": DEFAULT_ROLES_KEY, "button_key": "default-roles", "emoji": "👩‍🎓"},
        {"command_key": BLOCK_LINKS_KEY, "button_key": "block-links", "emoji": "🚫"},
        {"command_key": NOTIFICATIONS_TWITCH_KEY, "button_key": "twitch", "emoji": "📡"},
        {"command_key": NOTIFICATIONS_YOUTUBE_VIDEO_KEY, "button_key": "youtube", "emoji": "▶️"},
        {"command_key": REMINDERS_BIRTHDAY_KEY, "button_key": "birthdays", "emoji": "🎂"},
    ]

    FEATURE_COMMANDS: Final[Dict[str, Dict[str, str]]] = {
        WELCOME_MESSAGES_KEY: {"group": "moderations", "namespace": "welcome-messages"},
        DEFAULT_ROLES_KEY: {"group": "moderations", "namespace": "default-roles"},
        BLOCK_LINKS_KEY: {"group": "moderations", "namespace": "block-links"},
        NOTIFICATIONS_TWITCH_KEY: {"group": "notifications", "namespace": "notifications-twitch"},
        NOTIFICATIONS_YOUTUBE_VIDEO_KEY: {"group": "notifications", "namespace": "notifications-youtube"},
        REMINDERS_BIRTHDAY_KEY: {"group": "moderations", "namespace": "moderations-birthdays"},
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
        Commands.REMINDERS_BIRTHDAY_KEY: False,
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
    MULTI_SELECT_ACTION_KEY: Final[str] = "multi_select"
    DESIGN_SELECT_ACTION_KEY: Final[str] = "design_select"
    FILE_UPLOAD_ACTION_KEY: Final[str] = "file_upload"
    USER_SELECT_ACTION_KEY: Final[str] = "user_select"
    MONTH_SELECT_ACTION_KEY: Final[str] = "month_select"
    SUMMARY_CARD_ACTION_KEY: Final[str] = "summary_card"
    CONFIGURATION_CARD_ACTION_KEY: Final[str] = "configuration_card"

    NO_ACTION_LIST: Final[List[str]] = [
        FORM_ACTION_KEY,
        BUTTON_ACTION_KEY,
        RESUME_ACTION_KEY,
        SUMMARY_CARD_ACTION_KEY,
    ]


class ViewConstants:
    """Timing and sizing standards shared by every interactive view and modal."""
    LONG_TIMEOUT_SECONDS: Final[int] = 1800
    SHORT_TIMEOUT_SECONDS: Final[int] = 300
    ACTION_COOLDOWN_SECONDS: Final[int] = 10
    ACTION_NOTICE_SECONDS: Final[int] = 5
    COMPOSITION_PREVIEW_LIMIT: Final[int] = 10


class WelcomeDesign:
    CUSTOM_BLUR_PREVIEW: Final[str] = "https://i.ibb.co/yBnKHC5p/04af360692ff269044c6de5a30bd45e4.gif"
    CUSTOM_ONLY_PREVIEW: Final[str] = "https://i.ibb.co/hxBscZDB/REC-20260213104214-ezgif-com-video-to-gif-converter.gif"
    DEFAULT_ICON: Final[str] = "https://i.sstatic.net/41v2I.png"

    PREVIEW_DATA_KEYS: Final[List[str]] = [
        "welcome_messages_title",
        "welcome_messages",
        "welcome_messages_footer",
        "welcome_design",
        "welcome_custom_image",
    ]


class DiscordLimits:
    """Hard API limits. Exceeding one makes Discord reject the whole message,
    which only shows up at runtime, so the renderers guard against them."""
    EMBED_TOTAL: Final[int] = 6000
    EMBED_DESCRIPTION: Final[int] = 4096
    EMBED_FIELDS: Final[int] = 25
    EMBED_FIELD_NAME: Final[int] = 256
    EMBED_FIELD_VALUE: Final[int] = 1024
    SELECT_OPTION_TEXT: Final[int] = 100


class TraceTitles:
    """What the admin log channel calls each kind of event.

    The emoji names the event, so two runs of the same kind never look
    different: every plain command run is `▶️`, every webhook is `🔔`.

    Only the *labels* live here. The emoji for an outcome comes from
    `journey.OUTCOME_ICONS`, which already owned that vocabulary — keeping a
    second copy is how `edited` ended up as 📝 in one place and 🔧 in another.

    Deliberately separate from the user-facing `commands.command-events.*` copy:
    that is shipped in two locales and read by server admins, this is read by
    whoever is on call, and the two are free to diverge.
    """

    OUTCOME_LABELS: Final[Dict[str, str]] = {
        "added": "Item Added",
        "removed": "Item Removed",
        "edited": "Config Edited",
        "enabled": "Command Enabled",
        "disabled": "Command Disabled",
        "paused": "Command Paused",
        "resumed": "Command Resumed",
        "saved": "Setup Saved",
        "discarded": "Setup Discarded",
        "abandoned": "Setup Abandoned",
        "in progress": "Setup In Progress",
        "failure": "Command Error",
    }

    SOURCE_TITLES: Final[Dict[str, str]] = {
        "webhook": "🔔 Webhook Event",
        "job": "⏰ Scheduled Job",
        "internal": "👂 Listener Event",
    }

    # Results that say nothing about what was done, so the last action gets to
    # name the message instead.
    GENERIC_RESULTS: Final[frozenset] = frozenset({"in progress", "success"})

    DEFAULT: Final[str] = "▶️ Command Run"


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
    BIRTHDAY_GIF: Final[str] = IMAGE_03

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

    BOT_ACTION_TYPE: Final[str] = "bot.action"
    BOT_ACTION_TITLE: Final[str] = "🤖 Bot Action"

    COMMAND_WARN_TYPE: Final[str] = "command.warn"
    COMMAND_WARN_TITLE: Final[str] = "⚠️ Command Warning"

    COMMAND_ERROR_TYPE: Final[str] = "command.error"
    COMMAND_ERROR_TITLE: Final[str] = "❌ Command Error"

    TRACE_TYPE: Final[str] = "trace"
    TRACE_TITLE: Final[str] = "🧵 Trace"
    JOURNEY_TITLE: Final[str] = "🧭"

    # Log types that exist to be read. A listener runs under a trace that stays
    # silent unless something failed — the rule that keeps `on_message` from
    # flooding the channel — and that silence swallowed the two lines the log
    # exists for: a server adding or removing Keiko. A line of one of these
    # types is never routine, so it publishes the trace that carries it.
    REPORTED_EVENT_TYPES: Final[frozenset] = frozenset({
        EVENT_JOIN_GUILD_TYPE,
        EVENT_LEFT_GUILD_TYPE,
    })

    TRACE_MAX_LINES: Final[int] = 20
    TRACE_LINE_MAX_LENGTH: Final[int] = 180
    TRACE_RESULT_SUCCESS: Final[str] = "success"
    TRACE_RESULT_FAILURE: Final[str] = "failure"
    TRACE_RESULT_ABANDONED: Final[str] = "abandoned"
    TRACE_RESULT_RUNNING: Final[str] = "running"

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
        BOT_ACTION_TYPE: (
            BOT_ACTION_TITLE,
            discord.Color.purple(),
        ),
        COMMAND_WARN_TYPE: (
            COMMAND_WARN_TITLE,
            discord.Color.gold(),
        ),
        COMMAND_ERROR_TYPE: (
            COMMAND_ERROR_TITLE,
            discord.Color.red(),
        ),
        TRACE_TYPE: (
            TRACE_TITLE,
            discord.Color.blurple(),
        ),
    }
    UNKNOWN_COMMAND: Final[str] = "unknown_command"


class Emojis:
    FRISBEE_EMOJI: Final[str] = ":flying_disc:"
    EDIT_EMOJI: Final[str] = ":pencil:"

# Quick-pick domains offered in the block_links card, with the extra hosts
# each one also covers. Keys must stay in sync with the card options in
# app/languages/form/block_links.yml (pinned by the YAML contract suite).
BLOCK_LINKS_QUICK_PICK_DOMAINS: Final[Dict[str, List[str]]] = {
    "facebook.com": [],
    "instagram.com": [],
    "twitter.com": ["x.com"],
    "twitch.tv": [],
    "youtube.com": ["youtu.be"],
    "discord.gg": ["discord.com"],
    "spotify.com": ["open.spotify.com"],
    "tiktok.com": [],
    "reddit.com": [],
}

# Legacy saved configs stored the option LABELS ("Youtube"); translated to
# domains at read time by normalize_block_links_config.
BLOCK_LINKS_LEGACY_LABEL_TO_DOMAIN: Final[Dict[str, str]] = {
    "facebook": "facebook.com",
    "instagram": "instagram.com",
    "twitter": "twitter.com",
    "twitch": "twitch.tv",
    "youtube": "youtube.com",
    "discord": "discord.gg",
    "spotify": "spotify.com",
    "tiktok": "tiktok.com",
    "reddit": "reddit.com",
}

supported_locales = [
    discord.Locale.american_english.value,
    discord.Locale.brazil_portuguese.value,
]
