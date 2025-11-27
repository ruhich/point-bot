import sqlite3
from datetime import datetime
import logging

class Database:
    def __init__(self, db_name):
        self.conn = sqlite3.connect(db_name)
        self.cursor = self.conn.cursor()
        self._create_tables()

    def _create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                score INTEGER DEFAULT 0,
                last_activity_month TEXT,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS admins (
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL,
                PRIMARY KEY (user_id, chat_id)
            )
        ''')
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS chats (
                chat_id INTEGER PRIMARY KEY,
                chat_name TEXT,
                chat_type TEXT
            )
        """)
        self.cursor.execute("""
            CREATE TABLE IF NOT EXISTS activity_log (
                chat_id INTEGER,
                giver_id INTEGER,
                receiver_id INTEGER,
                score_change INTEGER,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self.conn.commit()

    def add_chat(self, chat_id: int, chat_name: str, chat_type: str):
        try:
            self.cursor.execute('INSERT INTO chats (chat_id, chat_name, chat_type) VALUES (?, ?, ?)',
                                (chat_id, chat_name, chat_type))
            self.conn.commit()
            logging.info(f"Added chat: {chat_name} (ID: {chat_id})")
        except sqlite3.IntegrityError:
            pass

    def get_chats(self):
        self.cursor.execute('SELECT chat_id, chat_name FROM chats')
        return self.cursor.fetchall() 

    async def get_user_score(self, user_id: int, chat_id: int) -> int:
        self.cursor.execute('SELECT score FROM users WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        result = self.cursor.fetchone()
        return result[0] if result else 0

    async def update_user_score(self, user_id: int, chat_id: int, change: int):
        current_month = datetime.now().strftime('%Y-%m')
        self.cursor.execute('''
            INSERT INTO users (user_id, chat_id, score, last_activity_month)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, chat_id) DO UPDATE SET
                score = score + ?,
                last_activity_month = ?
        ''', (user_id, chat_id, change, current_month, change, current_month))
        self.conn.commit()

    async def add_admin(self, user_id: int, chat_id: int):
        self.cursor.execute('INSERT OR IGNORE INTO admins (user_id, chat_id) VALUES (?, ?)', (user_id, chat_id))
        self.conn.commit()

    async def remove_admin(self, user_id: int, chat_id: int):
        self.cursor.execute('DELETE FROM admins WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        self.conn.commit()

    async def is_admin(self, user_id: int, chat_id: int) -> bool:
        self.cursor.execute('SELECT 1 FROM admins WHERE user_id = ? AND chat_id = ?', (user_id, chat_id))
        return self.cursor.fetchone() is not None

    async def get_chat_admins(self, chat_id: int) -> list:
        self.cursor.execute('SELECT user_id FROM admins WHERE chat_id = ?', (chat_id,))
        return [row[0] for row in self.cursor.fetchall()]

    async def get_top_users(self, chat_id: int, limit: int = 10) -> list:
        self.cursor.execute(
            'SELECT user_id, score FROM users WHERE chat_id = ? ORDER BY score DESC LIMIT ?',
            (chat_id, limit)
        )
        return self.cursor.fetchall()

    async def log_activity(self, chat_id: int, giver_id: int, receiver_id: int, score_change: int):
        self.cursor.execute(
            'INSERT INTO activity_log (chat_id, giver_id, receiver_id, score_change) VALUES (?, ?, ?, ?)',
            (chat_id, giver_id, receiver_id, score_change)
        )
        self.conn.commit()

    async def get_monthly_activity(self, chat_id: int, year: int, month: int):
        start_date = datetime(year, month, 1).strftime('%Y-%m-%d %H:%M:%S')
        if month == 12:
            end_date = datetime(year + 1, 1, 1).strftime('%Y-%m-%d %H:%M:%S')
        else:
            end_date = datetime(year, month + 1, 1).strftime('%Y-%m-%d %H:%M:%S')

        self.cursor.execute(
            '''
            SELECT strftime('%Y-%m-%d', timestamp) as day, SUM(score_change)
            FROM activity_log
            WHERE chat_id = ? AND timestamp >= ? AND timestamp < ?
            GROUP BY day
            ORDER BY day
            ''',
            (chat_id, start_date, end_date)
        )
        return self.cursor.fetchall()

    async def reset_monthly_karma_if_needed(self):
        current_month = datetime.now().strftime('%Y-%m')
        self.cursor.execute('SELECT DISTINCT chat_id FROM users')
        chat_ids = [row[0] for row in self.cursor.fetchall()]

        for chat_id in chat_ids:
            self.cursor.execute('SELECT last_activity_month FROM users WHERE chat_id = ? LIMIT 1', (chat_id,))
            last_activity_month = self.cursor.fetchone()
            if last_activity_month and last_activity_month[0] != current_month:
                self.cursor.execute('UPDATE users SET score = 0 WHERE chat_id = ?', (chat_id,))
                self.conn.commit()
                print(f"Karma reset for chat {chat_id} for new month.")
