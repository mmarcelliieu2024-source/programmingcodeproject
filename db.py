
# db.py — SQLite: init, CRUD, auth, PBKDF2 hashing + items entity + Google OAuth

# IMPORTS
import sqlite3  # SQLite database engine
import os  # For generating random salt
import hashlib  # For PBKDF2 password hashing
import hmac  # For constant-time password comparison (prevents timing attacks)
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pandas as pd  # For exporting data as DataFrames

# DATABASE CONFIGURATION
DB_PATH = Path("app.db")  # SQLite database file location

# PBKDF2 PASSWORD HASHING CONFIGURATION 
# PBKDF2 (Password-Based Key Derivation Function 2) is a secure hashing standard
PBKDF2_ALGO = "sha256"  # Hashing algorithm (SHA-256)
PBKDF2_ITERATIONS = 200_000  # Number of iterations (higher = slower but more secure)
SALT_BYTES = 16  # Size of random salt in bytes (128 bits)


#  DATABASE CONNECTION HELPER 
def get_conn():
    """Create and return a new SQLite database connection"""
    return sqlite3.connect(DB_PATH)


def db_path() -> str:
    """Get absolute path to database file"""
    return str(DB_PATH.resolve())


# DATABASE INITIALIZATION 
def init_db():
    """
    Initialize database tables if they don't exist.
    Creates two tables: users and items
    """
    with get_conn() as conn:
        # Enable Write-Ahead Logging (WAL) mode for better concurrent access
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
        except Exception:
            pass  # Fail silently if WAL not supported
        
        c = conn.cursor()
        
        # USERS TABLE
        # Stores user accounts with both local and Google OAuth authentication
        c.execute("""
                  CREATE TABLE IF NOT EXISTS users
                  (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,  -- Unique user ID
                      username TEXT UNIQUE NOT NULL,  -- Username (unique identifier)
                      role TEXT NOT NULL DEFAULT 'Viewer',  -- User role (Viewer/Manager/Admin)
                      
                      -- Password fields (for local authentication)
                      salt_hex TEXT,  -- Random salt for password hashing (hex encoded)
                      password_hash_hex TEXT,  -- PBKDF2 hashed password (hex encoded)
                      
                      -- Security fields
                      failed_attempts INTEGER NOT NULL DEFAULT 0,  -- Failed login counter
                      lock_until TEXT,  -- ISO timestamp until account is locked
                      password_last_set TEXT,  -- ISO timestamp of last password change
                      
                      -- Google OAuth fields
                      google_id TEXT UNIQUE,  -- Google user ID (unique)
                      email TEXT,  -- User's email address
                      profile_picture TEXT,  -- URL to profile picture
                      auth_provider TEXT DEFAULT 'local' 
                          CHECK (auth_provider IN ('local', 'google')),  -- Auth method
                      
                      -- Audit fields
                      created_at TEXT DEFAULT (datetime('now')),  -- Record creation timestamp
                      updated_at TEXT  -- Last update timestamp
                  )
                  """)
        
        # ITEMS TABLE (SpendSense) 
        # Stores clothing items tracked by users (originals and second-hand alternatives)
        c.execute("""
                  CREATE TABLE IF NOT EXISTS items
                  (
                      id INTEGER PRIMARY KEY AUTOINCREMENT,  -- Unique item ID
                      created_by TEXT NOT NULL,  -- Username who created this item
                      
                      -- Item source
                      source TEXT CHECK (source IN ('original', 'second_hand')) 
                          NOT NULL DEFAULT 'original',  -- Where item comes from
                      
                      -- Basic item information
                      title TEXT NOT NULL,  -- Item name/description
                      brand TEXT,  -- Brand name
                      price REAL NOT NULL,  -- Original price in euros
                      origin TEXT,  -- Country of manufacture
                      material TEXT,  -- Primary material (cotton, polyester, etc.)
                      category TEXT,  -- Item category (shirt, pants, etc.)
                      
                      -- Images
                      image_path TEXT,  -- Path to product image
                      label_image_path TEXT,  -- Path to label/tag image
                      
                      -- Environmental impact
                      co2_estimate REAL,  -- Estimated CO₂ emissions in kg
                      co2_level TEXT CHECK (co2_level IN ('low', 'medium', 'high')),
                      
                      -- Shopping cart status
                      status TEXT CHECK (status IN ('in_cart', 'positive', 'negative')) 
                          NOT NULL DEFAULT 'in_cart',  -- Current item status
                      
                      -- Action taken
                      action_type TEXT CHECK (action_type IN 
                          ('none', 'bought_original', 'saved_money', 'bought_second_hand')) 
                          NOT NULL DEFAULT 'none',  -- What user did with this item
                      
                      -- Financial tracking
                      second_hand_price REAL,  -- Actual second-hand purchase price
                      savings REAL,  -- Amount saved vs. original price
                      
                      -- Additional metadata
                      color TEXT,  -- Main color of item
                      confidence REAL,  -- AI confidence score (0-1)
                      
                      -- Audit fields
                      created_at TEXT DEFAULT (datetime('now')),  -- Record creation
                      updated_at TEXT  -- Last update
                  )
                  """)
        
        conn.commit()


