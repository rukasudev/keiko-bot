from flask import Blueprint

discord = Blueprint('discord', __name__)

from . import commands
