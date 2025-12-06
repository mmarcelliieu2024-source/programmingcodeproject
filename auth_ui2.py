
# auth_ui2.py — Authentication UI: login, sign-up, password change, logout + Google OAuth

# IMPORTS
import re  # Regular expressions for validation
import streamlit as st
from datetime import datetime, timedelta, timezone
import db  # Database operations module
import google_oauth  # Google OAuth integration module


# RERUN HELPER (compatibility with different Streamlit versions)
def _rerun():
    """Handle rerun for both old and new Streamlit versions"""
    try:
        st.rerun()  # New version
    except Exception:
        try:
            st.experimental_rerun()  # Older versions
        except Exception:
            pass  # Fail silently if neither works


# SECURITY POLICY CONSTANTS 
MAX_FAILED_ATTEMPTS = 5  # Max failed login attempts per user before lockout
LOCKOUT_MINUTES = 15  # Minutes to lock user account after max failures
GLOBAL_MAX_FAILED_ATTEMPTS = 12  # Max failed attempts across all users
GLOBAL_LOCK_MINUTES = 15  # Minutes to lock entire system after global max
PASSWORD_EXPIRY_DAYS = 90  # Days until password expires (0 = never)


# UTILITY FUNCTIONS 

def now_utc_iso() -> str:
    """Get current UTC time as ISO string"""
    return datetime.now(timezone.utc).isoformat()


def parse_iso(dt_str: str) -> datetime:
    """Parse ISO datetime string with fallback for different formats"""
    try:
        return datetime.fromisoformat(dt_str)
    except Exception:
        # Fallback for older format without timezone
        return datetime.strptime(dt_str, "%Y-%m-%dT%H:%M:%S")


def validate_password(password: str) -> bool:
    """
    Validate password strength:
    - At least 8 characters
    - At least 1 uppercase letter
    - At least 1 special character
    """
    if len(password) < 8:
        return False
    if not re.search(r"[A-Z]", password):  # Check for uppercase
        return False
    if not re.search(r"[^A-Za-z0-9]", password):  # Check for special char
        return False
    return True


def validate_email(email: str) -> bool:
    """Validate email format using regex pattern"""
    if not email or not email.strip():
        return False
    # Standard email regex pattern
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email.strip()))


# GLOBAL SECURITY GUARDS (prevent brute force attacks) 

def init_global_guard():
    """Initialize global security tracking in session state"""
    if "global_failed_attempts" not in st.session_state:
        st.session_state["global_failed_attempts"] = 0
    if "global_lock_until" not in st.session_state:
        st.session_state["global_lock_until"] = None


