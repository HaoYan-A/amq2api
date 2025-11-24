"""
账号管理模块
负责多账号的数据库操作和管理
"""
import sqlite3
import json
import uuid
import time
import random
from pathlib import Path
from typing import Dict, List, Any, Optional
from datetime import datetime

# 数据库路径
# 优先使用 /app/data 目录（Docker 卷），否则使用当前目录
import os
if os.path.exists("/app/data"):
    DB_PATH = Path("/app/data/accounts.db")
else:
    DB_PATH = Path(__file__).parent / "accounts.db"


def _ensure_db():
    """初始化数据库表结构"""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS accounts (
                id TEXT PRIMARY KEY,
                label TEXT,
                clientId TEXT,
                clientSecret TEXT,
                refreshToken TEXT,
                accessToken TEXT,
                other TEXT,
                last_refresh_time TEXT,
                last_refresh_status TEXT,
                created_at TEXT,
                updated_at TEXT,
                enabled INTEGER DEFAULT 1,
                type TEXT DEFAULT 'amazonq'
            )
            """
        )

        # 迁移：为已存在的表添加 type 字段
        cursor = conn.execute("PRAGMA table_info(accounts)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'type' not in columns:
            conn.execute("ALTER TABLE accounts ADD COLUMN type TEXT DEFAULT 'amazonq'")

        conn.commit()


def _conn() -> sqlite3.Connection:
    """创建数据库连接"""
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _row_to_dict(r: sqlite3.Row) -> Dict[str, Any]:
    """将数据库行转换为字典"""
    d = dict(r)
    if d.get("other"):
        try:
            d["other"] = json.loads(d["other"])
        except Exception:
            pass
    if "enabled" in d and d["enabled"] is not None:
        d["enabled"] = bool(int(d["enabled"]))
    return d


def list_enabled_accounts(account_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """获取所有启用的账号"""
    with _conn() as conn:
        if account_type:
            rows = conn.execute("SELECT * FROM accounts WHERE enabled=1 AND type=? ORDER BY created_at DESC", (account_type,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM accounts WHERE enabled=1 ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


def get_random_account(account_type: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """随机选择一个启用的账号"""
    accounts = list_enabled_accounts(account_type)
    if not accounts:
        return None
    return random.choice(accounts)


def get_random_channel_by_model(model: str) -> Optional[str]:
    """根据模型智能选择渠道（按账号数量加权）

    Args:
        model: 请求的模型名称

    Returns:
        渠道名称 ('amazonq' 或 'gemini')，如果没有可用账号则返回 None
    """
    # Gemini 独占模型
    gemini_only_models = ['gemini-2.0-flash-exp']

    # 如果是 Gemini 独占模型，只返回 gemini 渠道
    if model in gemini_only_models:
        gemini_accounts = list_enabled_accounts(account_type='gemini')
        if gemini_accounts:
            return 'gemini'
        return None

    # 对于其他模型（两个渠道都支持），按账号数量加权随机选择
    amazonq_accounts = list_enabled_accounts(account_type='amazonq')
    gemini_accounts = list_enabled_accounts(account_type='gemini')

    amazonq_count = len(amazonq_accounts)
    gemini_count = len(gemini_accounts)

    # 如果没有任何可用账号
    if amazonq_count == 0 and gemini_count == 0:
        return None

    # 如果只有一个渠道有账号
    if amazonq_count == 0:
        return 'gemini'
    if gemini_count == 0:
        return 'amazonq'

    # 按账号数量加权随机选择
    total = amazonq_count + gemini_count
    rand = random.randint(1, total)

    if rand <= amazonq_count:
        return 'amazonq'
    else:
        return 'gemini'


def get_account(account_id: str) -> Optional[Dict[str, Any]]:
    """根据ID获取账号"""
    with _conn() as conn:
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if not row:
            return None
        return _row_to_dict(row)


def create_account(
    label: Optional[str],
    client_id: str,
    client_secret: str,
    refresh_token: Optional[str] = None,
    access_token: Optional[str] = None,
    other: Optional[Dict[str, Any]] = None,
    enabled: bool = True,
    account_type: str = "amazonq"
) -> Dict[str, Any]:
    """创建新账号"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    acc_id = str(uuid.uuid4())
    other_str = json.dumps(other, ensure_ascii=False) if other else None

    with _conn() as conn:
        conn.execute(
            """
            INSERT INTO accounts (id, label, clientId, clientSecret, refreshToken, accessToken, other, last_refresh_time, last_refresh_status, created_at, updated_at, enabled, type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (acc_id, label, client_id, client_secret, refresh_token, access_token, other_str, None, "never", now, now, 1 if enabled else 0, account_type)
        )
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (acc_id,)).fetchone()
        return _row_to_dict(row)


def update_account(
    account_id: str,
    label: Optional[str] = None,
    client_id: Optional[str] = None,
    client_secret: Optional[str] = None,
    refresh_token: Optional[str] = None,
    access_token: Optional[str] = None,
    other: Optional[Dict[str, Any]] = None,
    enabled: Optional[bool] = None
) -> Optional[Dict[str, Any]]:
    """更新账号信息"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    fields = []
    values: List[Any] = []

    if label is not None:
        fields.append("label=?")
        values.append(label)
    if client_id is not None:
        fields.append("clientId=?")
        values.append(client_id)
    if client_secret is not None:
        fields.append("clientSecret=?")
        values.append(client_secret)
    if refresh_token is not None:
        fields.append("refreshToken=?")
        values.append(refresh_token)
    if access_token is not None:
        fields.append("accessToken=?")
        values.append(access_token)
    if other is not None:
        fields.append("other=?")
        values.append(json.dumps(other, ensure_ascii=False))
    if enabled is not None:
        fields.append("enabled=?")
        values.append(1 if enabled else 0)

    if not fields:
        return get_account(account_id)

    fields.append("updated_at=?")
    values.append(now)
    values.append(account_id)

    with _conn() as conn:
        cur = conn.execute(f"UPDATE accounts SET {', '.join(fields)} WHERE id=?", values)
        conn.commit()
        if cur.rowcount == 0:
            return None
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return _row_to_dict(row)


def update_account_tokens(
    account_id: str,
    access_token: str,
    refresh_token: Optional[str] = None,
    status: str = "success"
) -> Optional[Dict[str, Any]]:
    """更新账号的 token 信息"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())

    with _conn() as conn:
        if refresh_token:
            conn.execute(
                """
                UPDATE accounts
                SET accessToken=?, refreshToken=?, last_refresh_time=?, last_refresh_status=?, updated_at=?
                WHERE id=?
                """,
                (access_token, refresh_token, now, status, now, account_id)
            )
        else:
            conn.execute(
                """
                UPDATE accounts
                SET accessToken=?, last_refresh_time=?, last_refresh_status=?, updated_at=?
                WHERE id=?
                """,
                (access_token, now, status, now, account_id)
            )
        conn.commit()
        row = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        return _row_to_dict(row) if row else None


def update_refresh_status(account_id: str, status: str) -> None:
    """更新账号的刷新状态"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
    with _conn() as conn:
        conn.execute(
            "UPDATE accounts SET last_refresh_time=?, last_refresh_status=?, updated_at=? WHERE id=?",
            (now, status, now, account_id)
        )
        conn.commit()


def delete_account(account_id: str) -> bool:
    """删除账号"""
    with _conn() as conn:
        cur = conn.execute("DELETE FROM accounts WHERE id=?", (account_id,))
        conn.commit()
        return cur.rowcount > 0


def list_all_accounts() -> List[Dict[str, Any]]:
    """获取所有账号"""
    with _conn() as conn:
        rows = conn.execute("SELECT * FROM accounts ORDER BY created_at DESC").fetchall()
        return [_row_to_dict(r) for r in rows]


# 初始化数据库
_ensure_db()