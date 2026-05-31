"""
Auto-login module for MiniMax Agent (agent.minimaxi.com).
Handles username/password authentication and JWT token retrieval.
"""

import asyncio
import json
import logging
import time
from typing import Dict, Optional, Tuple

import httpx

logger = logging.getLogger("minimax2api.autologin")

# ── Constants ────────────────────────────────────────────────────

ACCOUNT_BASE_URL = "https://account.minimaxi.com"
AGENT_BASE_URL = "https://agent.minimaxi.com"

FAKE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Origin": "https://account.minimaxi.com",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    ),
}


class MiniMaxAutoLogin:
    """Auto-login handler for MiniMax Agent with username/password."""

    def __init__(self):
        self.client = httpx.Client(timeout=60.0, follow_redirects=True)
        self.access_token: Optional[str] = None
        self.refresh_token: Optional[str] = None
        self.jwt_token: Optional[str] = None
        self.user_info: Optional[Dict] = None
        self.token_expiry: float = 0
        self.cookies = {}

    def _set_common_headers(self):
        """Set common headers for requests."""
        self.client.headers.update(FAKE_HEADERS)

    async def login_with_browser(self, username: str, password: str) -> Tuple[str, str]:
        """
        Login using browser automation (more reliable for OAuth flows).
        Falls back to manual mode if browser automation not available.
        """
        try:
            # Try to use playwright if available
            return await self._login_with_playwright(username, password)
        except ImportError:
            logger.warning("Playwright not available, trying simplified login...")
            return await self._login_simplified(username, password)

    async def _login_with_playwright(self, username: str, password: str) -> Tuple[str, str]:
        """Login using Playwright browser automation."""
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            
            try:
                # Go to login page
                logger.info("Navigating to login page...")
                await page.goto(f"{AGENT_BASE_URL}/")
                
                # Click login button
                await page.wait_for_selector("text=登录", timeout=10000)
                await page.click("text=登录")
                
                # Wait for redirect to account page
                await page.wait_for_url("**/unified-login**", timeout=10000)
                
                # Switch to password login
                await page.wait_for_selector("text=密码登录", timeout=10000)
                await page.click("text=密码登录")
                
                # Fill username
                await page.wait_for_selector("input[placeholder*='手机号'], input[placeholder*='邮箱']", timeout=10000)
                await page.fill("input[placeholder*='手机号'], input[placeholder*='邮箱']", username)
                
                # Fill password
                await page.wait_for_selector("input[type='password']", timeout=10000)
                await page.fill("input[type='password']", password)
                
                # Check agreement checkbox if present
                try:
                    await page.click("input[type='checkbox']", timeout=2000)
                except:
                    pass
                
                # Click login button
                await page.click("text=立即登录")
                
                # Wait for redirect back to agent page
                await page.wait_for_url(f"{AGENT_BASE_URL}/**", timeout=30000)
                
                # Extract JWT token from requests or local storage
                logger.info("Extracting JWT token...")
                
                # Try to get token from local storage
                jwt_token = None
                try:
                    local_storage = await page.evaluate("() => JSON.stringify(localStorage)")
                    ls_data = json.loads(local_storage)
                    for key, value in ls_data.items():
                        if "token" in key.lower() or "jwt" in key.lower():
                            if isinstance(value, str) and len(value) > 100:
                                jwt_token = value
                                break
                except:
                    pass
                
                # If not found, wait for a request that contains the token
                if not jwt_token:
                    async with page.expect_request("**/api/**", timeout=30000) as request_info:
                        # Trigger some action that makes an API call
                        await page.reload()
                        request = await request_info.value
                        
                        # Check request URL and headers for token
                        url = request.url
                        if "token=" in url:
                            from urllib.parse import parse_qs, urlparse
                            parsed = urlparse(url)
                            params = parse_qs(parsed.query)
                            if "token" in params:
                                jwt_token = params["token"][0]
                
                if not jwt_token:
                    # Fallback: Try to get from cookies or page context
                    cookies = await page.context.cookies()
                    for cookie in cookies:
                        if "token" in cookie["name"].lower():
                            jwt_token = cookie["value"]
                            break
                
                if not jwt_token:
                    raise RuntimeError("Could not extract JWT token")
                
                # Parse user ID from token
                user_id = self._parse_jwt_user_id(jwt_token)
                
                logger.info(f"Successfully logged in! User ID: {user_id}")
                self.jwt_token = jwt_token
                return jwt_token, user_id
                
            finally:
                await browser.close()

    async def _login_simplified(self, username: str, password: str) -> Tuple[str, str]:
        """
        Simplified login - requires manual token input for now,
        but provides a helper to get started.
        """
        print("\n" + "="*60)
        print("🚀 MiniMax Auto-Login")
        print("="*60)
        print("\nDue to the complex OAuth2 flow, please:")
        print("\n1. Open https://agent.minimaxi.com in your browser")
        print("2. Login with your credentials")
        print("3. Open browser DevTools (F12)")
        print("4. Go to Network tab")
        print("5. Refresh the page and look for any API request")
        print("6. In the request URL, find the 'token=' parameter")
        print("7. Copy that JWT token and paste it below\n")
        
        jwt_token = input("Paste your JWT token here: ").strip()
        
        if not jwt_token:
            raise RuntimeError("No token provided")
        
        user_id = self._parse_jwt_user_id(jwt_token)
        self.jwt_token = jwt_token
        
        print(f"\n✅ Token received! User ID: {user_id}")
        return jwt_token, user_id

    def _parse_jwt_user_id(self, jwt_token: str) -> str:
        """Extract user.id from a MiniMax JWT token payload."""
        try:
            parts = jwt_token.split(".")
            if len(parts) != 3:
                return ""
            payload_b64 = parts[1]
            padding = 4 - (len(payload_b64) % 4)
            if padding != 4:
                payload_b64 += "=" * padding
            import base64
            decoded = base64.b64decode(payload_b64).decode("utf-8")
            payload = json.loads(decoded)
            return payload.get("user", {}).get("id", "")
        except Exception:
            return ""

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
        return await login.login_with_browser(username, password)
    finally:
        login.close()


async def simple_chat_example(username: str, password: str, message: str = "你好"):
    """Simple example: login and send one chat message."""
    from minimax_adapter import web_agent_chat
    
    print(f"\n[1/3] Logging in...")
    jwt_token, user_id = await login_with_credentials(username, password)
    
    print(f"[2/3] Sending message...")
    messages = [{"role": "user", "content": message}]
    response = await web_agent_chat("MiniMax-M2.7", messages, jwt_token, user_id)
    
    print(f"[3/3] Got response!\n")
    print("🤖 AI:", response["choices"][0]["message"]["content"])
    print()
    
    return response


# ── Test ─────────────────────────────────────────────────────────

async def test_login():
    """Test auto-login (requires credentials)."""
    import getpass
    phone = input("Enter phone number: ")
    password = getpass.getpass("Enter password: ")
    
    try:
        jwt_token, user_id = await login_with_credentials(phone, password)
        print(f"\n✅ Login successful!")
        print(f"   User ID: {user_id}")
        print(f"   JWT Token: {jwt_token[:50]}...")
        return True
    except Exception as e:
        print(f"\n❌ Login failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    asyncio.run(test_login())
