import functools
from typing import Callable

from app import logger
from app.constants import LogTypes as logconstants
from app.exceptions import ErrorContext


def with_error_context(flow: str) -> Callable:
    """
    Decorator que captura erros com contexto enriquecido.

    Extrai automaticamente guild_id, user_id e channel_id dos argumentos
    quando disponíveis (Member, Message, Interaction).
    """

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            context = ErrorContext(flow=flow)

            for arg in args:
                if hasattr(arg, "guild") and arg.guild:
                    context.guild_id = str(arg.guild.id)

                if hasattr(arg, "id"):
                    if hasattr(arg, "display_avatar"):
                        context.user_id = str(arg.id)
                        context.extra["user_name"] = getattr(arg, "name", None)
                    elif hasattr(arg, "author"):
                        context.user_id = str(arg.author.id)
                        context.extra["user_name"] = arg.author.name
                        if hasattr(arg, "content") and arg.content:
                            context.extra["message_preview"] = arg.content[:200]

                if hasattr(arg, "channel") and arg.channel:
                    context.channel_id = str(arg.channel.id)

            try:
                return await func(*args, **kwargs)
            except Exception as e:
                logger.error(
                    f"Error in {flow}: {type(e).__name__}: {e}",
                    log_type=logconstants.COMMAND_ERROR_TYPE,
                    context=context,
                    exc_info=True,
                )
                raise

        return wrapper

    return decorator
