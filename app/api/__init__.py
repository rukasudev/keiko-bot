import sys

from flask import Flask, current_app, redirect

from app import logger
from app.api.config import get_config


def create_api(config_name: str):
    app = Flask(__name__)
    app.config.from_object(
        get_config(config_name)
    )

    app.add_url_rule("/v1/health", "health", health)
    app.add_url_rule("/v1/invite", "invite", invite)
    app.add_url_rule("/v1/support", "support", support)

    from app.webhooks import webhooks as webhooks_blueprint
    app.register_blueprint(webhooks_blueprint, url_prefix='/v1/webhooks')

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
