"""
Auto-login module for MiniMax Agent (agent.minimaxi.com).
Handles username/password authentication and JWT token retrieval.
"""

import json
import logging
import time
from typing import Dict, Optional, Tuple

import httpx

logger = logging.getLogger("minimax2api.autologin")

# ── Constants ────────────────────────────────────────────────────

MINIMAX_LOGIN_BASE = "https://api.minimax.chat"
FAKE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Origin": "https://agent.minimaxi.com",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    ),
}


class MiniMaxAutoLogin:
    """Auto-login handler for MiniMax Agent with username/password."""

    def __init__(self):
        self.client = httpx.Client(timeout=30.0, follow_redirects=True)
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.jwt_token: Optional[str] = None
        self.user_info: Optional[Dict] = None
        self.token_expiry: float = 0

    def _set_common_headers(self):
        """Set common headers for requests."""
        self.client.headers.update(FAKE_HEADERS)
        self.client.headers["Referer"] = "https://agent.minimaxi.com/"

    async def login(self, username: str, password: str) -> Tuple[str, str]:
        """
        Perform login with username (phone) and password.
        
        Returns (jwt_token, real_user_id).
        """
        try:
            self._set_common_headers()
            
            # Step 1: Try to get initial config (optional)
            try:
                await self._get_initial_config()
            except Exception as e:
                logger.debug("Initial config not needed: %s", e)
            
            # Step 2: Login with phone/password
            login_data = await self._password_login(username, password)
            self.access_token = login_data.get("access_token", "")
            self.refresh_token = login_data.get("refresh_token", "")
            self.user_info = login_data.get("user_info", {})
            
            # Step 3: Get user profile and JWT token
            profile = await self._get_user_profile()
            self.jwt_token = profile.get("jwt_token", "")
            real_user_id = str(profile.get("user_id", ""))
            
            if not self.jwt_token or not real_user_id:
                raise RuntimeError("Failed to get JWT token or user ID from profile")
            
            logger.info("Successfully logged in: user_id=%s", real_user_id)
            return self.jwt_token, real_user_id
            
        except Exception as e:
            logger.error("Login failed: %s", e)
            raise RuntimeError(f"Login failed: {str(e)}")

    async def _get_initial_config(self):
        """Get initial config from agent page."""
        resp = self.client.get("https://agent.minimaxi.com/", follow_redirects=True)
        resp.raise_for_status()

    async def _password_login(self, phone: str, password: str) -> Dict:
        """Login via phone and password."""
        url = f"{MINIMAX_LOGIN_BASE}/v1/auth/password_login"
        
        payload = {
            "phone": phone,
            "password": password,
            "remember_me": True,
        }
        
        headers = {
            **FAKE_HEADERS,
            "Content-Type": "application/json",
        }
        
        resp = self.client.post(url, json=payload, headers=headers)
        
        if resp.status_code != 200:
            raise RuntimeError(f"Password login failed: HTTP {resp.status_code} - {resp.text}")
        
        data = resp.json()
        
        if data.get("base_resp", {}).get("status_code") != 0:
            raise RuntimeError(f"Login rejected: {data.get('base_resp', {}).get('status_msg', 'unknown')}")
        
        return data.get("data", {})

    async def _get_user_profile(self) -> Dict:
        """Get user profile with JWT token."""
        url = f"{MINIMAX_LOGIN_BASE}/v1/user/profile"
        
        headers = {
            **FAKE_HEADERS,
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        
        resp = self.client.get(url, headers=headers)
        
        if resp.status_code != 200:
            raise RuntimeError(f"Get profile failed: HTTP {resp.status_code} - {resp.text}")
        
        data = resp.json()
        
        if data.get("base_resp", {}).get("status_code") != 0:
            raise RuntimeError(f"Get profile rejected: {data.get('base_resp', {}).get('status_msg', 'unknown')}")
        
        return data.get("data", {})

    async def refresh_tokens(self) -> Tuple[str, str]:
        """Refresh access token using refresh token."""
        if not self.refresh_token:
            raise RuntimeError("No refresh token available")
        
        url = f"{MINIMAX_LOGIN_BASE}/v1/auth/refresh_token"
        
        payload = {
            "refresh_token": self.refresh_token,
        }
        
        headers = {
            **FAKE_HEADERS,
            "Content-Type": "application/json",
        }
        
        resp = self.client.post(url, json=payload, headers=headers)
        
        if resp.status_code != 200:
            raise RuntimeError(f"Refresh token failed: HTTP {resp.status_code}")
        
        data = resp.json()
        
        if data.get("base_resp", {}).get("status_code") != 0:
            raise RuntimeError(f"Refresh token rejected: {data.get('base_resp', {}).get('status_msg', 'unknown')}")
        
        new_data = data.get("data", {})
        self.access_token = new_data.get("access_token", "")
        self.refresh_token = new_data.get("refresh_token", "")
        
        # Re-get profile for new JWT
        profile = await self._get_user_profile()
        self.jwt_token = profile.get("jwt_token", "")
        real_user_id = str(profile.get("user_id", ""))
        
        return self.jwt_token, real_user_id

    def close(self):
        """Close the HTTP client."""
        self.client.close()


# ── Convenience functions ────────────────────────────────────────

async def login_with_credentials(username: str, password: str) -> Tuple[str, str]:
    """
    Convenience function to login and get (jwt_token, real_user_id).
    
    Example:
        jwt_token, user_id = await login_with_credentials("13800138000", "mypassword")
    """
    login = MiniMaxAutoLogin()
    try:
        return await login.login(username, password)
    finally:
        login.close()


# ── Test ─────────────────────────────────────────────────────────

async def test_login():
    """Test auto-login (requires credentials)."""
    import getpass
    phone = input("Enter phone number: ")
    password = getpass.getpass("Enter password: ")
    
    try:
        jwt_token, user_id = await login_with_credentials(phone, password)
        print(f"\n✓ Login successful!")
        print(f"  User ID: {user_id}")
        print(f"  JWT Token: {jwt_token[:50]}...")
        return True
    except Exception as e:
        print(f"\n✗ Login failed: {e}")
        return False


if __name__ == "__main__":
    import asyncio
    asyncio.run(test_login())
