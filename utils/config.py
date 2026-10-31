MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
MAX_AI_TOKENS = 350
AI_UNAVAILABLE_MESSAGE = (
    "Наш ИИ-наставник временно недоступен. Пожалуйста, попробуйте позже "
    "или выберите другую модель."
)

MAX_UPLOAD_SIZE_MB = 30
MAX_UPLOAD_SIZE_BYTES = MAX_UPLOAD_SIZE_MB * 1024 * 1024
SUPPORTED_AUDIO_EXTENSIONS = {".mp3", ".wav"}
INVALID_AUDIO_TYPE_MESSAGE = "Загрузите аудиофайл в формате MP3 или WAV."
INVALID_AUDIO_FILE_MESSAGE = (
    "Не удалось прочитать аудиофайл. Проверьте, что файл не повреждён."
)
UPLOAD_TOO_LARGE_MESSAGE = "Максимальный размер аудиофайла — 30 МБ."

RATE_LIMIT_SECONDS = 3
RATE_LIMIT_MESSAGE = "Не так быстро! Подождите несколько секунд перед следующим запросом."
MAX_USER_MESSAGES = 10
MAX_CHAT_MESSAGE_CHARS = 500
MESSAGE_LIMIT_MESSAGE = (
    "✨ Бесплатные сообщения закончились. Подключите пакет сообщений, "
    "чтобы продолжить."
)
MESSAGE_TOO_LONG_MESSAGE = (
    "Сообщение слишком длинное. Максимум — 500 символов."
)
