def check_integration_enabled(func):
    def wrapper(self, *args, **kwargs):
        if not self.enabled:
            return None
        return func(self, *args, **kwargs)
    return wrapper
