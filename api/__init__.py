import os

from config import config_by_name
from flask import Flask


def create_app(config_name: str):
    app = Flask(__name__)
    app.config.from_object(
        config_by_name[config_name]
    )

    from discord import discord as discord_blueprint
    app.register_blueprint(discord_blueprint, url_prefix='/discord')

    from webhooks import webhooks as webhooks_blueprint
    app.register_blueprint(webhooks_blueprint, url_prefix='/webhooks')

    from core import api as api_blueprint
    app.register_blueprint(api_blueprint, url_prefix='/api')

    return app


if __name__ == "__main__":
    app = create_app(os.getenv("APPLICATION_ENVIRONMENT").lower() or "dev")

    app.run(port=os.getenv("APPLICATION_PORT") or 5000)
