import os
from os.path import dirname, join

import boto3
from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
dotenv_path = join(dirname(__file__), "..", ".env")
load_dotenv(dotenv_path, override=True)

ssm = boto3.client("ssm", region_name="sa-east-1")


class DevelopmentConfig:
    DEBUG = True
    INVITE_URL = os.getenv("INVITE_URL")


class ProductionConfig:
    DEBUG = False
    INVITE_URL = ssm.get_parameter(Name="/keiko/api/invite_url")["Parameter"][
        "Value"
    ]


config_by_name = dict(dev=DevelopmentConfig, prod=ProductionConfig)
