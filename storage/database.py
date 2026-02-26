"""SQLite 持久化层 — 账户、任务、会话状态"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from models.schemas import Account, AccountStatus, NoteType, ProductInfo, PublishTask, TaskStatus


class Database:
    def __init__(self, db_path: str = "./data/xhs.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_tables(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    nickname TEXT NOT NULL,
                    xhs_user_id TEXT DEFAULT '',
                    xhs_nickname TEXT DEFAULT '',
                    proxy TEXT DEFAULT '',
                    persona TEXT DEFAULT '',
                    status TEXT DEFAULT 'offline',
                    state_path TEXT DEFAULT '',
                    last_health_check TEXT,
                    consecutive_failures INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id INTEGER NOT NULL,
                    title TEXT DEFAULT '',
                    content TEXT DEFAULT '',
                    tags TEXT DEFAULT '[]',
                    image_paths TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'pending',
                    error_msg TEXT DEFAULT '',
                    verified_user_id TEXT DEFAULT '',
                    verified_proxy_ip TEXT DEFAULT '',
                    scheduled_at TEXT,
                    published_at TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    note_type TEXT DEFAULT 'normal',
                    products TEXT DEFAULT '[]',
                    FOREIGN KEY (account_id) REFERENCES accounts(id)
                );

                CREATE INDEX IF NOT EXISTS idx_tasks_account ON tasks(account_id);
                CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            """)
            # 兼容旧库: 若 tasks 表缺少新字段则自动添加
            self._migrate_tasks_table(conn)

    def _migrate_tasks_table(self, conn):
        """自动迁移: 为旧的 tasks 表添加 note_type / products 字段"""
        cursor = conn.execute("PRAGMA table_info(tasks)")
        columns = {row[1] for row in cursor.fetchall()}
        if "note_type" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN note_type TEXT DEFAULT 'normal'")
        if "products" not in columns:
            conn.execute("ALTER TABLE tasks ADD COLUMN products TEXT DEFAULT '[]'")

    # ── 账户操作 ──

    def add_account(self, account: Account) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO accounts (nickname, proxy, persona, status, state_path) "
                "VALUES (?, ?, ?, ?, ?)",
                (account.nickname, account.proxy, account.persona,
                 account.status.value, account.state_path),
            )
            return cur.lastrowid

    def get_account(self, account_id: int) -> Optional[Account]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
            return self._row_to_account(row) if row else None

    def get_all_accounts(self) -> list[Account]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM accounts ORDER BY id").fetchall()
            return [self._row_to_account(r) for r in rows]

    def get_online_accounts(self) -> list[Account]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM accounts WHERE status = ?",
                (AccountStatus.ONLINE.value,),
            ).fetchall()
            return [self._row_to_account(r) for r in rows]

    def update_account(self, account: Account):
        with self._conn() as conn:
            conn.execute(
                "UPDATE accounts SET nickname=?, xhs_user_id=?, xhs_nickname=?, "
                "proxy=?, persona=?, status=?, state_path=?, last_health_check=?, "
                "consecutive_failures=? WHERE id=?",
                (account.nickname, account.xhs_user_id, account.xhs_nickname,
                 account.proxy, account.persona, account.status.value,
                 account.state_path,
                 account.last_health_check.isoformat() if account.last_health_check else None,
                 account.consecutive_failures, account.id),
            )

    def delete_account(self, account_id: int):
        with self._conn() as conn:
            conn.execute("DELETE FROM tasks WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))

    # ── 任务操作 ──

    def add_task(self, task: PublishTask) -> int:
        products_data = [
            {"keyword": p.keyword, "product_id": p.product_id,
             "product_name": p.product_name, "product_url": p.product_url}
            for p in task.products
        ]
        with self._conn() as conn:
            cur = conn.execute(
                "INSERT INTO tasks (account_id, title, content, tags, image_paths, "
                "status, scheduled_at, note_type, products) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (task.account_id, task.title, task.content,
                 json.dumps(task.tags, ensure_ascii=False),
                 json.dumps(task.image_paths, ensure_ascii=False),
                 task.status.value,
                 task.scheduled_at.isoformat() if task.scheduled_at else None,
                 task.note_type.value,
                 json.dumps(products_data, ensure_ascii=False)),
            )
            return cur.lastrowid

    def get_pending_tasks(self, account_id: Optional[int] = None) -> list[PublishTask]:
        with self._conn() as conn:
            if account_id:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE account_id = ? AND status IN (?, ?) "
                    "ORDER BY scheduled_at",
                    (account_id, TaskStatus.PENDING.value, TaskStatus.READY.value),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM tasks WHERE status IN (?, ?) ORDER BY scheduled_at",
                    (TaskStatus.PENDING.value, TaskStatus.READY.value),
                ).fetchall()
            return [self._row_to_task(r) for r in rows]

    def update_task(self, task: PublishTask):
        products_data = [
            {"keyword": p.keyword, "product_id": p.product_id,
             "product_name": p.product_name, "product_url": p.product_url}
            for p in task.products
        ]
        with self._conn() as conn:
            conn.execute(
                "UPDATE tasks SET title=?, content=?, tags=?, image_paths=?, "
                "status=?, error_msg=?, verified_user_id=?, verified_proxy_ip=?, "
                "published_at=?, note_type=?, products=? WHERE id=?",
                (task.title, task.content,
                 json.dumps(task.tags, ensure_ascii=False),
                 json.dumps(task.image_paths, ensure_ascii=False),
                 task.status.value, task.error_msg,
                 task.verified_user_id, task.verified_proxy_ip,
                 task.published_at.isoformat() if task.published_at else None,
                 task.note_type.value,
                 json.dumps(products_data, ensure_ascii=False),
                 task.id),
            )

    def get_today_published_count(self, account_id: int) -> int:
        today = datetime.now().strftime("%Y-%m-%d")
        with self._conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) as cnt FROM tasks "
                "WHERE account_id = ? AND status = ? AND published_at LIKE ?",
                (account_id, TaskStatus.PUBLISHED.value, f"{today}%"),
            ).fetchone()
            return row["cnt"]

    # ── 序列化 ──

    @staticmethod
    def _row_to_account(row: sqlite3.Row) -> Account:
        return Account(
            id=row["id"],
            nickname=row["nickname"],
            xhs_user_id=row["xhs_user_id"],
            xhs_nickname=row["xhs_nickname"],
            proxy=row["proxy"],
            persona=row["persona"],
            status=AccountStatus(row["status"]),
            state_path=row["state_path"],
            last_health_check=(
                datetime.fromisoformat(row["last_health_check"])
                if row["last_health_check"] else None
            ),
            consecutive_failures=row["consecutive_failures"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _row_to_task(row: sqlite3.Row) -> PublishTask:
        # 解析商品列表
        raw_products = json.loads(row["products"]) if row["products"] else []
        products = [
            ProductInfo(
                keyword=p.get("keyword", ""),
                product_id=p.get("product_id", ""),
                product_name=p.get("product_name", ""),
                product_url=p.get("product_url", ""),
            )
            for p in raw_products
        ]
        return PublishTask(
            id=row["id"],
            account_id=row["account_id"],
            title=row["title"],
            content=row["content"],
            tags=json.loads(row["tags"]),
            image_paths=json.loads(row["image_paths"]),
            status=TaskStatus(row["status"]),
            error_msg=row["error_msg"],
            verified_user_id=row["verified_user_id"],
            verified_proxy_ip=row["verified_proxy_ip"],
            scheduled_at=(
                datetime.fromisoformat(row["scheduled_at"])
                if row["scheduled_at"] else None
            ),
            published_at=(
                datetime.fromisoformat(row["published_at"])
                if row["published_at"] else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            note_type=NoteType(row["note_type"]) if row["note_type"] else NoteType.NORMAL,
            products=products,
        )
