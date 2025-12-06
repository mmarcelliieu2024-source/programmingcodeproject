
# db.py — SQLite: init, CRUD, auth, PBKDF2 hashing + items entity + Google OAuth
import sqlite3, os, hashlib, hmac
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd

DB_PATH = Path("app.db")

# PBKDF2 hashing
PBKDF2_ALGO = "sha256"
PBKDF2_ITERATIONS = 200_000
SALT_BYTES = 16


def get_conn():
    return sqlite3.connect(DB_PATH)


def db_path() -> str:
    return str(DB_PATH.resolve())


# =========================
# Init DB (users + items)
# =========================
def init_db():
    with get_conn() as conn:
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass
        c = conn.cursor()
        # Users table
        c.execute("""
                  CREATE TABLE IF NOT EXISTS users
                  (
                      id
                      INTEGER
                      PRIMARY
                      KEY
                      AUTOINCREMENT,
                      username
                      TEXT
                      UNIQUE
                      NOT
                      NULL,
                      role
                      TEXT
                      NOT
                      NULL
                      DEFAULT
                      'Viewer',
                      salt_hex
                      TEXT,
                      password_hash_hex
                      TEXT,
                      failed_attempts
                      INTEGER
                      NOT
                      NULL
                      DEFAULT
                      0,
                      lock_until
                      TEXT,
                      password_last_set
                      TEXT,
                      google_id
                      TEXT
                      UNIQUE,
                      email
                      TEXT,
                      profile_picture
                      TEXT,
                      auth_provider
                      TEXT
                      DEFAULT
                      'local'
                      CHECK (
                      auth_provider
                      IN
                  (
                      'local',
                      'google'
                  )),
                      created_at TEXT DEFAULT
                  (
                      datetime
                  (
                      'now'
                  )),
                      updated_at TEXT
                      )
                  """)
        # Items table (SpendSense)
        c.execute("""
                  CREATE TABLE IF NOT EXISTS items
                  (
                      id
                      INTEGER
                      PRIMARY
                      KEY
                      AUTOINCREMENT,
                      created_by
                      TEXT
                      NOT
                      NULL,
                      source
                      TEXT
                      CHECK (
                      source
                      IN
                  (
                      'original',
                      'second_hand'
                  )) NOT NULL DEFAULT 'original',
                      title TEXT NOT NULL,
                      brand TEXT,
                      price REAL NOT NULL,
                      origin TEXT,
                      material TEXT,
                      category TEXT,
                      image_path TEXT,
                      label_image_path TEXT,
                      co2_estimate REAL,
                      co2_level TEXT CHECK
                  (
                      co2_level
                      IN
                  (
                      'low',
                      'medium',
                      'high'
                  )),
                      status TEXT CHECK
                  (
                      status
                      IN
                  (
                      'in_cart',
                      'positive',
                      'negative'
                  )) NOT NULL DEFAULT 'in_cart',
                      action_type TEXT CHECK
                  (
                      action_type
                      IN
                  (
                      'none',
                      'bought_original',
                      'saved_money',
                      'bought_second_hand'
                  )) NOT NULL DEFAULT 'none',
                      second_hand_price REAL,
                      savings REAL,
                      color TEXT,
                      confidence REAL,
                      created_at TEXT DEFAULT
                  (
                      datetime
                  (
                      'now'
                  )),
                      updated_at TEXT
                      )
                  """)
        conn.commit()


# =========================
# Hash helpers
# =========================
def _hash_password(password: str) -> tuple[str, str]:
    salt = os.urandom(SALT_BYTES)
    pwd = hashlib.pbkdf2_hmac(PBKDF2_ALGO, password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return salt.hex(), pwd.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)
    got = hashlib.pbkdf2_hmac(PBKDF2_ALGO, password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return hmac.compare_digest(got, expected)


# =========================
# Users CRUD / Auth
# =========================
def _user_row_to_dict(row):
    if not row:
        return None
    cols = ["id", "username", "role", "salt_hex", "password_hash_hex", "failed_attempts",
            "lock_until", "password_last_set", "google_id", "email", "profile_picture",
            "auth_provider", "created_at", "updated_at"]
    return dict(zip(cols, row))


def get_user(username: str) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id,
                           username,
                           role,
                           salt_hex,
                           password_hash_hex,
                           failed_attempts,
                           lock_until,
                           password_last_set,
                           google_id,
                           email,
                           profile_picture,
                           auth_provider,
                           created_at,
                           updated_at
                    FROM users
                    WHERE username = ?
                    """, (username,))
        return _user_row_to_dict(cur.fetchone())


def get_user_by_email(email: str) -> dict | None:
    """Get user by email address."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id,
                           username,
                           role,
                           salt_hex,
                           password_hash_hex,
                           failed_attempts,
                           lock_until,
                           password_last_set,
                           google_id,
                           email,
                           profile_picture,
                           auth_provider,
                           created_at,
                           updated_at
                    FROM users
                    WHERE email = ?
                    """, (email,))
        return _user_row_to_dict(cur.fetchone())


def get_user_by_google_id(google_id: str) -> dict | None:
    """Get user by Google ID."""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id,
                           username,
                           role,
                           salt_hex,
                           password_hash_hex,
                           failed_attempts,
                           lock_until,
                           password_last_set,
                           google_id,
                           email,
                           profile_picture,
                           auth_provider,
                           created_at,
                           updated_at
                    FROM users
                    WHERE google_id = ?
                    """, (google_id,))
        return _user_row_to_dict(cur.fetchone())


