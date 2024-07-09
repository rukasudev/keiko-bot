from flask import Blueprint

webhooks = Blueprint('webhooks', __name__)

from . import twitch
