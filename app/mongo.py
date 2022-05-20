from pymongo import MongoClient
from data.moderations import ModerationData
import certifi


class MongoDB:
    def __init__(self, MONGO_URL: str):
        """Opens a connection to the database."""
        ca = certifi.where()
        self.connection = MongoClient(MONGO_URL, tlsCAFile=ca)
        self.moderations = ModerationData(self.connection)
