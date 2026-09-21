"""Work a webhook starts and does not wait for.

Its own module rather than `app/webhooks/__init__.py`, because the routes import
this name and the package imports the routes: living in the package body meant
the definition had to sit above that import, with nothing but a comment holding
the order in place.
"""
from typing import Any

from app.services.trace import current_trace, run_traced


def schedule_webhook_job(coroutine: Any, name: str):
    """Hand work that outlives the request to the bot loop, continuing its message.

    Two things are wrong with awaiting it here, and both have bitten:

    - the webhook runs on the Flask thread, where `create_task` is not safe;
    - the request's trace closes the moment Flask returns, so a job that keeps
      logging afterwards would write into a message that is already gone.

    The first job takes the request's trace over before Flask returns, so one
    event is one message; a second job in the same request gets its own. When
    the job cannot be scheduled, the request keeps its message.
    """
    import asyncio

    from app import bot

    request = current_trace()
    handed = request.handover() if request is not None and not request.superseded else None
    job = run_traced(coroutine, name, trace=handed, source="job")
    try:
        return asyncio.run_coroutine_threadsafe(job, bot.loop)
    except Exception:
        job.close()
        coroutine.close()
        if handed is not None:
            request.superseded = False
        raise