# PASSWORD HASHING HELPERS

def _hash_password(password: str) -> tuple[str, str]:
    """
    Hash a password using PBKDF2 with random salt.
    Returns: (salt_hex, password_hash_hex)
    """
    # Generate random salt (cryptographically secure)
    salt = os.urandom(SALT_BYTES)
    
    # Apply PBKDF2 key derivation with 200,000 iterations
    pwd = hashlib.pbkdf2_hmac(
        PBKDF2_ALGO,  # SHA-256
        password.encode("utf-8"),  # Password as bytes
        salt,  # Random salt
        PBKDF2_ITERATIONS  # 200,000 iterations
    )
    
    # Return both as hex strings for database storage
    return salt.hex(), pwd.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    """
    Verify a password against stored salt and hash.
    Uses constant-time comparison to prevent timing attacks.
    """
    # Convert hex strings back to bytes
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(hash_hex)
    
    # Hash the provided password with the stored salt
    got = hashlib.pbkdf2_hmac(
        PBKDF2_ALGO, 
        password.encode("utf-8"), 
        salt, 
        PBKDF2_ITERATIONS
    )
    
    # Use constant-time comparison (prevents timing attacks)
    return hmac.compare_digest(got, expected)


# USERS CRUD / AUTHENTICATION

def _user_row_to_dict(row):
    """Convert database row tuple to dictionary for easier access"""
    if not row:
        return None
    
    # Define column names in order
    cols = ["id", "username", "role", "salt_hex", "password_hash_hex", "failed_attempts",
            "lock_until", "password_last_set", "google_id", "email", "profile_picture",
            "auth_provider", "created_at", "updated_at"]
    
    # Zip column names with row values
    return dict(zip(cols, row))


def get_user(username: str) -> dict | None:
    """Fetch user by username"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id, username, role, salt_hex, password_hash_hex, failed_attempts,
                           lock_until, password_last_set, google_id, email, profile_picture,
                           auth_provider, created_at, updated_at
                    FROM users
                    WHERE username = ?
                    """, (username,))
        return _user_row_to_dict(cur.fetchone())


def get_user_by_email(email: str) -> dict | None:
    """Fetch user by email address (used for Google OAuth account linking)"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id, username, role, salt_hex, password_hash_hex, failed_attempts,
                           lock_until, password_last_set, google_id, email, profile_picture,
                           auth_provider, created_at, updated_at
                    FROM users
                    WHERE email = ?
                    """, (email,))
        return _user_row_to_dict(cur.fetchone())


def get_user_by_google_id(google_id: str) -> dict | None:
    """Fetch user by Google ID (used for Google OAuth login)"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id, username, role, salt_hex, password_hash_hex, failed_attempts,
                           lock_until, password_last_set, google_id, email, profile_picture,
                           auth_provider, created_at, updated_at
                    FROM users
                    WHERE google_id = ?
                    """, (google_id,))
        return _user_row_to_dict(cur.fetchone())


