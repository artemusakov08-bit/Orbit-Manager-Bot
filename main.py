import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id
import sqlite3
import json
import threading
import time
from datetime import datetime, timedelta
import logging
import re
import os
from flask import Flask

# ========== НАСТРОЙКИ ДЛЯ RENDER ==========
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")  # Из переменных окружения
GROUP_ID = os.getenv("GROUP_ID", "123456789")
COMMAND_PREFIX = "!"
DEV_PREFIX = "!!"
DEV_IDS = [int(x) for x in os.getenv("DEV_IDS", "123456789").split(",")]
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///orbit.db")

# Flask app для вебхука (нужен для Render)
app = Flask(__name__)

@app.route('/')
def home():
    return "Orbit Manager Bot is running!"

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect('orbit.db', check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self.init_db()
    
    def init_db(self):
        # Чаты
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                owner_id INTEGER,
                settings TEXT DEFAULT '{}'
            )
        ''')
        # Права
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_perms (
                user_id INTEGER,
                chat_id INTEGER,
                level INTEGER DEFAULT 2,
                warns INTEGER DEFAULT 0,
                reputation INTEGER DEFAULT 0,
                muted_until TEXT,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        # Логи
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                action TEXT,
                details TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        self.conn.commit()
    
    def get_user_level(self, user_id, chat_id):
        if user_id in DEV_IDS:
            return 999
        
        self.cursor.execute(
            "SELECT level FROM user_perms WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        )
        row = self.cursor.fetchone()
        if row:
            return row[0]
        
        # Проверяем владельца чата
        self.cursor.execute(
            "SELECT owner_id FROM chats WHERE chat_id=?",
            (chat_id,)
        )
        chat = self.cursor.fetchone()
        if chat and chat[0] == user_id:
            self.set_user_level(user_id, chat_id, 7)
            return 7
        
        return 2
    
    def set_user_level(self, user_id, chat_id, level):
        if level == 7:
            self.cursor.execute(
                "UPDATE chats SET owner_id=? WHERE chat_id=?",
                (user_id, chat_id)
            )
        self.cursor.execute(
            "INSERT OR REPLACE INTO user_perms (user_id, chat_id, level) VALUES (?, ?, ?)",
            (user_id, chat_id, level)
        )
        self.conn.commit()
    
    def add_warn(self, user_id, chat_id):
        self.cursor.execute(
            "UPDATE user_perms SET warns=warns+1 WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        )
        self.conn.commit()
        # Проверяем, не превысил ли лимит варнов
        self.cursor.execute(
            "SELECT warns FROM user_perms WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        )
        warns = self.cursor.fetchone()[0]
        return warns
    
    def get_chat_settings(self, chat_id):
        self.cursor.execute(
            "SELECT settings FROM chats WHERE chat_id=?",
            (chat_id,)
        )
        row = self.cursor.fetchone()
        if row:
            return json.loads(row[0])
        # Настройки по умолчанию
        default = {
            "antimat": True,
            "antiflood": True,
            "anticaps": False,
            "max_warns": 3
        }
        self.cursor.execute(
            "INSERT INTO chats (chat_id, settings) VALUES (?, ?)",
            (chat_id, json.dumps(default))
        )
        self.conn.commit()
        return default

db = Database()

# ========== ОСНОВНОЙ КЛАСС БОТА ==========
class OrbitManager:
    def __init__(self):
        self.vk_session = vk_api.VkApi(token=BOT_TOKEN)
        self.vk = self.vk_session.get_api()
        self.longpoll = VkBotLongPoll(self.vk_session, GROUP_ID)
        self.running = True
        self.commands = {}
        self.dev_commands = {}
        self.register_commands()
        print("🚀 Orbit Manager запущен!")
    
    def register_commands(self):
        # Обычные команды
        self.commands = {
            "старт": self.cmd_start,
            "помощь": self.cmd_help,
            "профиль": self.cmd_profile,
            "права": self.cmd_rights,
            "варн": self.cmd_warn,
            "кик": self.cmd_kick,
            "мут": self.cmd_mute,
            "размут": self.cmd_unmute,
            "бан": self.cmd_ban,
            "разбан": self.cmd_unban,
            "стата": self.cmd_stats,
            "топ": self.cmd_top,
            "настройки": self.cmd_settings,
            "сохранить": self.cmd_save,
            "префикс": self.cmd_prefix
        }
        
        # DEV команды (с префиксом !!)
        self.dev_commands = {
            "обновить": self.dev_update,
            "выйти": self.dev_leave,
            "глобал": self.dev_global,
            "логи": self.dev_logs,
            "eval": self.dev_eval
        }
    
    def parse_message(self, text):
        """Парсинг команды"""
        text = text.strip()
        if text.startswith(DEV_PREFIX):
            prefix = DEV_PREFIX
            text = text[len(DEV_PREFIX):].strip()
            is_dev = True
        elif text.startswith(COMMAND_PREFIX):
            prefix = COMMAND_PREFIX
            text = text[len(COMMAND_PREFIX):].strip()
            is_dev = False
        else:
            return None, None, False
        
        parts = text.split(maxsplit=1)
        command = parts[0].lower()
        args = parts[1] if len(parts) > 1 else ""
        
        return command, args, is_dev
    
    def get_mention_id(self, text):
        """Извлечение ID из упоминания [id123|Name]"""
        match = re.search(r'\[id(\d+)\|', text)
        if match:
            return int(match.group(1))
        return None
    
    def check_permission(self, user_id, chat_id, required_level):
        """Проверка прав доступа"""
        user_level = db.get_user_level(user_id, chat_id)
        
        if user_level == 999:  # DEV
            return True
        if user_level == 0:    # Заблокирован
            return False
        return user_level >= required_level
    
    def send_message(self, chat_id, text, reply_to=None):
        """Отправка сообщения"""
        try:
            params = {
                'chat_id': chat_id,
                'message': text,
                'random_id': get_random_id()
            }
            if reply_to:
                params['forward_messages'] = reply_to
            
            self.vk.messages.send(**params)
        except Exception as e:
            print(f"Ошибка отправки: {e}")
    
    # ========== ОСНОВНЫЕ КОМАНДЫ ==========
    def cmd_start(self, event, args):
        """!старт - Активировать бота"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        # Проверяем, существует ли чат
        db.get_chat_settings(chat_id)
        
        # Назначаем создателя владельцем
        members = self.vk.messages.getConversationMembers(peer_id=2000000000 + chat_id)
        if members.get('items'):
            creator = None
            for member in members['items']:
                if member.get('is_creator'):
                    creator = member['member_id']
                    break
            if creator:
                db.set_user_level(creator, chat_id, 7)
        
        self.send_message(chat_id, 
            "👋 Orbit Manager активирован!\n"
            "📋 Используйте !помощь для списка команд\n"
            "⚙️ Настройте бота командой !настройки"
        )
    
    def cmd_help(self, event, args):
        """!помощь - Справка по командам"""
        user_id = event.object.message['from_id']
        chat_id = event.chat_id
        user_level = db.get_user_level(user_id, chat_id)
        
        help_text = "📚 Доступные команды:\n\n"
        
        # Гость (уровень 1+)
        if user_level >= 1:
            help_text += "👤 Гость:\n"
            help_text += "!помощь - эта справка\n"
            help_text += "!профиль [@упоминание] - информация о пользователе\n"
            help_text += "!правила - правила чата\n\n"
        
        # Участник (уровень 2+)
        if user_level >= 2:
            help_text += "👥 Участник:\n"
            help_text += "!репутация [+/-] [@упоминание] - изменить репутацию\n"
            help_text += "!топ - топ активных пользователей\n"
            help_text += "!голосование [вопрос] - создать голосование\n\n"
        
        # Модератор (уровень 3+)
        if user_level >= 3:
            help_text += "🛡️ Модератор:\n"
            help_text += "!варн [@упоминание] [причина] - выдать предупреждение\n"
            help_text += "!кик [@упоминание] [причина] - исключить из беседы\n"
            help_text += "!мут [@упоминание] [время] - ограничить чат\n"
            help_text += "!очистка [число] - удалить сообщения\n\n"
        
        # Администратор (уровень 5+)
        if user_level >= 5:
            help_text += "👑 Администратор:\n"
            help_text += "!права [@упоминание] [0-7] - изменить права\n"
            help_text += "!бан [@упоминание] [причина] - заблокировать\n"
            help_text += "!настройки - настройки чата\n"
            help_text += "!сохранить - сохранить настройки\n\n"
        
        # DEV команды
        if user_level == 999:
            help_text += "⚡ DEVELOPER:\n"
            help_text += "!!обновить - перезагрузить бота\n"
            help_text += "!!выйти [id чата] - покинуть чат\n"
            help_text += "!!глобал [сообщение] - глобальная рассылка\n"
            help_text += "!!логи [кол-во] - просмотр логов\n"
        
        self.send_message(chat_id, help_text)
    
    def cmd_profile(self, event, args):
        """!профиль - Информация о пользователе"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        # Проверяем упоминание
        target_id = self.get_mention_id(args)
        if not target_id:
            target_id = user_id
        
        # Получаем уровень
        level = db.get_user_level(target_id, chat_id)
        
        # Получаем информацию о пользователе
        user_info = self.vk.users.get(user_ids=target_id, fields='online,last_seen')[0]
        
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
            f"👤 Профиль: {user_info['first_name']} {user_info['last_name']}\n"
            f"📊 Уровень: {level_names.get(level, level)}\n"
            f"🆔 ID: {target_id}\n"
            f"⚠️ Варны: {db.add_warn(target_id, chat_id) - 1}/3\n"
            f"⭐ Репутация: 0\n"
            f"🌐 Онлайн: {'✅ Да' if user_info.get('online') else '❌ Нет'}"
        )
        
        self.send_message(chat_id, profile)
    
    def cmd_rights(self, event, args):
        """!права [@упоминание] [уровень] - Изменить права"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        # Проверяем права (только 5+)
        if not self.check_permission(user_id, chat_id, 5):
            self.send_message(chat_id, "❌ Недостаточно прав. Требуется уровень Администратор (5+)")
            return
        
        # Парсим аргументы
        parts = args.split()
        if len(parts) < 2:
            self.send_message(chat_id, "❌ Используйте: !права @упоминание уровень")
            return
        
        target_id = self.get_mention_id(parts[0])
        if not target_id:
            self.send_message(chat_id, "❌ Укажите пользователя через @упоминание")
            return
        
        try:
            new_level = int(parts[1])
            if not (0 <= new_level <= 7):
                raise ValueError
        except:
            self.send_message(chat_id, "❌ Уровень должен быть числом от 0 до 7")
            return
        
        # Проверяем, может ли пользователь изменить права
        user_level = db.get_user_level(user_id, chat_id)
        target_current_level = db.get_user_level(target_id, chat_id)
        
        # Нельзя изменять права выше или равные своим
        if target_current_level >= user_level and user_level != 999:
            self.send_message(chat_id, "❌ Нельзя изменить права пользователю с таким же или высшим уровнем")
            return
        
        # Нельзя назначить уровень выше своего
        if new_level >= user_level and user_level != 999:
            self.send_message(chat_id, f"❌ Вы не можете назначить уровень {new_level}, ваш уровень {user_level}")
            return
        
        # Применяем изменение
        db.set_user_level(target_id, chat_id, new_level)
        
        level_names = {
            0: "🚫 Заблокированный",
            1: "👤 Гость",
            2: "👥 Участник",
            3: "🛡️ Модератор",
            4: "⭐ Старший модератор",
            5: "👑 Администратор",
            6: "🔥 Лидер чата",
            7: "👑 Владелец"
        }
        
        self.send_message(chat_id,
            f"✅ Права обновлены!\n"
            f"👤 Пользователь: [id{target_id}|...]\n"
            f"📊 Новый уровень: {level_names.get(new_level, new_level)}"
        )
    
    def cmd_warn(self, event, args):
        """!варн [@упоминание] [причина] - Выдать предупреждение"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if not self.check_permission(user_id, chat_id, 3):
            self.send_message(chat_id, "❌ Недостаточно прав. Требуется уровень Модератор (3+)")
            return
        
        parts = args.split(maxsplit=1)
        if len(parts) < 1:
            self.send_message(chat_id, "❌ Используйте: !варн @упоминание [причина]")
            return
        
        target_id = self.get_mention_id(parts[0])
        if not target_id:
            self.send_message(chat_id, "❌ Укажите пользователя через @упоминание")
            return
        
        reason = parts[1] if len(parts) > 1 else "Не указана"
        
        # Выдаем варн
        warns = db.add_warn(target_id, chat_id)
        
        message = (
            f"⚠️ Выдано предупреждение!\n"
            f"👤 Кому: [id{target_id}|...]\n"
            f"👮 Кем: [id{user_id}|...]\n"
            f"📝 Причина: {reason}\n"
            f"🔢 Всего варнов: {warns}/3"
        )
        
        # Проверяем, не превышен ли лимит
        if warns >= 3:
            # Автоматический мут на 1 час
            mute_until = (datetime.now() + timedelta(hours=1)).strftime('%Y-%m-%d %H:%M:%S')
            db.cursor.execute(
                "UPDATE user_perms SET muted_until=? WHERE user_id=? AND chat_id=?",
                (mute_until, target_id, chat_id)
            )
            db.conn.commit()
            message += f"\n⏰ Автоматический мут до {mute_until}"
        
        self.send_message(chat_id, message)
    
    def cmd_kick(self, event, args):
        """!кик [@упоминание] [причина] - Исключить из беседы"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if not self.check_permission(user_id, chat_id, 3):
            self.send_message(chat_id, "❌ Недостаточно прав. Требуется уровень Модератор (3+)")
            return
        
        parts = args.split(maxsplit=1)
        if len(parts) < 1:
            self.send_message(chat_id, "❌ Используйте: !кик @упоминание [причина]")
            return
        
        target_id = self.get_mention_id(parts[0])
        if not target_id:
            self.send_message(chat_id, "❌ Укажите пользователя через @упоминание")
            return
        
        reason = parts[1] if len(parts) > 1 else "Не указана"
        
        try:
            # Исключаем пользователя
            self.vk.messages.removeChatUser(
                chat_id=chat_id,
                user_id=target_id
            )
            
            self.send_message(chat_id,
                f"👢 Пользователь исключен!\n"
                f"👤 Кто: [id{target_id}|...]\n"
                f"👮 Кем: [id{user_id}|...]\n"
                f"📝 Причина: {reason}"
            )
        except Exception as e:
            self.send_message(chat_id, f"❌ Ошибка: {str(e)}")
    
    def cmd_mute(self, event, args):
        """!мут [@упоминание] [время] - Ограничить чат"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if not self.check_permission(user_id, chat_id, 3):
            self.send_message(chat_id, "❌ Недостаточно прав")
            return
        
        parts = args.split()
        if len(parts) < 2:
            self.send_message(chat_id, "❌ Используйте: !мут @упоминание время\nПример: !мут @user 30м")
            return
        
        target_id = self.get_mention_id(parts[0])
        if not target_id:
            self.send_message(chat_id, "❌ Укажите пользователя")
            return
        
        time_str = parts[1].lower()
        
        # Парсим время
        if time_str.endswith('м'):
            minutes = int(time_str[:-1])
            delta = timedelta(minutes=minutes)
        elif time_str.endswith('ч'):
            hours = int(time_str[:-1])
            delta = timedelta(hours=hours)
        elif time_str.endswith('д'):
            days = int(time_str[:-1])
            delta = timedelta(days=days)
        else:
            try:
                minutes = int(time_str)
                delta = timedelta(minutes=minutes)
            except:
                self.send_message(chat_id, "❌ Неверный формат времени. Пример: 30м, 2ч, 1д")
                return
        
        mute_until = (datetime.now() + delta).strftime('%Y-%m-%d %H:%M:%S')
        
        db.cursor.execute(
            "UPDATE user_perms SET muted_until=? WHERE user_id=? AND chat_id=?",
            (mute_until, target_id, chat_id)
        )
        db.conn.commit()
        
        self.send_message(chat_id,
            f"🔇 Пользователь замьючен!\n"
            f"👤 Кто: [id{target_id}|...]\n"
            f"⏰ До: {mute_until}\n"
            f"👮 Кем: [id{user_id}|...]"
        )
    
    def cmd_stats(self, event, args):
        """!стата - Статистика чата"""
        chat_id = event.chat_id
        
        db.cursor.execute(
            "SELECT COUNT(*) as total, "
            "SUM(CASE WHEN level >= 5 THEN 1 ELSE 0 END) as admins "
            "FROM user_perms WHERE chat_id=?",
            (chat_id,)
        )
        stats = db.cursor.fetchone()
        
        db.cursor.execute(
            "SELECT COUNT(*) as muted FROM user_perms "
            "WHERE chat_id=? AND muted_until > datetime('now')",
            (chat_id,)
        )
        muted = db.cursor.fetchone()[0]
        
        message = (
            f"📊 Статистика чата:\n"
            f"👥 Всего пользователей: {stats[0] if stats else 0}\n"
            f"👑 Администраторов: {stats[1] if stats else 0}\n"
            f"🔇 Замьючено: {muted}\n"
            f"🆔 ID чата: {chat_id}"
        )
        
        self.send_message(chat_id, message)
    
    def cmd_settings(self, event, args):
        """!настройки - Настройки чата"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if not self.check_permission(user_id, chat_id, 5):
            self.send_message(chat_id, "❌ Только администраторы")
            return
        
        settings = db.get_chat_settings(chat_id)
        
        text = (
            f"⚙️ Настройки чата #{chat_id}:\n\n"
            f"🔤 Анти-мат: {'✅ Вкл' if settings.get('antimat') else '❌ Выкл'}\n"
            f"💬 Анти-флуд: {'✅ Вкл' if settings.get('antiflood') else '❌ Выкл'}\n"
            f"📛 Анти-капс: {'✅ Вкл' if settings.get('anticaps') else '❌ Выкл'}\n"
            f"⚠️ Макс варнов: {settings.get('max_warns', 3)}\n\n"
            f"Используйте: !настройки [параметр] [вкл/выкл]\n"
            f"Пример: !настройки антимат вкл"
        )
        
        self.send_message(chat_id, text)
    
    # ========== DEV КОМАНДЫ ==========
    def dev_update(self, event, args):
        """!!обновить - Перезагрузить бота (DEV)"""
        chat_id = event.chat_id
        user_id = event.object.message['from_id']
        
        if user_id not in DEV_IDS:
            return
        
        self.send_message(chat_id, "🔄 Перезагрузка...")
        # Здесь можно добавить перезагрузку модулей
        self.send_message(chat_id, "✅ Бот обновлен!")
    
    def dev_leave(self, event, args):
        """!!выйти [id] - Покинуть чат (DEV)"""
        user_id = event.object.message['from_id']
        
        if user_id not in DEV_IDS:
            return
        
        try:
            target_chat = int(args)
            self.vk.messages.removeChatUser(
                chat_id=target_chat,
                member_id=-int(GROUP_ID)
            )
            self.send_message(event.chat_id, f"✅ Бот вышел из чата {target_chat}")
        except:
            self.send_message(event.chat_id, "❌ Используйте: !!выйти ID_чата")
    
    def dev_global(self, event, args):
        """!!глобал [текст] - Глобальная рассылка (DEV)"""
        user_id = event.object.message['from_id']
        
        if user_id not in DEV_IDS:
            return
        
        if not args:
            self.send_message(event.chat_id, "❌ Укажите сообщение для рассылки")
            return
        
        # Получаем все чаты, где есть бот
        chats = self.vk.messages.getConversations(filter='all', count=100)
        
        count = 0
        for chat in chats['items']:
            try:
                peer_id = chat['conversation']['peer']['id']
                if peer_id > 2000000000:  # Это беседа
                    chat_id = peer_id - 2000000000
                    self.send_message(chat_id, f"📢 Глобальное сообщение:\n\n{args}")
                    count += 1
                    time.sleep(0.5)  # Задержка против флуда
            except:
                continue
        
        self.send_message(event.chat_id, f"✅ Отправлено в {count} чатов")
    
    # ========== СИСТЕМА НАСЛЕДОВАНИЯ ==========
    def handle_owner_left(self, chat_id, user_id):
        """Обработка выхода владельца"""
        # Проверяем, был ли это владелец
        db.cursor.execute(
            "SELECT owner_id FROM chats WHERE chat_id=?",
            (chat_id,)
        )
        chat = db.cursor.fetchone()
        
        if not chat or chat[0] != user_id:
            return
        
        # Ищем нового владельца (самый высокий уровень)
        db.cursor.execute(
            "SELECT user_id, level FROM user_perms "
            "WHERE chat_id=? AND user_id!=? AND level>0 "
            "ORDER BY level DESC LIMIT 1",
            (chat_id, user_id)
        )
        candidate = db.cursor.fetchone()
        
        if candidate:
            new_owner = candidate[0]
            db.set_user_level(new_owner, chat_id, 7)
            
            self.send_message(chat_id,
                f"⚠️ Владелец беседы покинул чат.\n"
                f"👑 Право владения передано [id{new_owner}|наследнику]."
            )
        else:
            self.send_message(chat_id,
                "⚠️ Владелец покинул чат. Новый владелец не найден."
            )
    
    # ========== ОБРАБОТЧИК СОБЫТИЙ ==========
    def run(self):
        """Запуск бота"""
        while self.running:
            try:
                for event in self.longpoll.listen():
                    if event.type == VkBotEventType.MESSAGE_NEW:
                        if event.from_chat:
                            self.handle_chat_message(event)
                    
                    elif event.type == VkBotEventType.MESSAGE_EVENT:
                        # Обработка callback кнопок
                        pass
                    
                    elif event.type == VkBotEventType.MESSAGE_REPLY:
                        # Ответ на сообщение
                        pass
                    
                    elif event.type == VkBotEventType.USER_TYPING:
                        # Пользователь печатает
                        pass
                    
                    elif event.type == VkBotEventType.CHAT_TITLE_UPDATE:
                        # Изменение названия беседы
                        pass
                    
                    elif event.type == VkBotEventType.USER_ONLINE:
                        # Пользователь онлайн
                        pass
                    
                    elif event.type == VkBotEventType.USER_OFFLINE:
                        # Пользователь оффлайн
                        pass
                    
                    elif event.type == VkBotEventType.GROUP_LEAVE:
                        # Бота исключили из беседы
                        chat_id = event.object['peer_id'] - 2000000000
                        print(f"Бота исключили из чата {chat_id}")
                    
                    elif event.type == VkBotEventType.GROUP_JOIN:
                        # Бота добавили в беседу
                        chat_id = event.object['peer_id'] - 2000000000
                        self.send_message(chat_id,
                            "👋 Orbit Manager добавлен в беседу!\n"
                            "Для активации напишите !старт"
                        )
                    
            except Exception as e:
                print(f"Ошибка в главном цикле: {e}")
                time.sleep(5)
    
    def handle_chat_message(self, event):
        """Обработка сообщений в беседе"""
        message = event.object.message
        chat_id = event.chat_id
        user_id = message['from_id']
        text = message.get('text', '').strip()
        
        # Игнорируем сообщения без текста
        if not text:
            return
        
        # Проверяем муты
        db.cursor.execute(
            "SELECT muted_until FROM user_perms WHERE user_id=? AND chat_id=?",
            (user_id, chat_id)
        )
        mute = db.cursor.fetchone()
        if mute and mute[0]:
            mute_time = datetime.strptime(mute[0], '%Y-%m-%d %H:%M:%S')
            if mute_time > datetime.now():
                # Удаляем сообщение если пользователь в муте
                try:
                    self.vk.messages.delete(
                        delete_for_all=1,
                        peer_id=2000000000 + chat_id,
                        cmids=message['conversation_message_id']
                    )
                except:
                    pass
                return
        
        # Проверяем команды
        command, args, is_dev = self.parse_message(text)
        
        if command:
            if is_dev:
                if command in self.dev_commands:
                    self.dev_commands[command](event, args)
            else:
                if command in self.commands:
                    self.commands[command](event, args)
                else:
                    # Проверяем кастомные команды
                    db.cursor.execute(
                        "SELECT response FROM custom_commands WHERE chat_id=? AND command=?",
                        (chat_id, command)
                    )
                    custom = db.cursor.fetchone()
                    if custom:
                        self.send_message(chat_id, custom[0])
        
        # Проверяем фильтры (анти-мат, анти-флуд и т.д.)
        self.check_filters(event)

# ========== ЗАПУСК БОТА ==========
def run_bot():
    bot = OrbitManager()
    bot.run()

if __name__ == "__main__":
    # Запускаем Flask на порте 8080 (для Render)
    from threading import Thread
    Thread(target=lambda: app.run(host="0.0.0.0", port=8080, debug=False)).start()
    
    # Запускаем бота
    time.sleep(2)  # Даем Flask запуститься
    run_bot()