import os
from dotenv import load_dotenv

load_dotenv()

# Основные настройки
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ГРУППЫ")
GROUP_ID = int(os.getenv("GROUP_ID", "123456789"))
COMMAND_PREFIX = os.getenv("PREFIX", "!")
DEV_PREFIX = os.getenv("DEV_PREFIX", "!!")

# DEV пользователи
DEV_USER_IDS = list(map(int, os.getenv("DEV_IDS", "123456789,987654321").split(',')))

# Настройки БД
DATABASE_FILE = "data/orbit.db"

# Настройки по умолчанию для чатов
DEFAULT_CHAT_SETTINGS = {
    "antimat": True,
    "antiflood": True,
    "anticaps": False,
    "antilinks": True,
    "antimedia": False,
    "max_warns": 3,
    "warn_expire_hours": 24,
    "mute_duration": 300
}

# Описания уровней прав
LEVEL_NAMES = {
    0: "🚫 Заблокированный",
    1: "👤 Гость",
    2: "👥 Участник",
    3: "🛡️ Модератор",
    4: "⭐ Старший модератор",
    5: "👑 Администратор",
    6: "🔥 Лидер чата",
    7: "👑 Владелец беседы",
    999: "⚡ DEVELOPER"
}