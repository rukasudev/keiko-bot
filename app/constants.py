from typing import Final


class Style:
    RED_COLOR: Final[str] = "ff0000"
    BACKGROUND_COLOR: Final[str] = "3aaaff"


class Commands:
    BLOCK_LINKS_KEY: Final[str] = "block_links"
    BLOCK_LINKS_ALLOWED_CHATS_KEY: Final[str] = "allowed_chats"
    BLOCK_LINKS_ALLOWED_ROLES_KEY: Final[str] = "allowed_roles"
    BLOCK_LINKS_ALLOWED_LINKS_KEY: Final[str] = "allowed_links"
    BLOCK_LINKS_ANSWER_KEY: Final[str] = "answer"

    MODERATIONS_KEY: Final[str] = "moderations"
    STREAM_MONITOR_KEY: Final[str] = "stream_monitor"
    WELCOME_MESSAGES_KEY: Final[str] = "welcome_messages"
    DEFAULT_ROLE_KEY: Final[str] = "default_role"


class CogsConstants:
    COGS_MODERATIONS_COMMANDS_DEFAULT: Final[dict] = {
        Commands.STREAM_MONITOR_KEY: False,
        Commands.WELCOME_MESSAGES_KEY: False,
        Commands.DEFAULT_ROLE_KEY: False,
        Commands.BLOCK_LINKS_KEY: False,
    }


class FormConstants:
    MODAL_ACTION_KEY: Final[str] = "modal"
    OPTIONS_ACTION_KEY: Final[str] = "options"
    ROLES_ACTION_KEY: Final[str] = "roles"
    CHANNELS_ACTION_KEY: Final[str] = "channels"
    RESUME_ACTION_KEY: Final[str] = "resume"


class Emojis:
    FRISBEE_EMOJI: Final[str] = ":flying_disc:"


GUILD_ID_KEY: Final[str] = "guild_id"