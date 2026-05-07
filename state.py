import sqlite3
import json
import os
from datetime import datetime
from PyQt6.QtCore import QObject, pyqtSignal  # <--- ДОДАНО

class SessionState(QObject):  # <--- УСПАДКУВАННЯ ВІД QObject
    # Сигнали визначаються на рівні класу
    template_changed = pyqtSignal()

    def __init__(self, db_path=".eplan_cache.db"):
        super().__init__()  # Ініціалізація базового класу QObject
        self.db_path = db_path
        
        # --- Змінні файлової системи ---
        self.pdf_path = None
        self.templates_dir = ""
        self.page_num = 0
        
        # --- Змінні UI стану ---
        self.current_mode = "CONFIG"
        self.template_data = {}
        
        # --- Кеш бази даних ---
        self.session_cache = {}  # {page_idx: [FoundObject_dict, ...]}
        
        self.init_db()

    # --- МЕТОДИ СИНХРОНІЗАЦІЇ UI ---
    def update_template(self, new_data):
        """Оновлює поточний шаблон і повідомляє інтерфейс"""
        self.template_data = new_data
        self.template_changed.emit()

    def set_mode(self, mode):
        self.current_mode = mode

    # -----------------------------------------------------------
    # Нижче залишаються всі ваші існуючі методи для SQLite
    # def init_db(self): ...
    # def sync_page_to_db(self, page_index, objects_list, status="pending"): ...
    # ...


    def sync_page_to_db(self, page_num, objects_dicts, status="saved"):
        """Зберігає дані сторінки в правильну таблицю та запам'ятовує останній PDF."""
        import json
        page_key = str(page_num) # ПРИМУСОВО STR
        self.session_cache[page_key] = objects_dicts
        
        # Зберігаємо шлях до PDF, щоб знати, що відновлювати
        if self.pdf_path:
            self.save_setting("last_pdf_path", self.pdf_path)

        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT OR REPLACE INTO page_cache (page_index, objects_json, status, timestamp) VALUES (?, ?, ?, ?)",
                    (int(page_num), json.dumps(objects_dicts), status, datetime.now().isoformat())
                )
                conn.commit()
        except Exception as e:
            print(f"[State] Помилка sync_page_to_db: {e}")

    def load_all_from_db(self):
        """Завантажує весь кеш, перетворюючи індекси на str."""
        self.session_cache = {}
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT page_index, objects_json FROM page_cache")
                for row in cursor.fetchall():
                    # КРИТИЧНО: row[0] (int) стає 'str' ключем
                    self.session_cache[str(row[0])] = json.loads(row[1])
        except Exception as e:
            print(f"[State] Помилка load_all_from_db: {e}")

    def check_recovery(self):
        """Повертає останню сторінку та шлях до PDF."""
        self.load_all_from_db()
        if not self.session_cache:
            return None
        
        last_page = max(self.session_cache.keys(), key=int)
        last_pdf = self.load_setting("last_pdf_path")
        return {"page": last_page, "pdf_path": last_pdf}

    def get_page_objects(self, page_num) -> list:
        """Безпечне отримання об'єктів сторінки з кешу (str або int ключ)."""
        return (self.session_cache.get(str(page_num)) or
                self.session_cache.get(int(page_num)) or [])

    def set_page_objects(self, page_num, objects_list, save_to_db=True, status="pending"):
        """Безпечне збереження об'єктів сторінки — завжди str ключ."""
        page_key = str(page_num)
        self.session_cache[page_key] = objects_list
        if save_to_db:
            self.sync_page_to_db(page_num, objects_list, status)



    def clear_cache(self):
        """Повне очищення кешу об'єктів при відкритті нового файлу. 
        Налаштування UI (кольори, шари) в app_settings залишаються!"""
        self.session_cache = {}
        if os.path.exists(self.db_path):
            try:
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    # Очищаємо лише знайдені рамки старого документа
                    cursor.execute("DELETE FROM page_cache")
                    # Рядок 'DELETE FROM session_info' видалено, бо таблиці більше немає
                    conn.commit()
            except Exception as e:
                print(f"Помилка очищення БД: {e}")
    def init_db(self):
        """Ініціалізація SQLite таблиць для кешування"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                
                # Таблиця для збереження знайдених об'єктів по сторінках
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS page_cache (
                        page_index INTEGER PRIMARY KEY,
                        objects_json TEXT,
                        status TEXT,
                        timestamp TIMESTAMP
                    )
                """)
                
                # Таблиця для метаданих сесії та налаштувань UI (Кольори, Шари)
                # Ми об'єднали session_info та app_settings в одну універсальну таблицю
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS app_settings (
                        key TEXT PRIMARY KEY, 
                        value TEXT
                    )
                """)
                
                conn.commit()
        except Exception as e:
            print(f"Помилка ініціалізації БД: {e}")

    def save_setting(self, key, value):
        import json
        val_json = json.dumps(value)
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)", (key, val_json))
                conn.commit()
            # Інвалідуємо кеш
            if hasattr(self, '_settings_cache'):
                self._settings_cache[key] = value
        except Exception as e:
            print(f"[State] Помилка збереження налаштування {key}: {e}")

    def load_setting(self, key, default=None):
        import json
        # Кеш налаштувань в пам'яті — уникаємо повторних SQLite-запитів
        if not hasattr(self, '_settings_cache'):
            self._settings_cache = {}
        if key in self._settings_cache:
            return self._settings_cache[key]
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM app_settings WHERE key = ?", (key,))
                row = cursor.fetchone()
                val = json.loads(row[0]) if row else default
                self._settings_cache[key] = val
                return val
        except Exception:
            return default
    def export_session(self, file_path):
        """Зберігає сесію у файл .epss (JSON)."""
        import json
        session_data = {
            "version": "2.0",
            "pdf_path": self.pdf_path,
            "templates_dir": self.templates_dir,
            "page_num": self.page_num,
            "session_cache": {str(k): v for k, v in self.session_cache.items()}
        }
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(session_data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            print(f"[State] Помилка збереження сесії: {e}")
            return False

    def import_session(self, file_path):
        """Завантажує сесію з файлу .epss (JSON). Повертає dict або None."""
        import json
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get("version") != "2.0":
                print(f"[State] Невідома версія сесії: {data.get('version')}")
                return None
            return data
        except Exception as e:
            print(f"[State] Помилка читання сесії: {e}")
            return None