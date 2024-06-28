
from functools import wraps

from openai import AsyncOpenAI

from app.config import AppConfig
from app.integrations import check_integration_enabled
from app.types.integration import IntegrationBase


class OpenAIIntegration(IntegrationBase):
    def __init__(self, config: AppConfig):
        self.client = AsyncOpenAI(api_key=config.OPENAI_API_KEY)
        super().__init__(config.OPENAI_ENABLED)

    def cache_translation(func):
        @wraps(func)
        async def wrapper(self, locale: str, message: str) -> str:
            from app.services.cache import get_data_from_redis
            cached_locale = get_data_from_redis(message)
            if cached_locale:
                return cached_locale
            return await func(self, locale, message)
        return wrapper

    @check_integration_enabled
    @cache_translation
    async def get_translation(self, locale: str, original_message: str) -> str:
        from app.services.cache import set_data_in_redis_with_expiration
        prompt = f"Translate the following text to ISO Code '{locale}': '{original_message}'"
        translated_response = await self.generate_question(prompt)

        country = locale.split("-")[1] if len(locale.split("-")) > 1 else locale

        response = f":flag_{country.lower()}: -> {translated_response}"
        set_data_in_redis_with_expiration(original_message, locale, 60 * 60 * 24 * 7)

        return response

    async def generate_question(self, message: str) -> str:
        chat_completion = await self.client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": message,
                }
            ],
            model="gpt-3.5-turbo",
        )
        return chat_completion.choices[0].message.content
