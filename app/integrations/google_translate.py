

import html
import re
import urllib.parse

import detectlanguage
import discord
from requests import RequestException

import app
from app import logger
from app.constants import Dependencies
from app.constants import LogTypes as logconstants
from app.integrations import http_client
from app.services import metrics
from app.services.utils import ml

languages_with_flags = {
    "ar": "🇸🇦",  # Arabic - Saudi Arabia
    "hy": "🇦🇲",  # Armenian - Armenia
    "az": "🇦🇿",  # Azerbaijani - Azerbaijan
    "be": "🇧🇾",  # Belarusian - Belarus
    "bn": "🇧🇩",  # Bengali - Bangladesh
    "bg": "🇧🇬",  # Bulgarian - Bulgaria
    "zh": "🇨🇳",  # Chinese - China
    "cs": "🇨🇿",  # Czech - Czech Republic
    "da": "🇩🇰",  # Danish - Denmark
    "nl": "🇳🇱",  # Dutch - Netherlands
    "en": "🇺🇸",  # English - United States
    "et": "🇪🇪",  # Estonian - Estonia
    "fi": "🇫🇮",  # Finnish - Finland
    "fr": "🇫🇷",  # French - France
    "ka": "🇬🇪",  # Georgian - Georgia
    "de": "🇩🇪",  # German - Germany
    "el": "🇬🇷",  # Greek - Greece
    "he": "🇮🇱",  # Hebrew - Israel
    "hi": "🇮🇳",  # Hindi - India
    "hu": "🇭🇺",  # Hungarian - Hungary
    "is": "🇮🇸",  # Icelandic - Iceland
    "id": "🇮🇩",  # Indonesian - Indonesia
    "it": "🇮🇹",  # Italian - Italy
    "ja": "🇯🇵",  # Japanese - Japan
    "ko": "🇰🇷",  # Korean - South Korea
    "lv": "🇱🇻",  # Latvian - Latvia
    "lt": "🇱🇹",  # Lithuanian - Lithuania
    "mk": "🇲🇰",  # Macedonian - North Macedonia
    "ms": "🇲🇾",  # Malay - Malaysia
    "ml": "🇮🇳",  # Malayalam - India
    "mt": "🇲🇹",  # Maltese - Malta
    "ne": "🇳🇵",  # Nepali - Nepal
    "no": "🇳🇴",  # Norwegian - Norway
    "fa": "🇮🇷",  # Persian - Iran
    "pl": "🇵🇱",  # Polish - Poland
    "pt": "🇧🇷",  # Portuguese - Brazil
    "pa": "🇮🇳",  # Panjabi - India
    "ro": "🇷🇴",  # Romanian - Romania
    "ru": "🇷🇺",  # Russian - Russia
    "sr": "🇷🇸",  # Serbian - Serbia
    "sk": "🇸🇰",  # Slovak - Slovakia
    "sl": "🇸🇮",  # Slovenian - Slovenia
    "es": "🇪🇸",  # Spanish - Spain
    "sv": "🇸🇪",  # Swedish - Sweden
    "ta": "🇮🇳",  # Tamil - India
    "th": "🇹🇭",  # Thai - Thailand
    "tr": "🇹🇷",  # Turkish - Turkey
    "uk": "🇺🇦",  # Ukrainian - Ukraine
    "ur": "🇵🇰",  # Urdu - Pakistan
    "uz": "🇺🇿",  # Uzbek - Uzbekistan
    "vi": "🇻🇳",  # Vietnamese - Vietnam
    "cy": "🇬🇧",  # Welsh - United Kingdom (Wales)
    "yo": "🇳🇬",  # Yoruba - Nigeria
    "zu": "🇿🇦"   # Zulu - South Africa
}


detectlanguage.configuration.api_key = app.bot.config.DETECT_LANGUAGE_API_KEY
detectlanguage.configuration.secure = True
detectlanguage.configuration.timeout = Dependencies.LANGUAGE_DETECTION_TIMEOUT_SECONDS

class GoogleTranslate:
    pattern = r'(?s)class="(?:t0|result-container)">(.*?)<'
    message_validation = r"^(?=.*\S)(?:https?|ftp):\/\/[^\s/$.?#].[^\s]*$"

    @staticmethod
    def translate(content: str, dest_locale: discord.Locale) -> str:
        dest = GoogleTranslate.parse_destination_locale(dest_locale.value)
        full_dest = languages_with_flags.get(dest, "🌐")

        try:
            src = GoogleTranslate.detect(content)
            full_src = languages_with_flags.get(src, "🌐")
        except (Exception, detectlanguage.DetectLanguageError) as error:
            logger.error(f"Error while detecting language: {error}", log_type=logconstants.COMMAND_ERROR_TYPE,)
            full_src = ml("errors.translate-message.unknown-language", dest_locale)

        translated_message = GoogleTranslate.google_translate(content, dest)
        if not translated_message:
            return None

        return {"src": full_src, "dest": full_dest, "original": content, "translated_message": translated_message}

    @staticmethod
    def google_translate(content: str, dest: str) -> str:
        escaped_text = urllib.parse.quote(content.encode("utf8"))
        url = "https://translate.google.com/m?tl=%s&sl=%s&q=%s" % (
            dest,
            "auto",
            escaped_text,
        )
        try:
            response = http_client.get("translate", url)
        except RequestException as error:
            logger.warn(
                f"Google Translate did not answer: {type(error).__name__}",
                log_type=logconstants.COMMAND_WARN_TYPE,
            )
            return None
        result = response.text.encode("utf8").decode("utf8")
        result = re.findall(GoogleTranslate.pattern, response.content.decode("utf-8"))

        if not result:
            return None

        response = html.unescape(result[0])

        return response

    @staticmethod
    def is_only_url(content: str) -> bool:
        if re.match(GoogleTranslate.message_validation, content):
            return True

    @staticmethod
    def parse_destination_locale(locale: str) -> str:
        return locale.split("-")[0]

    @staticmethod
    def get_language_with_flag(locale: str) -> str:
        locale = GoogleTranslate.parse_destination_locale(locale)
        return f"{languages_with_flags.get(locale, '🌐')} {ml(f'locales.{locale}', locale)}"

    @staticmethod
    def detect(content: str) -> str:
        with metrics.timed("translate"):
            return detectlanguage.simple_detect(content)
