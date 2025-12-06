
# google_oauth.py — Google OAuth 2.0 helper functions
# This module handles "Sign in with Google" functionality using OAuth 2.0 protocol

# IMPORTS 
import streamlit as st  # For session state and error messages
from google_auth_oauthlib.flow import Flow  # OAuth 2.0 flow handler
from google.oauth2 import id_token  # For verifying JWT tokens from Google
from google.auth.transport import requests as google_requests  # HTTP client for Google API
import secrets  # Cryptographically secure random number generator
import os  # For environment variables

# CONFIGURATION: Try to load from app_secrets.py, fallback to environment variables 
try:
    # Attempt to import from local secrets file (development)
    from app_secrets import (
        GOOGLE_CLIENT_ID,       # Your app's client ID from Google Cloud Console
        GOOGLE_CLIENT_SECRET,   # Your app's client secret (keep this private!)
        GOOGLE_REDIRECT_URI,    # Where Google redirects after authentication
        GOOGLE_SCOPES          # What permissions we're requesting
    )
except ImportError:
    # If app_secrets.py doesn't exist, use environment variables (production)
    GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
    GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
    GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "http://localhost:8501")
    
    # OAuth scopes define what data we can access from user's Google account
    GOOGLE_SCOPES = [
        "openid",  # Required for OAuth 2.0 authentication
        "https://www.googleapis.com/auth/userinfo.email",  # Access to user's email
        "https://www.googleapis.com/auth/userinfo.profile"  # Access to user's name and picture
    ]


# AVAILABILITY CHECK 
def google_oauth_available() -> bool:
    """
    Check if Google OAuth credentials are properly configured.
    Returns True only if both client ID and secret are present.
    """
    # Both credentials must exist for OAuth to work
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)


#  OAUTH FLOW FACTORY 
def get_google_oauth_flow():
    """
    Create and return a Google OAuth Flow instance.
    The Flow object manages the OAuth 2.0 authentication process.
    Returns None if credentials aren't configured.
    """
    # Check if credentials are available
    if not google_oauth_available():
        return None
    
    # Configure OAuth client settings in the format Google expects
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,  # Identifies your app to Google
            "client_secret": GOOGLE_CLIENT_SECRET,  # Proves your app's identity
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",  # Where users log in
            "token_uri": "https://oauth2.googleapis.com/token",  # Where we exchange code for token
            "redirect_uris": [GOOGLE_REDIRECT_URI],  # Where Google sends users back
        }
    }
    
    # Create Flow object with configuration
    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=GOOGLE_SCOPES,  # What permissions we're requesting
        redirect_uri=GOOGLE_REDIRECT_URI  # Must match what's registered in Google Console
    )
    
    return flow


# GENERATE AUTHORIZATION URL 
def get_google_auth_url() -> tuple[str, str]:
    """
    Generate Google OAuth authorization URL that users will visit to sign in.
    
    Returns: 
        (auth_url, state) - URL to redirect user to, and CSRF protection token
    
    The state token prevents Cross-Site Request Forgery (CSRF) attacks.
    """
    # Get OAuth flow object
    flow = get_google_oauth_flow()
    if not flow:
        return "", ""  # Return empty strings if OAuth not configured
    
    # CSRF PROTECTION: Generate random state token 
    # This is a cryptographically secure random 32-byte token
    # URL-safe means it uses characters safe for URLs (A-Z, a-z, 0-9, -, _)
    state = secrets.token_urlsafe(32)
    
    # Generate authorization URL with parameters
    authorization_url, _ = flow.authorization_url(
        access_type='offline',  # Request refresh token (for long-term access)
        include_granted_scopes='true',  # Include previously granted scopes
        state=state,  # CSRF protection token (Google will return this to us)
        prompt='select_account'  # Force account selection (better UX)
    )
    
    # Return both the URL (to redirect user) and state (to verify later)
    return authorization_url, state


# VERIFY AUTHORIZATION CODE AND GET USER INFO 
def verify_google_token(code: str) -> dict | None:
    """
    Verify the authorization code from Google and extract user information.
    
    Args:
        code: Authorization code returned by Google after user logs in
    
    Returns:
        dict with user info (email, name, picture, google_id) or None if verification fails
    
    This function performs several security checks:
    1. Exchanges authorization code for access token (HTTPS request)
    2. Verifies JWT signature using Google's public keys (RSA crypto)
    3. Validates token hasn't expired
    4. Confirms token is for our app (audience check)
    """
    try:
        # Get OAuth flow object
        flow = get_google_oauth_flow()
        if not flow:
            return None
        
        #  STEP 1: Exchange authorization code for credentials 
        # This makes an HTTPS POST request to Google's token endpoint
        # Google verifies the code and returns:
        # - Access token (for API requests)
        # - ID token (JWT with user info)
        # - Optionally: Refresh token (for long-term access)
        flow.fetch_token(code=code)
        credentials = flow.credentials
        
        # STEP 2: Verify the ID token (JWT) 
        # This performs cryptographic verification:
        # - Checks RSA signature using Google's public keys
        # - Validates expiration time (exp claim)
        # - Confirms audience matches our client ID (aud claim)
        # - Verifies issuer is Google (iss claim)
        idinfo = id_token.verify_oauth2_token(
            credentials.id_token,  # JWT token to verify
            google_requests.Request(),  # HTTP client for fetching Google's public keys
            GOOGLE_CLIENT_ID  # Expected audience (prevents token reuse by other apps)
        )
        
        # STEP 3: Extract user information from verified token 
        # The token contains "claims" (key-value pairs) about the user
        user_info = {
            "email": idinfo.get("email"),  # User's email address
            "name": idinfo.get("name"),  # User's full name
            "picture": idinfo.get("picture"),  # URL to profile picture
            "google_id": idinfo.get("sub"),  # Unique Google user ID (subject claim)
            "verified_email": idinfo.get("email_verified", False)  # Has Google verified this email?
        }
        
        return user_info
        
    except Exception as e:
        # If anything goes wrong (invalid code, expired token, network error, etc.)
        # show error message and return None
        st.error(f"Error verifying Google token: {e}")
        return None


