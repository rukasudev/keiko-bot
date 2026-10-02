from http import HTTPStatus
from threading import Thread

from flask import Blueprint, g, request

from app import logger
from app.api import create_api, run_api
from app.config import AppConfig
from app.constants import LogTypes as logconstants
from app.services import metrics
from app.services.trace import trace_scope

webhooks = Blueprint('webhooks', __name__)


def webhook_trace(name: str) -> trace_scope:
    """The trace a webhook request runs under: posted to Discord only when it failed."""
    return trace_scope(name, source="webhook", silent_when_clean=True)


@webhooks.before_request
def open_webhook_trace():
    """One webhook request is one unit of work; the healthcheck is not one."""
    if request.endpoint == "webhooks.healthcheck":
        return
    scope = webhook_trace(request.path.rsplit("/", 1)[-1])
    scope.__enter__()
    g.webhook_trace_scope = scope


@webhooks.after_request
def settle_by_status(response):
    """A 4xx is a refusal, stored and counted; a 503 asks the sender to try again
    later, which is not a failure; any other 5xx is a failure, which posts."""
    scope = g.get("webhook_trace_scope")
    status = response.status_code
    if scope is None or status < 400 or status == HTTPStatus.SERVICE_UNAVAILABLE:
        return response

    route = request.url_rule.rule
    if status >= 500:
        if not scope.trace.has_error:
            logger.error(
                f"{route} answered {status}", log_type=logconstants.COMMAND_ERROR_TYPE
            )
        return response

    scope.trace.quiet = True
    logger.warn(f"{route} refused with {status}", log_type=logconstants.COMMAND_WARN_TYPE)
    metrics.record_webhook_refusal(route, status)
    return response


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


from . import reminder, twitch, youtube

__all__ = ["webhooks", "webhook_trace", "reminder", "twitch", "youtube"]
