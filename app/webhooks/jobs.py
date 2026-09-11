"""Work a webhook starts and does not wait for.

Its own module rather than `app/webhooks/__init__.py`, because the routes import
this name and the package imports the routes: living in the package body meant
the definition had to sit above that import, with nothing but a comment holding
the order in place.
"""
from typing import Any

from app.services.trace import run_traced


def schedule_webhook_job(coroutine: Any, name: str):
    """Hand work that outlives the request to the bot loop, under its own trace.

    Two things are wrong with awaiting it here, and both have bitten:

    - the webhook runs on the Flask thread, where `create_task` is not safe;
    - the request's trace closes — and posts — the moment Flask returns, so a
      job that keeps logging afterwards writes into a message that is already
      gone. `run_traced` gives it a message of its own.
    """
    import asyncio

    from app import bot

    return asyncio.run_coroutine_threadsafe(
        run_traced(coroutine, name, source="job"), bot.loop
    )