def is_global_locked():
    """
    Check if system is globally locked due to too many failed attempts.
    Returns: (is_locked: bool, message: str)
    """
    init_global_guard()
    lock_until = st.session_state.get("global_lock_until")
    
    if lock_until:
        dt = parse_iso(lock_until)
        # Check if lock period is still active
        if datetime.now(timezone.utc) < dt.astimezone(timezone.utc):
            # Calculate minutes remaining
            minutes_left = int((dt - datetime.now(timezone.utc)).total_seconds() // 60) + 1
            return True, f"Global access locked. Try again in ~{minutes_left} min."
        else:
            # Lock period expired, reset counters
            st.session_state["global_lock_until"] = None
            st.session_state["global_failed_attempts"] = 0
            return False, None
    
    return False, None


def register_global_fail():
    """
    Increment global failed attempt counter.
    Trigger global lockout if max attempts reached.
    """
    init_global_guard()
    st.session_state["global_failed_attempts"] += 1
    
    # Check if global max reached
    if st.session_state["global_failed_attempts"] >= GLOBAL_MAX_FAILED_ATTEMPTS:
        # Set lock expiration time
        st.session_state["global_lock_until"] = (
                datetime.now(timezone.utc) + timedelta(minutes=GLOBAL_LOCK_MINUTES)
        ).isoformat()


def reset_global_fail():
    """Reset global failed attempt counter (called on successful login)"""
    init_global_guard()
    st.session_state["global_failed_attempts"] = 0
    st.session_state["global_lock_until"] = None


#PER-USER SECURITY CHECKS 

def is_locked(user_data: dict):
    """
    Check if specific user account is locked.
    Returns: (is_locked: bool, message: str)
    """
    lock_until = user_data.get("lock_until")
    
    if lock_until:
        try:
            dt = parse_iso(lock_until)
            # Check if lock is still active
            if datetime.now(timezone.utc) < dt.astimezone(timezone.utc):
                # Calculate minutes remaining
                minutes_left = int((dt - datetime.now(timezone.utc)).total_seconds() // 60) + 1
                return True, f"Account locked. Try again in ~{minutes_left} min."
            else:
                # Lock expired, reset user's failed attempts
                db.reset_failed_attempts(user_data["username"])
                return False, None
        except Exception:
            return False, None
    
    return False, None


def is_password_expired(user_data: dict) -> bool:
    """
    Check if user's password has expired.
    Google OAuth users never have expired passwords.
    """
    # Google users don't have password expiry
    if user_data.get("auth_provider") == "google":
        return False
    
    # If expiry disabled (0 days), never expire
    if PASSWORD_EXPIRY_DAYS <= 0:
        return False
    
    # Get last password set date
    last_set_iso = user_data.get("password_last_set")
    if not last_set_iso:
        return True  # No date = expired (force change)
    
    try:
        last_set = parse_iso(last_set_iso)
    except Exception:
        return True  # Parse error = expired (safety)
    
    # Check if current time exceeds expiry period
    return datetime.now(timezone.utc) >= (last_set.astimezone(timezone.utc) + timedelta(days=PASSWORD_EXPIRY_DAYS))


# SESSION STATE MANAGEMENT

def is_authenticated() -> bool:
    """Check if user is currently logged in"""
    return bool(st.session_state.get("logged_in", False))


def _ensure_session_keys():
    """Initialize all required session state keys if not present"""
    if "logged_in" not in st.session_state:
        st.session_state["logged_in"] = False
        st.session_state["user"] = None
        st.session_state["role"] = None
        st.session_state["must_change_password"] = False
        st.session_state["auth_provider"] = None
        st.session_state["profile_picture"] = None
    
    # Initialize security guards
    init_global_guard()
    google_oauth.init_google_oauth_session()


# GOOGLE OAUTH CALLBACK HANDLER 

def handle_google_oauth_callback():
    """
    Handle OAuth callback from Google after authentication.
    This is called when Google redirects back to the app with an auth code.
    Returns: True if callback was handled, False otherwise
    """
    _ensure_session_keys()
    query_params = st.query_params

    # Check if this is an OAuth callback (contains 'code' parameter)
    if "code" in query_params:
        code = query_params["code"]
        
        # Exchange authorization code for user info
        user_info = google_oauth.verify_google_token(code)

        if not user_info:
            st.error(" Failed to authenticate with Google.")
            st.query_params.clear()
            return False

        # Try to find existing user by Google ID
        user = db.get_user_by_google_id(user_info["google_id"])

        if not user:
            # Google ID not found, check if email exists (link accounts)
            user = db.get_user_by_email(user_info["email"])

            if user:
                # Link Google account to existing local account
                db.update_user_profile(
                    user["username"],
                    google_id=user_info["google_id"],
                    profile_picture=user_info.get("picture"),
                    auth_provider="google"
                )
                st.info(" Google account linked to your existing account.")
            else:
                # Create new user with Google account
                success, error, user = db.create_google_user(
                    google_id=user_info["google_id"],
                    email=user_info["email"],
                    name=user_info["name"],
                    picture=user_info.get("picture"),
                    role="Viewer"  # Default role for new Google users
                )

                if not success:
                    st.error(f" {error}")
                    st.query_params.clear()
                    return False

                st.success(" Account created successfully!")

        # Set session state for logged-in user
        st.session_state["logged_in"] = True
        st.session_state["user"] = user["email"]
        st.session_state["role"] = user.get("role", "Viewer")
        st.session_state["auth_provider"] = "google"
        st.session_state["profile_picture"] = user.get("profile_picture")

        # Reset security counters on successful login
        reset_global_fail()
        
        # Clear query parameters to prevent re-processing
        st.query_params.clear()
        
        st.success(f"Welcome, {user['email']}!")
        _rerun()
        return True

    return False


# SIGN-UP UI 

def show_signup():
    """Display user registration form"""
    _ensure_session_keys()
    st.subheader("Create account")

    # Create form for signup (prevents page reload on every input)
    with st.form("signup_form", clear_on_submit=False):
        email = st.text_input("Email *", placeholder="user@example.com")
        new_pass = st.text_input("New password *", type="password")
        confirm = st.text_input("Confirm password *", type="password")
        role = st.selectbox("Role", ["Viewer", "Manager", "Admin"])
        submitted = st.form_submit_button("Register")

    # Only process if form was submitted
    if not submitted:
        return

    # VALIDATION CHECKS 
    
    # Check global lockout
    g_locked, g_msg = is_global_locked()
    if g_locked:
        st.error(f" {g_msg}")
        return

    # Validate email presence
    if not email or not email.strip():
        register_global_fail()
        st.error("Email is required.")
        return

    # Validate email format
    if not validate_email(email):
        register_global_fail()
        st.error(" Please enter a valid email address (e.g., user@example.com).")
        return

    # Validate password strength
    if not validate_password(new_pass):
        register_global_fail()
        st.error(" Password must be at least 8 characters, include one uppercase letter and one special character.")
        return

    # Validate password confirmation
    if new_pass != confirm:
        register_global_fail()
        st.error(" Passwords do not match.")
        return

    # CREATE USER 
    
    # Normalize email (lowercase, trimmed)
    email_clean = email.strip().lower()
    
    # Attempt to create user in database
    ok, msg = db.create_user(email_clean, new_pass, role, email=email_clean)
    
    if ok:
        reset_global_fail()  # Success, reset security counters
        st.success(" User registered. You can now log in with your email.")
    else:
        register_global_fail()  # Failure, increment security counter
        st.error(msg or "Could not create user. Email may already be registered.")


#LOGIN UI 

def show_login():
    """Display login form with optional Google OAuth"""
    _ensure_session_keys()
    st.subheader(" Sign in")

    # Create login form
    with st.form("login_form", clear_on_submit=False):
        email = st.text_input("Email *", placeholder="user@example.com")
        password = st.text_input("Password *", type="password")
        submitted = st.form_submit_button("Login")

    # If form not submitted, show Google OAuth option
    if not submitted:
        if google_oauth.google_oauth_available():
            st.divider()
            st.write("**Or sign in with:**")

            # Google sign-in button
            if st.button(" Continue with Google", key="google_login_btn", use_container_width=True):
                # Generate Google OAuth URL
                auth_url, state = google_oauth.get_google_auth_url()
                
                if auth_url:
                    # Redirect to Google using meta refresh
                    st.markdown(
                        f'<meta http-equiv="refresh" content="0;url={auth_url}">',
                        unsafe_allow_html=True
                    )
                    st.info(" Redirecting to Google...")
                else:
                    st.error(" Google OAuth not properly configured.")
        return

    # VALIDATION CHECKS
    
    # Check global lockout
    g_locked, g_msg = is_global_locked()
    if g_locked:
        st.error(f" {g_msg}")
        return

    # Validate email presence
    if not email or not email.strip():
        register_global_fail()
        st.error(" Email is required.")
        return

    # Validate email format
    if not validate_email(email):
        register_global_fail()
        st.error(" Please enter a valid email address.")
        return

    # AUTHENTICATION 
    
    # Normalize email
    email_clean = email.strip().lower()
    
    # Get user data from database
    user_data = db.get_user(email_clean)
    if not user_data:
        register_global_fail()
        st.error(" Incorrect email or password.")
        return

    # Check if this is a Google account (can't login with password)
    if user_data.get("auth_provider") == "google":
        register_global_fail()
        st.error(" This account uses Google Sign-In. Please use the 'Continue with Google' button.")
        return

    # Check if account is locked
    locked, msg = is_locked(user_data)
    if locked:
        register_global_fail()
        st.error(f" {msg}")
        return

    # Verify password
    ok, _ = db.authenticate(email_clean, password)
    if not ok:
        # Failed authentication
        db.register_failed_attempt(email_clean, MAX_FAILED_ATTEMPTS, LOCKOUT_MINUTES)
        register_global_fail()

        # Calculate remaining attempts for user and global
        after_user = int(user_data.get("failed_attempts", 0)) + 1
        remaining_user = max(0, MAX_FAILED_ATTEMPTS - after_user)
        remaining_global = max(0, GLOBAL_MAX_FAILED_ATTEMPTS - st.session_state["global_failed_attempts"])

        # Show appropriate error message
        if remaining_global == 0:
            st.error(f" Global maximum reached. Access locked for {GLOBAL_LOCK_MINUTES} min.")
        else:
            st.error(f" Incorrect credentials. Remaining — User: {remaining_user} | Global: {remaining_global}")
        return

    # SUCCESSFUL LOGIN
    
    # Reset failed attempt counters
    db.reset_failed_attempts(email_clean)
    reset_global_fail()

    # Check if password has expired
    if is_password_expired(user_data):
        st.warning(" Your password has expired. You must change it to continue.")
        st.session_state["must_change_password"] = True
        st.session_state["user"] = email_clean
        show_change_password(email_clean, force=True)
        return

    # Set session state for logged-in user
    st.session_state["logged_in"] = True
    st.session_state["user"] = email_clean
    st.session_state["role"] = user_data.get("role", "Viewer")
    st.session_state["auth_provider"] = user_data.get("auth_provider", "local")
    st.session_state["profile_picture"] = user_data.get("profile_picture")
    
    st.success(f" Welcome, {email_clean}!")
    _rerun()


# PASSWORD CHANGE UI 

def show_change_password(email: str, force: bool = False):
    """
    Display password change form.
    Args:
        email: User's email address
        force: If True, password change is mandatory (expired password)
    """
    # Set appropriate help text based on force flag
    help_txt = "Your password has expired, please change it to continue." if force else "Update your password."
    
    # Create password change form
    with st.form("force_pw_change", clear_on_submit=False):
        st.info(help_txt)
        
        # Current password only required if not forced (not expired)
        current_pw = None if force else st.text_input("Current password (leave empty if expired)", type="password")
        new_pw = st.text_input("New password", type="password")
        confirm_pw = st.text_input("Confirm new password", type="password")
        submitted = st.form_submit_button("Update")

    # Only process if form was submitted
    if not submitted:
        return

    # === VALIDATION CHECKS ===
    
    # Check global lockout
    g_locked, g_msg = is_global_locked()
    if g_locked:
        st.error(f" {g_msg}")
        return

    # If not forced, verify current password
    if not force:
        ok, _ = db.authenticate(email, current_pw or "")
        if not ok:
            register_global_fail()
            st.error(" Current password is incorrect.")
            return

    # Validate password confirmation
    if new_pw != confirm_pw:
        register_global_fail()
        st.error(" Passwords do not match.")
        return
    
    # Validate new password strength
    if not validate_password(new_pw):
        register_global_fail()
        st.error(" New password does not meet requirements (min 8 chars, 1 uppercase, 1 special char).")
        return

    # UPDATE PASSWORD 
    
    # Set new password in database
    db.set_new_password(email, new_pw)
    reset_global_fail()  # Reset security counters
    
    st.success(" Password updated successfully.")
    
    # Update session state
    st.session_state["must_change_password"] = False
    st.session_state["logged_in"] = True
    
    # Refresh user data to get updated role
    fresh = db.get_user(email)
    st.session_state["role"] = fresh.get("role", "Viewer") if fresh else "Viewer"
    
    _rerun()


# LOGOUT 

def logout():
    """Clear all session state and log out user"""
    # Clear authentication state
    st.session_state["logged_in"] = False
    st.session_state["user"] = None
    st.session_state["role"] = None
    st.session_state["must_change_password"] = False
    
    # Clear OAuth state
    st.session_state["auth_provider"] = None
    st.session_state["profile_picture"] = None
    st.session_state["google_oauth_state"] = None
    st.session_state["google_auth_url"] = None
    
    # Reset security counters
    reset_global_fail()
