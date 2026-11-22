"""
认证与接口权限模块（Flask 装饰器版：JWT + RBAC）
-----------------------------------------------
- 密码存储：PBKDF2-HMAC-SHA256 加盐哈希（不存明文密码）
- 登录态：HS256 JWT Bearer Token
- 权限守卫（Python 装饰器）：
    * @jwt_required   -> 校验 Authorization: Bearer <token>，失败返回 401
    * @admin_required -> 在 @jwt_required 基础上校验 role == "admin"，失败返回 403
"""

import hashlib
import hmac
import os
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

import jwt
from flask import g, jsonify, request

DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "users.db"

JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "dev-secret-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = int(os.getenv("JWT_EXPIRE_MINUTES", "120"))


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 100_000)
    return f"{salt}${dk.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt, _ = stored_hash.split("$", 1)
    except ValueError:
        return False
    actual = hash_password(password, salt)
    return hmac.compare_digest(actual, stored_hash)


def init_user_db() -> None:
    """初始化 SQLite 用户表，并预置两个演练账号（admin / researcher）"""
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'researcher',
                created_at TEXT NOT NULL
            )
            """
        )
        now = datetime.now(timezone.utc).isoformat()
        defaults = [
            ("admin", "admin123", "admin"),
            ("researcher", "user123", "researcher"),
        ]
        for username, raw_pwd, role in defaults:
            exists = conn.execute(
                "SELECT 1 FROM users WHERE username = ?", (username,)
            ).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                    (username, hash_password(raw_pwd), role, now),
                )
        conn.commit()


def authenticate_user(username: str, password: str) -> dict | None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT id, username, password_hash, role, created_at FROM users WHERE username = ?",
            (username,),
        ).fetchone()
    if not row or not verify_password(password, row["password_hash"]):
        return None
    return {
        "id": row["id"],
        "username": row["username"],
        "role": row["role"],
        "created_at": row["created_at"],
    }


def register_user(username: str, password: str, role: str = "researcher") -> dict | None:
    now = datetime.now(timezone.utc).isoformat()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cur = conn.execute(
                "INSERT INTO users (username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
                (username, hash_password(password), role, now),
            )
            conn.commit()
            user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    return {"id": user_id, "username": username, "role": role, "created_at": now}


def list_all_users() -> list[dict]:
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, username, role, created_at FROM users ORDER BY id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def create_access_token(username: str, role: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=JWT_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "role": role,
        "exp": expire,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def jwt_required(fn):
    """
    【登录权限装饰器】
    从请求头提取 Authorization: Bearer <token>，校验通过后把用户信息挂到 flask.g.current_user。
    未带 Token 或 Token 无效/过期 -> 返回 401 Unauthorized。
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            return jsonify({"detail": "未登录或缺少 Authorization Bearer Token"}), 401
        token = auth_header[len("Bearer "):].strip()
        try:
            payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
            username = payload.get("sub")
            role = payload.get("role")
            if not username or not role:
                return jsonify({"detail": "Token 载荷不完整"}), 401
            g.current_user = {"username": username, "role": role}
        except jwt.ExpiredSignatureError:
            return jsonify({"detail": "登录已过期，请重新登录"}), 401
        except jwt.InvalidTokenError:
            return jsonify({"detail": "无效的 Token"}), 401
        return fn(*args, **kwargs)
    return wrapper


def admin_required(fn):
    """
    【管理员接口权限装饰器】
    先校验登录（401），再校验角色必须是 admin，否则返回 403 Forbidden。
    """
    @wraps(fn)
    @jwt_required
    def wrapper(*args, **kwargs):
        if g.current_user.get("role") != "admin":
            return jsonify({"detail": "接口权限不足：该操作仅限管理员（admin）执行"}), 403
        return fn(*args, **kwargs)
    return wrapper
