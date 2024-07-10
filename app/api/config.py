import os
from os.path import dirname, join

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = join(dirname(__file__), "..", ".env")
load_dotenv(dotenv_path, override=True)

class Config:
    TWITCH_SECRET_KEY = os.getenv("TWITCH_SECRET_KEY")


class DevelopmentConfig:
    DEBUG = True


class ProductionConfig:
    DEBUG = False


config_by_name = dict(dev=DevelopmentConfig, prod=ProductionConfig)
