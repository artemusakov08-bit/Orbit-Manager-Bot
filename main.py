import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id
import sqlite3
import json
import time
from datetime import datetime, timedelta
import os
import sys
import threading

# ========== КОНФИГ ==========
# Получаем настройки из переменных окружения Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
if not BOT_TOKEN:
    print("❌ ОШИБКА: BOT_TOKEN не задан в Render Environment")
    sys.exit(1)

GROUP_ID = os.getenv("GROUP_ID")
if not GROUP_ID:
    print("❌ ОШИБКА: GROUP_ID не задан")
    sys.exit(1)

try:
    GROUP_ID = int(GROUP_ID)
except:
    print("❌ ОШИБКА: GROUP_ID должен быть числом")
    sys.exit(1)

# DEV ID берем из переменной окружения или спрашиваем
DEV_IDS = []
dev_env = os.getenv("DEV_IDS", "").strip()
if dev_env:
    try:
        DEV_IDS = [int(x.strip()) for x in dev_env.split(",") if x.strip()]
    except:
        DEV_IDS = []

if not DEV_IDS:
    print("⚠️  Введите ваш ID ВК (можно несколько через запятую)")
    try:
        user_input = input("DEV_IDS: ").strip()
        if user_input:
            DEV_IDS = [int(x.strip()) for x in user_input.split(",")]
    except:
        pass

if not DEV_IDS:
    print("⚠️  Используем тестовый DEV_ID")
    DEV_IDS = [1]

print(f"✅ Токен получен")
print(f"✅ ID группы: {GROUP_ID}")
print(f"✅ DEV IDS: {DEV_IDS}")

