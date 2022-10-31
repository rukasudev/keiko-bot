from typing import Final

# styles
RED_COLOR: Final[str] = "#ff0000"
ICON_COLOR: Final[str] = "#3aaaff"

# commands keys
BLOCK_LINKS_COMMAND_KEY: Final[str] = "block_links"
MODERATIONS_COMMAND_KEY: Final[str] = "moderations"
STREAM_MONITOR_COMMAND_KEY: Final[str] = "stream_monitor"
WELCOME_MESSAGES_COMMAND_KEY: Final[str] = "welcome_messages"
DEFAULT_ROLE_COMMAND_KEY: Final[str] = "default_role"

# commands cogs key
BLOCK_LINKS_ALLOWED_CHATS_KEY: Final[str] = "allowed_chats"
BLOCK_LINKS_ALLOWED_LINKS_KEY: Final[str] = "allowed_links"
BLOCK_LINKS_ANSWER_KEY: Final[str] = "answer"

# TODO: improve this to use another languages in future
# command description
COMMAND_DESCRIPTION: Final[dict] = {"block_links": "Bloqueador de links"}

# cogs
COGS_MODERATION_DEFAULT: Final[dict] = {
    STREAM_MONITOR_COMMAND_KEY: False,
    WELCOME_MESSAGES_COMMAND_KEY: False,
    DEFAULT_ROLE_COMMAND_KEY: False,
    BLOCK_LINKS_COMMAND_KEY: False,
}

BLOCK_LINKS_COGS_DESCRIPTION: Final[dict] = {
    BLOCK_LINKS_ALLOWED_CHATS_KEY: "Chats permitidos",
    BLOCK_LINKS_ALLOWED_LINKS_KEY: "Links permitidos",
    BLOCK_LINKS_ANSWER_KEY: "Resposta ao bloquear links",
}

# forms
MODAL_FORM_VIEW_ACTION: Final[str] = "modal"
OPTIONS_FORM_VIEW_ACTION: Final[str] = "options"
CHANNELS_FORM_VIEW_ACTION: Final[str] = "channels"
RESUME_FORM_VIEW_ACTION: Final[str] = "resume"
