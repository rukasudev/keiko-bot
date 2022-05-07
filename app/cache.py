import redis


class Redis:
    def __init__(self, REDIS_URL):
        self.redis = redis.from_url(REDIS_URL)