PREFIX = "!"
DEV_PREFIX = "!!"

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('orbit.db', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.init_db()
    
    def init_db(self):
        # Пользователи
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER,
                chat_id INTEGER,
                level INTEGER DEFAULT 2,
                warns INTEGER DEFAULT 0,
                muted_until TEXT,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        # Чаты
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                owner_id INTEGER,
                title TEXT
            )
        ''')
        self.conn.commit()
    
    def get_user_level(self, user_id, chat_id):
        if user_id in DEV_IDS:
            return 999
        
        self.cursor.execute(
            "SELECT level FROM users WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        )
        row = self.cursor.fetchone()
        return row['level'] if row else 2
    
    def set_user_level(self, user_id, chat_id, level):
        self.cursor.execute('''
            INSERT OR REPLACE INTO users (user_id, chat_id, level)
            VALUES (?, ?, ?)
        ''', (user_id, chat_id, level))
        self.conn.commit()
        return True

db = Database()

# ========== ОСНОВНОЙ КЛАСС БОТА ==========
class OrbitBot:
    def __init__(self):
        print("🔧 Инициализация бота...")
        try:
            self.vk_session = vk_api.VkApi(token=BOT_TOKEN)
            self.vk = self.vk_session.get_api()
            self.longpoll = VkBotLongPoll(self.vk_session, GROUP_ID)
            print("✅ VK API подключен")
        except Exception as e:
            print(f"❌ Ошибка подключения: {e}")
            sys.exit(1)
        
        self.commands = {
            "старт": self.cmd_start,
            "помощь": self.cmd_help,
            "профиль": self.cmd_profile,
            "права": self.cmd_rights,
            "варн": self.cmd_warn,
            "кик": self.cmd_kick,
            "мут": self.cmd_mute,
            "стата": self.cmd_stats,
            "топ": self.cmd_top,
        }
        
        self.dev_commands = {
            "обновить": self.dev_update,
            "выйти": self.dev_leave,
            "статус": self.dev_status,
        }
        
        print("✅ Бот инициализирован")
        print("=" * 50)
    
    # ========== ОСНОВНЫЕ КОМАНДЫ ==========
    def cmd_start(self, event, args):
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        self.send(chat_id, 
            "👋 Orbit Manager активирован!\n"
            "📋 Команды: !помощь\n"
            "⚡ Система прав: 0-7 + DEV\n"
            "🔧 Для админов: !права @user уровень"
        )
    
    def cmd_help(self, event, args):
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        level = db.get_user_level(user_id, chat_id)
        
        help_text = "📚 Команды Orbit Manager:\n\n"
        
        if level >= 1:
            help_text += "👤 Основные:\n"
            help_text += "!помощь - эта справка\n"
            help_text += "!профиль - информация\n"
            help_text += "!стата - статистика\n\n"
        
        if level >= 3:
            help_text += "🛡️ Модерация:\n"
            help_text += "!варн @user - предупреждение\n"
            help_text += "!кик @user - исключить\n"
            help_text += "!мут @user 30м - мут\n\n"
        
        if level >= 5:
            help_text += "👑 Админ:\n"
            help_text += "!права @user 0-7 - права\n"
            help_text += "!топ - топ активных\n\n"
        
        if level == 999:
            help_text += "⚡ DEV:\n"
            help_text += "!!обновить - перезагрузка\n"
            help_text += "!!выйти id - выйти из чата\n"
            help_text += "!!статус - статус бота\n"
        
        self.send(chat_id, help_text)
    
    def cmd_profile(self, event, args):
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        level = db.get_user_level(user_id, chat_id)
        level_names = {
            0: "🚫 Заблокированный",
            1: "👤 Гость",
            2: "👥 Участник",
            3: "🛡️ Модератор",
            4: "⭐ Старший модератор",
            5: "👑 Администратор",
            6: "🔥 Лидер чата",
            7: "👑 Владелец",
            999: "⚡ DEVELOPER"
        }
        
        profile = (
            f"👤 Профиль [id{user_id}|пользователя]\n"
            f"📊 Уровень: {level_names.get(level, level)}\n"
            f"🆔 ID: {user_id}"
        )
        
        self.send(chat_id, profile)
    
    def cmd_rights(self, event, args):
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if db.get_user_level(user_id, chat_id) < 5:
            self.send(chat_id, "❌ Требуется уровень 5+")
            return
        
        parts = args.split()
        if len(parts) < 2:
            self.send(chat_id, "❌ Формат: !права [id] [0-7]\nПример: !права 123456789 5")
            return
        
        try:
            target_id = int(parts[0])
            new_level = int(parts[1])
            if not (0 <= new_level <= 7):
                raise ValueError
        except:
            self.send(chat_id, "❌ Неверный формат. Пример: !права 123456789 5")
            return
        
        db.set_user_level(target_id, chat_id, new_level)
        self.send(chat_id, f"✅ Права пользователя [id{target_id}|...] изменены на уровень {new_level}")
    
    def cmd_warn(self, event, args):
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if db.get_user_level(user_id, chat_id) < 3:
            self.send(chat_id, "❌ Требуется уровень 3+")
            return
        
        if not args.strip():
            self.send(chat_id, "❌ Укажите ID пользователя")
            return
        
        try:
            target_id = int(args.split()[0])
        except:
            self.send(chat_id, "❌ Неверный ID")
            return
        
        self.send(chat_id, f"⚠️ Пользователю [id{target_id}|...] выдано предупреждение")
    
    def cmd_kick(self, event, args):
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if db.get_user_level(user_id, chat_id) < 3:
            self.send(chat_id, "❌ Требуется уровень 3+")
            return
        
        if not args.strip():
            self.send(chat_id, "❌ Укажите ID пользователя")
            return
        
        try:
            target_id = int(args.split()[0])
        except:
            self.send(chat_id, "❌ Неверный ID")
            return
        
        try:
            self.vk.messages.removeChatUser(
                chat_id=chat_id,
                user_id=target_id
            )
            self.send(chat_id, f"👢 Пользователь [id{target_id}|...] исключен")
        except Exception as e:
            self.send(chat_id, f"❌ Ошибка: {e}")
    
    def cmd_mute(self, event, args):
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if db.get_user_level(user_id, chat_id) < 3:
            self.send(chat_id, "❌ Требуется уровень 3+")
            return
        
        parts = args.split()
        if len(parts) < 2:
            self.send(chat_id, "❌ Формат: !мут [id] [время]\nПример: !мут 123456789 30м")
            return
        
        try:
            target_id = int(parts[0])
            time_str = parts[1]
            self.send(chat_id, f"🔇 Пользователь [id{target_id}|...] замьючен на {time_str}")
        except:
            self.send(chat_id, "❌ Неверный формат")
    
    def cmd_stats(self, event, args):
        chat_id = event.chat_id
        self.send(chat_id, f"📊 Чат #{chat_id}\n👑 Бот Orbit Manager v1.0")
    
    def cmd_top(self, event, args):
        chat_id = event.chat_id
        self.send(chat_id, "🏆 Топ пользователей:\n1. [id1|User1]\n2. [id2|User2]\n3. [id3|User3]")
    
    # ========== DEV КОМАНДЫ ==========
    def dev_update(self, event, args):
        user_id = event.object.message['from_id']
        if user_id not in DEV_IDS:
            return
        self.send(event.chat_id, "🔄 Бот перезагружается...")
    
    def dev_leave(self, event, args):
        user_id = event.object.message['from_id']
        if user_id not in DEV_IDS:
            return
        
        if not args.strip():
            self.send(event.chat_id, "❌ Укажите ID чата")
            return
        
        try:
            chat_id = int(args)
            self.vk.messages.removeChatUser(
                chat_id=chat_id,
                member_id=-int(GROUP_ID)
            )
            self.send(event.chat_id, f"✅ Бот вышел из чата {chat_id}")
        except Exception as e:
            self.send(event.chat_id, f"❌ Ошибка: {e}")
    
    def dev_status(self, event, args):
        user_id = event.object.message['from_id']
        if user_id not in DEV_IDS:
            return
        
        status = (
            "⚡ Статус бота:\n"
            f"✅ Активен\n"
            f"👑 DEV: {DEV_IDS}\n"
            f"📊 Группа: {GROUP_ID}\n"
            f"🕐 Время: {datetime.now()}"
        )
        self.send(event.chat_id, status)
    
    # ========== СЛУЖЕБНЫЕ ФУНКЦИИ ==========
    def send(self, chat_id, text):
        try:
            self.vk.messages.send(
                chat_id=chat_id,
                message=text,
                random_id=get_random_id()
            )
        except Exception as e:
            print(f"❌ Ошибка отправки: {e}")
    
    def parse_command(self, text):
        text = text.strip()
        if text.startswith(DEV_PREFIX):
            prefix = DEV_PREFIX
            text = text[len(DEV_PREFIX):].strip()
            is_dev = True
        elif text.startswith(PREFIX):
            prefix = PREFIX
            text = text[len(PREFIX):].strip()
            is_dev = False
        else:
            return None, None, False
        
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        return command, args, is_dev
    
    def run(self):
        print("🚀 Бот запущен! Ожидание сообщений...")
        print("Для остановки: Ctrl+C")
        
        while True:
            try:
                for event in self.longpoll.listen():
                    if event.type == VkBotEventType.MESSAGE_NEW and event.from_chat:
                        msg = event.object.message
                        chat_id = event.chat_id
                        user_id = msg['from_id']
                        text = msg.get('text', '').strip()
                        
                        print(f"[{chat_id}] {user_id}: {text}")
                        
                        command, args, is_dev = self.parse_command(text)
                        
                        if command:
                            if is_dev:
                                if user_id in DEV_IDS and command in self.dev_commands:
                                    self.dev_commands[command](event, args)
                            else:
                                if command in self.commands:
                                    self.commands[command](event, args)
                    
                    elif event.type == VkBotEventType.GROUP_JOIN:
                        chat_id = event.object['peer_id'] - 2000000000
                        print(f"✅ Бота добавили в чат {chat_id}")
                        self.send(chat_id, "👋 Orbit Manager добавлен! Напишите !старт")
                    
                    elif event.type == VkBotEventType.GROUP_LEAVE:
                        chat_id = event.object['peer_id'] - 2000000000
                        print(f"❌ Бота исключили из чата {chat_id}")
            
            except vk_api.exceptions.ApiError as e:
                if "invalid access_token" in str(e):
                    print("❌ НЕВЕРНЫЙ ТОКЕН! Проверьте BOT_TOKEN в Render")
                    print("Получите новый токен в настройках группы ВК")
                    sys.exit(1)
                print(f"⚠️  Ошибка VK API: {e}")
                time.sleep(5)
            
            except Exception as e:
                print(f"⚠️  Ошибка: {e}")
                time.sleep(5)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    try:
        bot = OrbitBot()
        bot.run()
    except KeyboardInterrupt:
        print("\n🛑 Бот остановлен")
    except Exception as e:
        print(f"❌ Критическая ошибка: {e}")
        sys.exit(1)