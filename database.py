import sqlite3
import json
import threading
from datetime import datetime
from config import DATABASE_FILE

class Database:
    _instance = None
    _lock = threading.Lock()
    
    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._init_db()
            return cls._instance
    
    def _init_db(self):
        self.conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.cursor = self.conn.cursor()
        self._create_tables()
    
    def _create_tables(self):
        """Создание всех таблиц"""
        # Чаты
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                owner_id INTEGER,
                settings TEXT DEFAULT '{}',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Права пользователей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS user_perms (
                user_id INTEGER,
                chat_id INTEGER,
                level INTEGER DEFAULT 2,
                warns INTEGER DEFAULT 0,
                reputation INTEGER DEFAULT 0,
                muted_until TIMESTAMP,
                banned_until TIMESTAMP,
                last_message TIMESTAMP,
                message_count INTEGER DEFAULT 0,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        
        # Логи действий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                action TEXT,
                target_id INTEGER,
                reason TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Кастомные команды
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS custom_commands (
                chat_id INTEGER,
                command TEXT,
                response TEXT,
                created_by INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (chat_id, command)
            )
        ''')
        
        self.conn.commit()
    
    def get_user_level(self, user_id: int, chat_id: int) -> int:
        """Получить уровень прав пользователя"""
        from config import DEV_USER_IDS
        if user_id in DEV_USER_IDS:
            return 999
        
        self.cursor.execute(
            "SELECT level FROM user_perms WHERE user_id = ? AND chat_id = ?",
            (user_id, chat_id)
        )
        result = self.cursor.fetchone()
        
        if result:
            return result['level']
        
        # Проверяем, владелец ли чата
        self.cursor.execute(
            "SELECT owner_id FROM chats WHERE chat_id = ?",
            (chat_id,)
        )
        chat = self.cursor.fetchone()
        if chat and chat['owner_id'] == user_id:
            self.set_user_level(user_id, chat_id, 7)
            return 7
        
        return 2
    
    def set_user_level(self, user_id: int, chat_id: int, level: int):
        """Установить уровень прав"""
        if level == 7:
            self.cursor.execute(
                "UPDATE chats SET owner_id = ? WHERE chat_id = ?",
                (user_id, chat_id)
            )
        
        self.cursor.execute('''
            INSERT OR REPLACE INTO user_perms (user_id, chat_id, level)
            VALUES (?, ?, ?)
        ''', (user_id, chat_id, level))
        self.conn.commit()
    
    def add_warn(self, user_id: int, chat_id: int) -> int:
        """Добавить варн пользователю"""
        self.cursor