def create_user(username: str, password: str, role: str = "Viewer",
                email: str = None, auth_provider: str = "local") -> tuple[bool, str | None]:
    try:
        salt, pwh = _hash_password(password)
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                        INSERT INTO users (username, role, salt_hex, password_hash_hex,
                                           failed_attempts, lock_until, password_last_set,
                                           email, auth_provider, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 0, NULL, ?, ?, ?, ?, ?)
                        """, (username, role, salt, pwh, now, email, auth_provider, now, now))
            conn.commit()
        return True, None
    except sqlite3.IntegrityError:
        return False, "User already exists."
    except Exception as e:
        return False, f"Error creating user: {e}"


def create_google_user(google_id: str, email: str, name: str,
                       picture: str = None, role: str = "Viewer") -> tuple[bool, str | None, dict | None]:
    """
    Create a new user from Google OAuth.
    Returns: (success, error_message, user_dict)
    """
    try:
        # Generate username from email or name
        username = email.split('@')[0] if email else name.replace(' ', '_').lower()

        # Check if username exists, add suffix if needed
        base_username = username
        counter = 1
        with get_conn() as conn:
            cur = conn.cursor()
            while True:
                cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
                if not cur.fetchone():
                    break
                username = f"{base_username}{counter}"
                counter += 1

        now = datetime.now(timezone.utc).isoformat()

        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                        INSERT INTO users (username, role, google_id, email, profile_picture,
                                           auth_provider, failed_attempts, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'google', 0, ?, ?)
                        """, (username, role, google_id, email, picture, now, now))
            conn.commit()

        user = get_user_by_google_id(google_id)
        return True, None, user

    except sqlite3.IntegrityError as e:
        return False, f"User with this Google account already exists: {e}", None
    except Exception as e:
        return False, f"Error creating Google user: {e}", None


def authenticate(username: str, password: str) -> tuple[bool, dict | None]:
    u = get_user(username)
    if not u:
        return False, None

    # Check if user uses Google OAuth
    if u.get("auth_provider") == "google":
        return False, None  # Cannot authenticate Google users with password

    # Check if password fields exist (for local auth)
    if not u.get("salt_hex") or not u.get("password_hash_hex"):
        return False, None

    ok = _verify_password(password, u["salt_hex"], u["password_hash_hex"])
    return ok, u if ok else None


def register_failed_attempt(username: str, max_attempts: int, lock_minutes: int):
    u = get_user(username)
    if not u:
        return
    fa = int(u.get("failed_attempts") or 0) + 1
    lock_until = u.get("lock_until")
    if fa >= max_attempts:
        lock_until = (datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    UPDATE users
                    SET failed_attempts = ?,
                        lock_until      = ?,
                        updated_at      = ?
                    WHERE username = ?
                    """, (fa, lock_until, datetime.now(timezone.utc).isoformat(), username))
        conn.commit()


def reset_failed_attempts(username: str):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    UPDATE users
                    SET failed_attempts = 0,
                        lock_until      = NULL,
                        updated_at      = ?
                    WHERE username = ?
                    """, (datetime.now(timezone.utc).isoformat(), username))
        conn.commit()


