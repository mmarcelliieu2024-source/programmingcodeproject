
# google_oauth.py — Google OAuth 2.0 helper functions
import streamlit as st
from google_auth_oauthlib.flow import Flow
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
import secrets
import os

try:
    from app_secrets import (
        GOOGLE_CLIENT_ID,
        GOOGLE_CLIENT_SECRET,
        GOOGLE_REDIRECT_URI,
        GOOGLE_SCOPES
    )
except ImportError:
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501")
    GOOGLE_SCOPES = [
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile"
    ]


def google_oauth_available() -> bool:
    """Check if Google OAuth credentials are configured."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


def get_google_oauth_flow():
    """Create and return a Google OAuth Flow instance."""
    if not google_oauth_available():
        return None

    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [GOOGLE_REDIRECT_URI],
        }
    }

    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=GOOGLE_SCOPES,
        redirect_uri=GOOGLE_REDIRECT_URI
    )

    return flow


def get_google_auth_url() -> tuple[str, str]:
    """
    Generate Google OAuth authorization URL.
    Returns: (auth_url, state)
    """
    flow = get_google_oauth_flow()
    if not flow:
        return "", ""

    # Generate state token for CSRF protection
    state = secrets.token_urlsafe(32)

    authorization_url, _ = flow.authorization_url(
        access_type='offline',
        include_granted_scopes='true',
        state=state,
        prompt='select_account'
    )

    return authorization_url, state


def verify_google_token(code: str) -> dict | None:
    """
    Verify the authorization code and return user info.
    Returns: dict with user info or None if verification fails
    """
    try:
        flow = get_google_oauth_flow()
        if not flow:
            return None

        # Exchange authorization code for credentials
        flow.fetch_token(code=code)

        credentials = flow.credentials

        # Verify the ID token
        idinfo = id_token.verify_oauth2_token(
            credentials.id_token,
            google_requests.Request(),
            GOOGLE_CLIENT_ID
        )

        # Extract user information
        user_info = {
            "email": idinfo.get("email"),
            "name": idinfo.get("name"),
            "picture": idinfo.get("picture"),
            "google_id": idinfo.get("sub"),
            "verified_email": idinfo.get("email_verified", False)
        }

        return user_info

    except Exception as e:
        st.error(f"Error verifying Google token: {e}")
        return None


def init_google_oauth_session():
    """Initialize session state variables for Google OAuth."""
    if "google_oauth_state" not in st.session_state:
        st.session_state["google_oauth_state"] = None
    if "google_auth_url" not in st.session_state:
        st.session_state["google_auth_url"] = None