def create_user(username: str, password: str, role: str = "Viewer",
                email: str = None, auth_provider: str = "local") -> tuple[bool, str | None]:
    """
    Create a new user with local authentication.
    Returns: (success: bool, error_message: str | None)
    """
    try:
        # Hash the password with random salt
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
        
        return True, None  # Success
        
    except sqlite3.IntegrityError:
        # Username or email already exists (UNIQUE constraint)
        return False, "User already exists."
    except Exception as e:
        return False, f"Error creating user: {e}"


def create_google_user(google_id: str, email: str, name: str,
                       picture: str = None, role: str = "Viewer") -> tuple[bool, str | None, dict | None]:
    """
    Create a new user from Google OAuth.
    Automatically generates a unique username from email or name.
    Returns: (success, error_message, user_dict)
    """
    try:
        # Generate username from email (part before @)
        username = email.split('@')[0] if email else name.replace(' ', '_').lower()

        # Ensure username is unique by adding counter if needed
        base_username = username
        counter = 1
        with get_conn() as conn:
            cur = conn.cursor()
            while True:
                # Check if username exists
                cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
                if not cur.fetchone():
                    break  # Username available
                # Add counter and try again
                username = f"{base_username}{counter}"
                counter += 1

        now = datetime.now(timezone.utc).isoformat()

        # Create user with Google OAuth (no password fields needed)
        with get_conn() as conn:
            cur = conn.cursor()
            cur.execute("""
                        INSERT INTO users (username, role, google_id, email, profile_picture,
                                           auth_provider, failed_attempts, created_at, updated_at)
                        VALUES (?, ?, ?, ?, ?, 'google', 0, ?, ?)
                        """, (username, role, google_id, email, picture, now, now))
            conn.commit()

        # Fetch and return the newly created user
        user = get_user_by_google_id(google_id)
        return True, None, user

    except sqlite3.IntegrityError as e:
        # Google ID already exists (shouldn't happen in normal flow)
        return False, f"User with this Google account already exists: {e}", None
    except Exception as e:
        return False, f"Error creating Google user: {e}", None


def authenticate(username: str, password: str) -> tuple[bool, dict | None]:
    """
    Authenticate user with username and password.
    Returns: (success: bool, user_dict: dict | None)
    """
    # Fetch user from database
    u = get_user(username)
    if not u:
        return False, None  # User doesn't exist

    # Check if user uses Google OAuth (can't login with password)
    if u.get("auth_provider") == "google":
        return False, None

    # Check if password fields exist (they should for local auth)
    if not u.get("salt_hex") or not u.get("password_hash_hex"):
        return False, None  # Password not set

    # Verify password using PBKDF2
    ok = _verify_password(password, u["salt_hex"], u["password_hash_hex"])
    return ok, u if ok else None


def register_failed_attempt(username: str, max_attempts: int, lock_minutes: int):
    """
    Increment failed login attempts counter.
    Lock account if max attempts reached.
    """
    u = get_user(username)
    if not u:
        return  # User doesn't exist
    
    # Increment failed attempts counter
    fa = int(u.get("failed_attempts") or 0) + 1
    lock_until = u.get("lock_until")
    
    # Lock account if max attempts reached
    if fa >= max_attempts:
        lock_until = (datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)).isoformat()
    
    # Update database
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
    """Reset failed login attempts counter (called on successful login)"""
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
    """
    Update user's password with a new one.
    Re-hashes password with new random salt.
    """
    # Generate new salt and hash
    salt, pwh = _hash_password(new_password)
    now = datetime.now(timezone.utc).isoformat()
    
    # Update database
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
    """
    Update user profile fields dynamically.
    Used for Google OAuth account linking and profile updates.
    """
    if not fields:
        return  # Nothing to update
    
    # Add timestamp
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # Build dynamic SQL query
    keys = ", ".join([f"{k} = ?" for k in fields.keys()])
    vals = list(fields.values())
    vals.append(username)  # WHERE clause value
    
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE users SET {keys} WHERE username = ?", vals)
        conn.commit()


