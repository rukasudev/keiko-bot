"""Keiko's HTTP side: the Flask app and its config, the webhook routes mounted under
`/v1/webhooks`, and the bridge that hands a webhook's work to the bot loop."""

import os
import sys
from importlib import import_module
from http import HTTPStatus
from os.path import dirname, join
from pathlib import Path
from threading import Thread
from typing import Any

import boto3
from dotenv import load_dotenv
from flask import Blueprint, Flask, current_app, g, redirect, request

from app import logger
from app.config import AppConfig
from app.constants import Commands
from app.constants import LogTypes as logconstants
from app.core import metrics
from app.core.trace import current_trace, run_traced, trace_scope

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = join(dirname(__file__), "..", ".env")
load_dotenv(dotenv_path, override=True)

ssm = boto3.client("ssm", region_name="sa-east-1")


class DevelopmentConfig:
    MAX_CONTENT_LENGTH = Commands.WEBHOOK_MAX_BODY_BYTES

    def __init__(self):
        self.DEBUG = True
        self.INVITE_URL = os.getenv("INVITE_URL")
        self.SUPPORT_URL = os.getenv("SUPPORT_URL")


class ProductionConfig:
    MAX_CONTENT_LENGTH = Commands.WEBHOOK_MAX_BODY_BYTES

    def __init__(self):
        self.DEBUG = False
        self.INVITE_URL = ssm.get_parameter(Name="/keiko/api/invite_url")["Parameter"][
            "Value"
        ]
        self.SUPPORT_URL = ssm.get_parameter(Name="/keiko/api/support_url")["Parameter"][
            "Value"
        ]


def get_config(env_name: str):
    return DevelopmentConfig() if env_name == "dev" else ProductionConfig()


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


def schedule_webhook_job(coroutine: Any, name: str):
    """Hand work that outlives the request to the bot loop, continuing its message.

    Two things are wrong with awaiting it here, and both have bitten:

    - the webhook runs on the Flask thread, where `create_task` is not safe;
    - the request's trace closes the moment Flask returns, so a job that keeps
      logging afterwards would write into a message that is already gone.

    The first job takes the request's trace over before Flask returns, so one
    event is one message: what the request logs afterwards lands there too, and
    it is posted once both are done. A second job in the same request gets its
    own, posted under the same rule as the request. When the job cannot be
    scheduled, the request keeps its message.
    """
    import asyncio

    from app import bot

    request = current_trace()
    handed = request.handover() if request is not None and not request.superseded else None
    rules = request.publishing() if request is not None else {}
    job = run_traced(coroutine, name, trace=handed, source="job", **rules)
    try:
        return asyncio.run_coroutine_threadsafe(job, bot.loop)
    except Exception:
        job.close()
        coroutine.close()
        if handed is not None:
            request.take_back()
        raise


def create_api(config_name: str):
    app = Flask(__name__)
    app.config.from_object(
        get_config(config_name)
    )

    app.add_url_rule("/v1/health", "health", health)
    app.add_url_rule("/v1/invite", "invite", invite)
    app.add_url_rule("/v1/support", "support", support)

    app.register_blueprint(webhooks, url_prefix="/v1/webhooks")

    return app

def health():
    return "OK", 200

def invite():
    invite_url = current_app.config.get("INVITE_URL")
    return redirect(invite_url, code=302)

def support():
    support_url = current_app.config.get("SUPPORT_URL")
    return redirect(support_url, code=302)

def run_api(api: Flask, port: int = 5000):
    cli = sys.modules['flask.cli']
    cli.show_server_banner = lambda *x: None

    logger.info(f"Starting Webhook API on port {port}")
    api.run(host="0.0.0.0", port=port, debug=False)


def _mount_webhook_routes() -> None:
    package = Path(__file__).parent
    for route in sorted(package.glob("*/webhook.py")):
        import_module(f"app.integrations.{route.parent.name}.webhook")


_mount_webhook_routes()

__all__ = ["webhooks", "webhook_trace"]
