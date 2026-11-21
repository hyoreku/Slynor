"""Local owner-specific settings, job references and Telegram file IDs."""
from pathlib import Path
import sqlite3
from time import time

CACHE_SECONDS = 7 * 24 * 60 * 60


class BotState:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS preferences (
                name TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                track_key TEXT,
                created REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telegram_files (
                track_key TEXT PRIMARY KEY,
                file_id TEXT NOT NULL,
                mode TEXT NOT NULL,
                expires REAL NOT NULL
            );
        """)

    def close(self):
        self.connection.close()

    def get(self, name, default):
        row = self.connection.execute(
            'SELECT value FROM preferences WHERE name=?', (name,)).fetchone()
        return row[0] if row else default

    def set(self, name, value):
        with self.connection:
            self.connection.execute('INSERT OR REPLACE INTO preferences VALUES (?,?)', (name, value))

    def add_job(self, identifier, kind, track_key=None):
        with self.connection:
            self.connection.execute('INSERT INTO jobs VALUES (?,?,?,?)',
                                    (identifier, kind, track_key, time()))
            self.connection.execute("""
                DELETE FROM jobs WHERE id IN (
                    SELECT id FROM jobs ORDER BY created DESC, rowid DESC LIMIT -1 OFFSET 200
                )
            """)

    def job_for(self, track_key):
        row = self.connection.execute("""
            SELECT id FROM jobs WHERE track_key=? AND created>? ORDER BY created DESC LIMIT 1
        """, (track_key, time() - CACHE_SECONDS)).fetchone()
        return row[0] if row else None

    def recent_jobs(self):
        return self.connection.execute(
            'SELECT id, kind FROM jobs ORDER BY created DESC LIMIT 6').fetchall()

    def owns_job(self, identifier):
        return self.connection.execute('SELECT 1 FROM jobs WHERE id=?', (identifier,)).fetchone() is not None

    def forget_job(self, identifier):
        with self.connection:
            self.connection.execute('UPDATE jobs SET track_key=NULL WHERE id=?', (identifier,))

    def get_file(self, track_key):
        with self.connection:
            self.connection.execute('DELETE FROM telegram_files WHERE expires<=?', (time(),))
        return self.connection.execute(
            'SELECT file_id, mode FROM telegram_files WHERE track_key=?', (track_key,)).fetchone()

    def save_file(self, track_key, file_id, mode):
        with self.connection:
            self.connection.execute('INSERT OR REPLACE INTO telegram_files VALUES (?,?,?,?)',
                                    (track_key, file_id, mode, time() + CACHE_SECONDS))
            self.connection.execute("""
                DELETE FROM telegram_files WHERE track_key IN (
                    SELECT track_key FROM telegram_files ORDER BY expires DESC LIMIT -1 OFFSET 200
                )
            """)

    def forget_file(self, track_key):
        with self.connection:
            self.connection.execute('DELETE FROM telegram_files WHERE track_key=?', (track_key,))