def seed_initial_users(seed: dict):
    """
    Seed database with initial test users.
    Only creates users that don't already exist.
    """
    with get_conn() as conn:
        cur = conn.cursor()
        
        for username, info in seed.items():
            # Check if user already exists
            cur.execute("SELECT 1 FROM users WHERE username = ?", (username,))
            if cur.fetchone():
                continue  # Skip existing user
            
            # Hash password
            salt, pwh = _hash_password(info["password"])
            now = datetime.now(timezone.utc).isoformat()
            
            # Insert new user
            cur.execute("""
                        INSERT INTO users (username, role, salt_hex, password_hash_hex,
                                           failed_attempts, lock_until, password_last_set,
                                           auth_provider, created_at, updated_at)
                        VALUES (?, ?, ?, ?, 0, NULL, ?, 'local', ?, ?)
                        """, (username, info.get("role", "Viewer"), salt, pwh, now, now, now))
        
        conn.commit()


def list_users_df() -> pd.DataFrame:
    """Export all users to pandas DataFrame (for admin view)"""
    with get_conn() as conn:
        df = pd.read_sql_query("""
                               SELECT id, username, role, email, auth_provider,
                                      failed_attempts, lock_until, password_last_set,
                                      created_at
                               FROM users
                               ORDER BY id
                               """, conn)
    return df


def count_users() -> int:
    """Get total number of users"""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM users")
        return c.fetchone()[0]


# ITEMS CRUD (SpendSense) 

def _item_row_to_dict(row):
    """Convert database row tuple to dictionary for easier access"""
    if not row:
        return None
    
    # Define column names in order
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
    """
    Create a new item in the database.
    Returns: item_id (auto-generated primary key)
    """
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
        
        # Return the auto-generated ID
        return cur.lastrowid


def get_item(item_id: int) -> dict | None:
    """Fetch a single item by ID"""
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("""
                    SELECT id, created_by, source, title, brand, price, origin, material,
                           category, image_path, label_image_path, co2_estimate, co2_level,
                           status, action_type, second_hand_price, savings, color,
                           confidence, created_at, updated_at
                    FROM items
                    WHERE id = ?
                    """, (item_id,))
        return _item_row_to_dict(cur.fetchone())


def update_item(item_id: int, **fields):
    """
    Update item fields dynamically.
    Used when user takes action on an item (bought, saved, etc.)
    """
    if not fields:
        return  # Nothing to update
    
    # Add timestamp
    fields["updated_at"] = datetime.now(timezone.utc).isoformat()
    
    # Build dynamic SQL query
    keys = ", ".join([f"{k} = ?" for k in fields.keys()])
    vals = list(fields.values())
    vals.append(item_id)  # WHERE clause value
    
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE items SET {keys} WHERE id = ?", vals)
        conn.commit()


def list_user_items(username: str, status: str | None = None, order_by: str = "-created_at") -> list[dict]:
    """
    List all items for a specific user.
    Can filter by status and order results.
    """
    # Convert order_by format: "-created_at" → "created_at DESC"
    order_sql = "created_at DESC" if order_by.startswith("-") else "created_at ASC"
    
    with get_conn() as conn:
        cur = conn.cursor()
        
        # Query with optional status filter
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
    
    # Convert all rows to dictionaries
    return [_item_row_to_dict(r) for r in rows]


def list_items_df() -> pd.DataFrame:
    """Export all items to pandas DataFrame (for admin view)"""
    with get_conn() as conn:
        df = pd.read_sql_query("""
                               SELECT id, created_by, source, title, brand, price, origin,
                                      material, category, co2_estimate, co2_level, status,
                                      action_type, second_hand_price, savings, color,
                                      confidence, created_at
                               FROM items
                               ORDER BY id
                               """, conn)
    return df


def count_items() -> int:
    """Get total number of items"""
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM items")
        return c.fetchone()[0]