# SESSION STATE INITIALIZATION 
def init_google_oauth_session():
    """
    Initialize session state variables for Google OAuth.
    
    Streamlit session state persists data across reruns.
    We use it to store:
    - google_oauth_state: The CSRF token we generated
    - google_auth_url: The authorization URL (optional caching)
    """
    # Check if state variable exists, if not create it
    if "google_oauth_state" not in st.session_state:
        st.session_state["google_oauth_state"] = None
    
    # Check if auth URL variable exists, if not create it
    if "google_auth_url" not in st.session_state:
        st.session_state["google_auth_url"] = None


#  HOW THIS ALL WORKS TOGETHER 
"""
OAUTH 2.0 FLOW (Step by step):

1. USER CLICKS "SIGN IN WITH GOOGLE"
    App calls get_google_auth_url()
   Generates random state token (CSRF protection)
    Creates authorization URL
    Redirects user to Google's login page

2. USER LOGS IN AT GOOGLE
    User enters Google credentials (password, 2FA, etc.)
    User grants permissions to your app
    Google validates everything

3. GOOGLE REDIRECTS BACK TO YOUR APP
    URL contains authorization code + state parameter
    Example: http://localhost:8501/?code=ABC123&state=XYZ789

4. APP VERIFIES AND EXCHANGES CODE
    App calls verify_google_token(code)
    Exchanges code for access token (HTTPS request)
    Verifies JWT signature (RSA crypto)
    Extracts user info (email, name, picture)

5. APP CREATES USER SESSION
    Stores user info in database
    Sets session state (logged_in = True)
    Redirects to main app

SECURITY FEATURES:
- HTTPS encrypts all communication
- State token prevents CSRF attacks
- JWT signature prevents token forgery
- Short-lived tokens (expire after 1 hour typically)
- No password ever touches your app
"""


# CRYPTOGRAPHIC SECURITY NOTES 
"""
1. CSRF PROTECTION (State Token):
   - Uses secrets.token_urlsafe(32) → 256 bits of entropy
   - Approximately 2^256 = 10^77 possible values
   - Practically impossible to guess (more combinations than atoms in universe)
   - URL-safe encoding (Base64 variant) allows use in query parameters

2. JWT VERIFICATION (ID Token):
   - Uses RSA-256 asymmetric encryption
   - Google signs token with private key
   - App verifies signature with Google's public key
   - Ensures token wasn't modified or forged
   
3. HTTPS/TLS:
   - All requests use HTTPS (implicit in OAuth 2.0)
   - Prevents man-in-the-middle attacks
   - Encrypts authorization codes and tokens in transit

4. TOKEN EXPIRY:
   - ID tokens expire after ~1 hour
   - Forces re-authentication for security
   - Refresh tokens can be used for extended sessions
"""


# === COMMON ISSUES AND TROUBLESHOOTING ===
"""
1. "OAuth credentials not configured"
   → Check GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set
   → Verify they're correct values from Google Cloud Console

2. "Redirect URI mismatch"
   → GOOGLE_REDIRECT_URI must EXACTLY match what's registered in Google Console
   → Include http:// or https:// and port number if applicable
   → Example: http://localhost:8501 (note: no trailing slash)

3. "Token verification failed"
   → Token may have expired (older than 1 hour)
   → Check system clock is accurate (JWT validation uses timestamps)
   → Ensure GOOGLE_CLIENT_ID matches the one token was issued for

4. "CSRF token mismatch"
   → State parameter returned by Google doesn't match what we sent
   → Possible CSRF attack or session expired
   → Regenerate state token and try again
"""


# === GOOGLE CLOUD CONSOLE SETUP REQUIRED ===
"""
Before this code works, you must:

1. Go to: https://console.cloud.google.com/
2. Create a new project (or select existing)
3. Enable "Google+ API" or "Google Identity"
4. Go to "Credentials" section
5. Create "OAuth 2.0 Client ID"
6. Choose "Web application"
7. Add authorized redirect URIs:
   - http://localhost:8501 (development)
   - https://yourdomain.com (production)
8. Copy Client ID and Client Secret
9. Store in app_secrets.py or environment variables

Example app_secrets.py:
```python
GOOGLE_CLIENT_ID = "123456789-abcdefg.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "GOCSPX-AbCdEfGhIjKlMnOpQrStUvWxYz"
GOOGLE_REDIRECT_URI = "http://localhost:8501"
GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile"
]
```
"""
