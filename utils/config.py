import os


MODEL = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free" # не меняем, показывает хороший результат
MODEL_FALLBACKS = [
    "dots-studio/dots-3-note-preview:free",
    "liquid/lfm-3-1.7b:free",
    "openrouter/free",
]
SVEDOS_DEMO_MODE = False
MAX_AI_TOKENS = 2000
AI_UNAVAILABLE_MESSAGE = (
    "Наш ИИ-наставник временно недоступен. Пожалуйста, попробуйте позже "
    "или выберите другую модель."
)

MAX_UPLOAD_SIZE_MB = 100
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
MAX_DECODED_AUDIO_BYTES = 128 * 1024 * 1024
MAX_AUDIO_DURATION_SECONDS = 20 * 60
MAX_AUDIO_DURATION_MESSAGE = (
    "Аудиофайл слишком длинный. Максимальная длительность анализа — 20 минут."
)
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav"}
INVALID_AUDIO_TYPE_MESSAGE = "Загрузите аудиофайл в формате MP3 или WAV."
INVALID_AUDIO_FILE_MESSAGE = (
    "Не удалось прочитать аудиофайл. Проверьте, что файл не повреждён."
)
UPLOAD_TOO_LARGE_MESSAGE = "Максимальный размер аудиофайла — 100 МБ."
DECODED_AUDIO_TOO_LARGE_MESSAGE = (
    "Этот аудиофайл потребует слишком много памяти для безопасного анализа. "
    "Уменьшите частоту дискретизации, число каналов или длительность."
)

RATE_LIMIT_SECONDS = 3
RATE_LIMIT_MESSAGE = "Не так быстро! Подождите несколько секунд перед следующим запросом."
MAX_USER_MESSAGES = 10


def get_message_limit(demo_package_active=False):
    return MAX_USER_MESSAGES


MAX_CHAT_MESSAGE_CHARS = 500
MESSAGE_LIMIT_MESSAGE = (
    "Лимит сообщений для текущего разбора исчерпан."
)
MESSAGE_TOO_LONG_MESSAGE = (
    "Сообщение слишком длинное. Максимум — 500 символов."
)

def get_server_secret(name):
    try:
        import streamlit as st

        value = st.secrets.get(name)
    except (FileNotFoundError, KeyError):
        value = None
    if value:
        return str(value)
    value = os.environ.get(name)
    return str(value) if value else None


def _is_production():
    environment = (
        get_server_secret("SVEDOS_ENV")
        or get_server_secret("APP_ENV")
        or "development"
    )
    return (
        environment.strip().lower()
        in {"prod", "production"}
    )


def get_storage_backend():
    configured = get_server_secret("SVEDOSSTORAGEBACKEND")
    if not configured:
        if _is_production():
            raise RuntimeError(
                "В production обязательно задайте SVEDOSSTORAGEBACKEND=supabase."
            )
        return "local"
    backend = configured.strip().lower()
    if _is_production() and backend != "supabase":
        raise RuntimeError(
            "В production разрешён только SVEDOSSTORAGEBACKEND=supabase."
        )
    return backend


def validate_storage_configuration():
    backend = get_storage_backend()
    if backend not in {"local", "supabase"}:
        raise RuntimeError(
            "SVEDOSSTORAGEBACKEND должен иметь значение local или supabase."
        )
    if backend == "supabase":
        storage_names = [
            "SUPABASEDBURL",
            "SUPABASE_URL",
            "SUPABASESERVICEROLE_KEY",
            "APPBASEURL",
        ]
        missing_storage = [
            name for name in storage_names if not get_server_secret(name)
        ]
        if missing_storage:
            raise RuntimeError(
                "Не настроено хранилище Supabase: отсутствуют "
                + ", ".join(missing_storage)
            )
        if _is_production():
            missing_app_config = [
                name
                for name in ("GUESTCOOKIESECRET", "API_KEY")
                if not get_server_secret(name)
            ]
            if missing_app_config:
                raise RuntimeError(
                    "Для production не настроена конфигурация приложения: "
                    + ", ".join(missing_app_config)
                )
            cookie_secret = get_server_secret("GUESTCOOKIESECRET")
            if len(cookie_secret.encode("utf-8")) < 32:
                raise RuntimeError(
                    "Для production GUESTCOOKIESECRET должен содержать "
                    "не менее 32 байт случайных данных."
                )
            if cookie_secret.casefold().startswith("replace-with"):
                raise RuntimeError(
                    "Замените пример GUESTCOOKIESECRET на случайный секрет."
                )
    return backend

AUDIO_FACTS = [
    "Человеческое ухо особенно чувствительно к области 2–4 кГц — там легко замаскировать разборчивость вокала.",
    "Низкие частоты требуют больше энергии, чем высокие, поэтому избыток баса быстро съедает запас громкости.",
    "Стерео-сигнал может звучать широко, но частично ослабевать при сведении в моно — проверяй микс на совместимость.",
    "Комната и положение колонок заметно меняют восприятие баса; сравнивай микс и в наушниках, и на других системах.",
    "Короткое сравнение с референсом на одинаковой громкости обычно полезнее, чем сравнение просто «громче — значит лучше».",
    "Компрессия уменьшает разницу между тихими и громкими частями, но слишком сильная компрессия может лишить микс движения.",
    "Реверберация помогает поместить звук в пространство, но длинный хвост способен сделать вокал менее разборчивым.",
    "Панорамирование помогает разделять инструменты, однако важные элементы микса стоит проверить и в моно.",
    "Спектральный центроид приблизительно описывает яркость сигнала, но сам по себе не определяет, хорош ли тембр.",
    "Сатурация добавляет гармоники и может сделать звук плотнее; она не исправляет уже возникший цифровой клиппинг.",
    "Небольшие изменения громкости при сравнении часто влияют на впечатление сильнее, чем ожидается.",
    "Атака и затухание компрессора влияют на транзиенты: слишком быстрая атака может смягчить удар бочки и малого барабана.",
    "Чистый низ часто получается не усилением баса, а поиском и устранением лишнего гула и конфликтов.",
    "Перерывы помогают отдохнуть слуху: после долгого прослушивания легче переоценить яркость и громкость.",
]