def set_new_password(username: str, new_password: str):
    salt, pwh = _hash_password(new_password)
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    UPDATE users
                    SET salt_hex          = ?,
                        password_hash_hex = ?,
                        password_last_set = ?,
                        updated_at        = ?
                    WHERE username = ?
                    """, (salt, pwh, now, now, username))
        conn.commit()


def update_user_profile(username: str, **fields):
    """Update user profile fields."""
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    keys = ", ".join([f"{k} = ?" for k in fields.keys()])
    vals = list(fields.values())
    vals.append(username)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET {keys} WHERE username = ?", vals)
        conn.commit()


def seed_initial_users(seed: dict):
    with get_conn() as conn:
        cur = conn.cursor()
        for username, info in seed.items():
            cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cur.fetchone():
                continue
            salt, pwh = _hash_password(info["password"])
            now = datetime.now(timezone.utc).isoformat()
            cur.execute("""
                        INSERT INTO users (username, role, salt_hex, password_hash_hex,
                                           failed_attempts, lock_until, password_last_set,
                                           auth_provider, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 0, NULL, ?, 'local', ?, ?)
                        """, (username, info.get("role", "Viewer"), salt, pwh, now, now, now))
        conn.commit()


def list_users_df() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query("""
                               SELECT id,
                                      username,
                                      role,
                                      email,
                                      auth_provider,
                                      failed_attempts,
                                      lock_until,
                                      password_last_set,
                                      created_at
                               FROM users
                               ORDER BY id
                               """, conn)
    return df


def count_users() -> int:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        return c.fetchone()[0]


# =========================
# Items CRUD
# =========================
def _item_row_to_dict(row):
    if not row:
        return None
    cols = [
        "id", "created_by", "source", "title", "brand", "price", "origin", "material",
        "category", "image_path", "label_image_path", "co2_estimate", "co2_level",
        "status", "action_type", "second_hand_price", "savings", "color", "confidence",
        "created_at", "updated_at"
    ]
    return dict(zip(cols, row))


def create_item(created_by: str, source: str, title: str, price: float,
                brand: str | None = None, origin: str | None = None,
                material: str | None = None, category: str | None = None,
                image_path: str | None = None, label_image_path: str | None = None,
                co2_estimate: float | None = None, co2_level: str | None = None,
                status: str = "in_cart", action_type: str = "none",
                second_hand_price: float | None = None, savings: float | None = None,
                color: str | None = None, confidence: float | None = None) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    INSERT INTO items (created_by, source, title, brand, price, origin, material, category,
                                       image_path, label_image_path, co2_estimate, co2_level, status, action_type,
                                       second_hand_price, savings, color, confidence, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (created_by, source, title, brand, price, origin, material, category,
                          image_path, label_image_path, co2_estimate, co2_level, status, action_type,
                          second_hand_price, savings, color, confidence, now, now))
        conn.commit()
        return cur.lastrowid


def get_item(item_id: int) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id,
                           created_by,
                           source,
                           title,
                           brand,
                           price,
                           origin,
                           material,
                           category,
                           image_path,
                           label_image_path,
                           co2_estimate,
                           co2_level,
                           status,
                           action_type,
                           second_hand_price,
                           savings,
                           color,
                           confidence,
                           created_at,
                           updated_at
                    FROM items
                    WHERE id = ?
                    """, (item_id,))
        return _item_row_to_dict(cur.fetchone())


def update_item(item_id: int, **fields):
    if not fields:
        return
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    keys = ", ".join([f"{k} = ?" for k in fields.keys()])
    vals = list(fields.values())
    vals.append(item_id)
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE items SET {keys} WHERE id = ?", vals)
        conn.commit()


def list_user_items(username: str, status: str | None = None, order_by: str = "-created_at") -> list[dict]:
    order_sql = "created_at DESC" if order_by.startswith("-") else "created_at ASC"
    with get_conn() as conn:
        cur = conn.cursor()
        if status:
            cur.execute(f"""
                SELECT id, created_by, source, title, brand, price, origin, material, category,
                       image_path, label_image_path, co2_estimate, co2_level, status, action_type,
                       second_hand_price, savings, color, confidence, created_at, updated_at
                FROM items
                WHERE created_by = ? AND status = ?
                ORDER BY {order_sql}
            """, (username, status))
        else:
            cur.execute(f"""
                SELECT id, created_by, source, title, brand, price, origin, material, category,
                       image_path, label_image_path, co2_estimate, co2_level, status, action_type,
                       second_hand_price, savings, color, confidence, created_at, updated_at
                FROM items
                WHERE created_by = ?
                ORDER BY {order_sql}
            """, (username,))
        rows = cur.fetchall()
    return [_item_row_to_dict(r) for r in rows]


def list_items_df() -> pd.DataFrame:
    with get_conn() as conn:
        df = pd.read_sql_query("""
                               SELECT id,
                                      created_by,
                                      source,
                                      title,
                                      brand,
                                      price,
                                      origin,
                                      material,
                                      category,
                                      co2_estimate,
                                      co2_level,
                                      status,
                                      action_type,
                                      second_hand_price,
                                      savings,
                                      color,
                                      confidence,
                                      created_at
                               FROM items
                               ORDER BY id
                               """, conn)
    return df


def count_items() -> int:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM items")
        return c.fetchone()[0]
