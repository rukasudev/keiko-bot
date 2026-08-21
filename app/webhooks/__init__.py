from threading import Thread

from flask import Blueprint, g, request

from app.api import create_api, run_api
from app.config import AppConfig
from app.services.trace import trace_scope

webhooks = Blueprint('webhooks', __name__)

from . import reminder, twitch, youtube


@webhooks.before_request
def open_webhook_trace():
    """One webhook request is one unit of work, and one log message."""
    scope = trace_scope(request.path.rsplit("/", 1)[-1], source="webhook")
    scope.__enter__()
    g.webhook_trace_scope = scope


@webhooks.teardown_request
def close_webhook_trace(error):
    scope = g.pop("webhook_trace_scope", None)
    if scope:
        scope.__exit__(type(error) if error else None, error, None)


def handle_webhook_api(config: AppConfig) -> None:
    if not config.run_local_webhook_api():
        return

    webhook_api = create_api(config.ENVIRONMENT)
    run_api_lambda = lambda: run_api(webhook_api, 5000)

    api_thread = Thread(target=run_api_lambda)
    return api_thread.start()


@webhooks.route('/healthcheck')
def healthcheck():
    return 'Webhooks are up and running!', 200
