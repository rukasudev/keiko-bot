import sys

from flask import Flask

from app import logger
from app.api.config import config_by_name


def create_api(config_name: str):
    app = Flask(__name__)
    app.config.from_object(
        config_by_name[config_name]
    )

    from app.webhooks import webhooks as webhooks_blueprint
    app.register_blueprint(webhooks_blueprint, url_prefix='/webhooks')

    return app

def run_api(api: Flask, port: int = 5000):
    cli = sys.modules['flask.cli']
    cli.show_server_banner = lambda *x: None

    logger.info(f"Starting Webhook API on port {port}")
    api.run(host="0.0.0.0", port=port, debug=False)
