
auth_ui2.py — Authentication UI: login, sign-up, password change, logout + Google OAuth
import re
import streamlit as st
from datetime import datetime, timedelta, timezone
import db
import google_oauth


# --- Rerun helper (compatible with old Streamlit versions) ---
def _rerun():
    try:
        st.rerun()
    except Exception:
        try:
            st.experimental_rerun()
        except Exception:
            pass


# UI/session policies
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 15
GLOBAL_MAX_FAILED_ATTEMPTS = 12
GLOBAL_LOCK_MINUTES = 15
PASSWORD_EXPIRY_DAYS = 90


# ---------- Utilities ----------
def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso(dt_str: str) -> datetime:
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")


def validate_password(password: str) -> bool:
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):
        return False
    if not re.search(r"[^A-Za-z0-9]", password):
        return False
    return True


def validate_email(email: str) -> bool:
    if not email or not email.strip():
        return False
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email.strip()))


# ---------- Global guards ----------
def init_global_guard():
    if "global_failed_attempts" not in st.session_state:
        st.session_state["global_failed_attempts"] = 0
    if "global_lock_until" not in st.session_state:
        st.session_state["global_lock_until"] = None


def is_global_locked():
    init_global_guard()
    lock_until = st.session_state.get("global_lock_until")
    if lock_until:
        dt = parse_iso(lock_until)
        if datetime.now(timezone.utc) < dt.astimezone(timezone.utc):
            minutes_left = int((dt - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            return True, f"Global access locked. Try again in ~{minutes_left} min."
        else:
            st.session_state["global_lock_until"] = None
            st.session_state["global_failed_attempts"] = 0
            return False, None
    return False, None


def register_global_fail():
    init_global_guard()
    st.session_state["global_failed_attempts"] += 1
    if st.session_state["global_failed_attempts"] >= GLOBAL_MAX_FAILED_ATTEMPTS:
        st.session_state["global_lock_until"] = (
                datetime.now(timezone.utc) + timedelta(minutes=GLOBAL_LOCK_MINUTES)
        ).isoformat()


def reset_global_fail():
    init_global_guard()
    st.session_state["global_failed_attempts"] = 0
    st.session_state["global_lock_until"] = None


# ---------- Per-user lock and expiry ----------
def is_locked(user_data: dict):
    lock_until = user_data.get("lock_until")
    if lock_until:
        try:
            dt = parse_iso(lock_until)
            if datetime.now(timezone.utc) < dt.astimezone(timezone.utc):
                minutes_left = int((dt - datetime.now(timezone.utc)).total_seconds() // 60) + 1
                return True, f"Account locked. Try again in ~{minutes_left} min."
            else:
                db.reset_failed_attempts(user_data["username"])
                return False, None
        except Exception:
            return False, None
    return False, None


def is_password_expired(user_data: dict) -> bool:
    if user_data.get("auth_provider") == "google":
        return False
    if PASSWORD_EXPIRY_DAYS <= 0:
        return False
    last_set_iso = user_data.get("password_last_set")
    if not last_set_iso:
        return True
    try:
        last_set = parse_iso(last_set_iso)
    except Exception:
        return True
    return datetime.now(timezone.utc) >= (last_set.astimezone(timezone.utc) + timedelta(days=PASSWORD_EXPIRY_DAYS))


# ---------- Session ----------
def is_authenticated() -> bool:
    return bool(st.session_state.get("logged_in", False))


def _ensure_session_keys():
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False
        st.session_state["username"] = None  # CHANGED: was "user"
        st.session_state["role"] = None
        st.session_state["must_change_password"] = False
        st.session_state["auth_provider"] = None
        st.session_state["profile_picture"] = None
    init_global_guard()
    google_oauth.init_google_oauth_session()


# ---------- Google OAuth Handler ----------
def handle_google_oauth_callback():
    _ensure_session_keys()
    query_params = st.query_params

    if "code" in query_params:
        code = query_params["code"]
        user_info = google_oauth.verify_google_token(code)

        if not user_info:
            st.error("❌ Failed to authenticate with Google.")
            st.query_params.clear()
            return False

        user = db.get_user_by_google_id(user_info["google_id"])

        if not user:
            user = db.get_user_by_email(user_info["email"])

            if user:
                db.update_user_profile(
                    user["username"],
                    google_id=user_info["google_id"],
                    profile_picture=user_info.get("picture"),
                    auth_provider="google"
                )
                st.info("✅ Google account linked to your existing account.")
            else:
                success, error, user = db.create_google_user(
                    google_id=user_info["google_id"],
                    email=user_info["email"],
                    name=user_info["name"],
                    picture=user_info.get("picture"),
                    role="Viewer"
                )

                if not success:
                    st.error(f"❌ {error}")
                    st.query_params.clear()
                    return False

                st.success("✅ Account created successfully!")

        st.session_state["logged_in"] = True
        st.session_state["username"] = user["username"]  # CHANGED: Store username
        st.session_state["role"] = user.get("role", "Viewer")
        st.session_state["auth_provider"] = "google"
        st.session_state["profile_picture"] = user.get("profile_picture")

        reset_global_fail()
        st.query_params.clear()
        st.success(f"✅ Welcome, {user['email']}!")
        _rerun()
        return True

    return False


# ---------- UI: Sign-up ----------
def show_signup():
    _ensure_session_keys()
    st.subheader("🆕 Create account")

    with st.form("signup_form", clear_on_submit=False):
        email = st.text_input("Email *", placeholder="user@example.com")
        new_pass = st.text_input("New password *", type="password")
        confirm = st.text_input("Confirm password *", type="password")
        role = st.selectbox("Role", ["Viewer", "Manager", "Admin"])
        submitted = st.form_submit_button("Register")

    if not submitted:
        return

    g_locked, g_msg = is_global_locked()
    if g_locked:
        st.error(f"⛔ {g_msg}")
        return

    if not email or not email.strip():
        register_global_fail()
        st.error("⚠️ Email is required.")
        return

    if not validate_email(email):
        register_global_fail()
        st.error("⚠️ Please enter a valid email address (e.g., user@example.com).")
        return

    if not validate_password(new_pass):
        register_global_fail()
        st.error("⚠️ Password must be at least 8 characters, include one uppercase letter and one special character.")
        return

    if new_pass != confirm:
        register_global_fail()
        st.error("⚠️ Passwords do not match.")
        return

    email_clean = email.strip().lower()
    ok, msg = db.create_user(email_clean, new_pass, role, email=email_clean)
    if ok:
        reset_global_fail()
        st.success("✅ User registered. You can now log in with your email.")
    else:
        register_global_fail()
        st.error(msg or "⚠️ Could not create user. Email may already be registered.")


# ---------- UI: Login ----------
def show_login():
    _ensure_session_keys()
    st.subheader("🔐 Sign in")

    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Email *", placeholder="user@example.com")
        password = st.text_input("Password *", type="password")
        submitted = st.form_submit_button("Login")

    if not submitted:
        if google_oauth.google_oauth_available():
            st.divider()
            st.write("**Or sign in with:**")

            if st.button("🔵 Continue with Google", key="google_login_btn", use_container_width=True):
                auth_url, state = google_oauth.get_google_auth_url()
                if auth_url:
                    st.markdown(
                        f'<meta http-equiv="refresh" content="0;url={auth_url}">',
                        unsafe_allow_html=True
                    )
                    st.info("🔄 Redirecting to Google...")
                else:
                    st.error("❌ Google OAuth not properly configured.")
        return

    g_locked, g_msg = is_global_locked()
    if g_locked:
        st.error(f"⛔ {g_msg}")
        return

    if not email or not email.strip():
        register_global_fail()
        st.error("⚠️ Email is required.")
        return

    if not validate_email(email):
        register_global_fail()
        st.error("⚠️ Please enter a valid email address.")
        return

    email_clean = email.strip().lower()
    user_data = db.get_user(email_clean)
    if not user_data:
        register_global_fail()
        st.error("❌ Incorrect email or password.")
        return

    if user_data.get("auth_provider") == "google":
        register_global_fail()
        st.error("❌ This account uses Google Sign-In. Please use the 'Continue with Google' button.")
        return

    locked, msg = is_locked(user_data)
    if locked:
        register_global_fail()
        st.error(f"⛔ {msg}")
        return

    ok, _ = db.authenticate(email_clean, password)
    if not ok:
        db.register_failed_attempt(email_clean, MAX_FAILED_ATTEMPTS, LOCKOUT_MINUTES)
        register_global_fail()

        after_user = int(user_data.get("failed_attempts", 0)) + 1
        remaining_user = max(0, MAX_FAILED_ATTEMPTS - after_user)
        remaining_global = max(0, GLOBAL_MAX_FAILED_ATTEMPTS - st.session_state["global_failed_attempts"])

        if remaining_global == 0:
            st.error(f"⛔ Global maximum reached. Access locked for {GLOBAL_LOCK_MINUTES} min.")
        else:
            st.error(f"❌ Incorrect credentials. Remaining — User: {remaining_user} | Global: {remaining_global}")
        return

    db.reset_failed_attempts(email_clean)
    reset_global_fail()

    if is_password_expired(user_data):
        st.warning("⚠️ Your password has expired. You must change it to continue.")
        st.session_state["must_change_password"] = True
        st.session_state["username"] = email_clean  # CHANGED
        show_change_password(email_clean, force=True)
        return

    st.session_state["logged_in"] = True
    st.session_state["username"] = user_data.get("username", email_clean)  # CHANGED: Store username from DB
    st.session_state["role"] = user_data.get("role", "Viewer")
    st.session_state["auth_provider"] = user_data.get("auth_provider", "local")
    st.session_state["profile_picture"] = user_data.get("profile_picture")
    st.success(f"✅ Welcome, {email_clean}!")
    _rerun()


# ---------- UI: Password change ----------
def show_change_password(email: str, force: bool = False):
    help_txt = "Your password has expired, please change it to continue." if force else "Update your password."
    with st.form("force_pw_change", clear_on_submit=False):
        st.info(help_txt)
        current_pw = None if force else st.text_input("Current password (leave empty if expired)", type="password")
        new_pw = st.text_input("New password", type="password")
        confirm_pw = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update")

    if not submitted:
        return

    g_locked, g_msg = is_global_locked()
    if g_locked:
        st.error(f"⛔ {g_msg}")
        return

    if not force:
        ok, _ = db.authenticate(email, current_pw or "")
        if not ok:
            register_global_fail()
            st.error("❌ Current password is incorrect.")
            return

    if new_pw != confirm_pw:
        register_global_fail()
        st.error("⚠️ Passwords do not match.")
        return
    if not validate_password(new_pw):
        register_global_fail()
        st.error("⚠️ New password does not meet requirements (min 8 chars, 1 uppercase, 1 special char).")
        return

    db.set_new_password(email, new_pw)
    reset_global_fail()
    st.success("✅ Password updated successfully.")
    st.session_state["must_change_password"] = False
    st.session_state["logged_in"] = True
    fresh = db.get_user(email)
    st.session_state["username"] = fresh.get("username", email) if fresh else email  # CHANGED
    st.session_state["role"] = fresh.get("role", "Viewer") if fresh else "Viewer"
    _rerun()


# ---------- UI: Logout ----------
def logout():
    st.session_state["logged_in"] = False
    st.session_state["username"] = None  # CHANGED: was "user"
    st.session_state["role"] = None
    st.session_state["must_change_password"] = False
    st.session_state["auth_provider"] = None
    st.session_state["profile_picture"] = None
    st.session_state["google_oauth_state"] = None
    st.session_state["google_auth_url"] = None
    reset_global_fail()
