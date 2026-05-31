"""
MiniMax Agent httpx 自动登录脚本
通过账号密码自动获取 JWT Token

使用方法:
    python httpx_login.py 19065353709 baobao615
    
或导入使用:
    from httpx_login import get_minimax_token
    jwt_token, user_id = get_minimax_token("手机号", "密码")
"""

import asyncio
import json
import sys
import time
import urllib.parse
from typing import Dict, Optional, Tuple

import httpx


class MiniMaxHttpxLogin:
    """使用httpx自动登录MiniMax并获取JWT Token"""
    
    # 基础URL配置
    ACCOUNT_BASE = "https://account.minimaxi.com"
    AGENT_BASE = "https://agent.minimaxi.com"
    API_BASE = "https://agent.minimaxi.com"
    
    def __init__(self):
        self.client = httpx.Client(
            timeout=60.0,
            follow_redirects=True,
            headers=self._get_headers()
        )
        self.jwt_token: Optional[str] = None
        self.user_id: Optional[str] = None
    
    def _get_headers(self) -> Dict[str, str]:
        """获取通用请求头"""
        return {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": self.ACCOUNT_BASE,
            "Referer": f"{self.ACCOUNT_BASE}/",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }
    
    def _build_oauth_state(self) -> str:
        """构建OAuth状态参数"""
        import base64
        import json
        import secrets
        
        csrf_token = secrets.token_hex(16)
        state_data = {
            "redirect_uri": f"{self.AGENT_BASE}/",
            "csrf": csrf_token
        }
        state_json = json.dumps(state_data)
        return base64.b64encode(state_json.encode()).decode().rstrip("=")
    
    def device_register(self) -> str:
        """注册设备，获取device_id"""
        url = f"{self.ACCOUNT_BASE}/v1/api/user/device/register"
        
        params = {
            "device_platform": "web",
            "biz_id": "3",
            "app_id": "3001",
            "version_code": "22201",
            "unix": str(int(time.time() * 1000)),
            "timezone_offset": "0",
            "lang": "zh",
            "sys_language": "zh",
            "uuid": self._generate_uuid(),
            "device_id": "0",
            "os_name": "h5",
            "browser_name": "chrome",
            "device_memory": "8",
            "cpu_core_num": "4",
            "browser_language": "en-US",
            "browser_platform": "MacIntel",
            "screen_width": "1920",
            "screen_height": "1080",
            "client": "web"
        }
        
        response = self.client.post(url, params=params, json={})
        
        if response.status_code == 200:
            data = response.json()
            if data.get("base_resp", {}).get("status_code") == 0:
                device_id = data.get("data", {}).get("device_id", "")
                print(f"✓ 设备注册成功: device_id={device_id}")
                return str(device_id)
        
        print(f"⚠ 设备注册响应: {response.status_code} - {response.text[:200]}")
        return ""
    
    def _generate_uuid(self) -> str:
        """生成UUID"""
        import uuid
        return str(uuid.uuid4())
    
    def password_login(self, phone: str, password: str, state: str) -> Tuple[bool, str]:
        """
        使用密码登录
        
        Args:
            phone: 手机号
            password: 密码
            state: OAuth状态参数
        
        Returns:
            (是否成功, 错误信息或ticket)
        """
        url = f"{self.ACCOUNT_BASE}/v1/api/auth/login"
        
        # 构建登录重定向URL
        login_redirect = urllib.parse.quote(
            f"/oauth2/authorize?client_id=agent-minimax"
            f"&redirect_uri=https%3A%2F%2F{self.AGENT_BASE}%2Fauth%2Fcallback"
            f"&response_type=code&state={state}",
            safe=""
        )
        
        payload = {
            "account": phone,
            "password": password,
            "login_type": 2,
            "login_redirect": login_redirect,
            "captcha": "",
            "device_id": ""
        }
        
        headers = {
            **self._get_headers(),
            "Content-Type": "application/json",
            "X-Csrf-Token": state[:32] if len(state) >= 32 else state
        }
        
        response = self.client.post(url, json=payload, headers=headers)
        
        print(f"[*] 登录请求状态: {response.status_code}")
        
        if response.status_code == 200:
            data = response.json()
            
            # 检查是否有ticket
            if "ticket" in data:
                return True, data["ticket"]
            
            # 检查状态码
            base_resp = data.get("base_resp", {})
            if base_resp.get("status_code") == 0:
                return True, "success"
            
            return False, base_resp.get("status_msg", "未知错误")
        
        return False, f"HTTP {response.status_code}"
    
    def exchange_code_for_token(self, code: str) -> bool:
        """
        使用授权码换取访问令牌
        
        Args:
            code: 授权码
        
        Returns:
            是否成功
        """
        url = f"{self.AGENT_BASE}/auth/callback"
        
        params = {
            "code": code,
            "state": self._build_oauth_state()
        }
        
        response = self.client.get(url, params=params, follow_redirects=True)
        
        if response.status_code == 200:
            # 检查是否设置了cookies或localStorage
            cookies = self.client.cookies
            
            # 尝试从URL参数或响应中提取token
            final_url = str(response.url)
            
            # 打印一些调试信息
            print(f"[*] Callback响应URL: {final_url}")
            
            # 尝试从响应HTML或JS中提取token
            text = response.text
            
            # 查找token
            import re
            token_match = re.search(r'token["\s:=]+([a-zA-Z0-9_\-\.]+)', text)
            if token_match:
                potential_token = token_match.group(1)
                if len(potential_token) > 100:
                    self.jwt_token = potential_token
                    print(f"✓ 从页面内容找到Token: {potential_token[:50]}...")
                    return True
            
            # 检查cookies
            for cookie in self.client.cookies:
                cookie_value = self.client.cookies[cookie]
                if cookie_value and len(cookie_value) > 100:
                    if "." in cookie_value:
                        self.jwt_token = cookie_value
                        print(f"✓ 从Cookie找到Token: {cookie_value[:50]}...")
                        return True
            
            print(f"⚠ 未找到Token, URL长度: {len(final_url)}")
            return False
        
        return False
    
    def get_token_from_api(self) -> bool:
        """
        从API请求中获取token
        """
        # 访问主页触发token设置
        response = self.client.get(f"{self.AGENT_BASE}/", follow_redirects=True)
        
        print(f"[*] 主页响应状态: {response.status_code}")
        
        # 从cookies中查找token
        cookies = dict(self.client.cookies)
        for name, value in cookies.items():
            if "token" in name.lower() or len(value) > 200:
                if "." in value:
                    self.jwt_token = value
                    print(f"✓ 从Cookie找到Token: {value[:50]}...")
                    return True
        
        # 尝试从localStorage (需要JS执行，这里无法获取)
        # 所以我们尝试访问一个需要认证的API
        try:
            config_url = (
                f"{self.AGENT_BASE}/v1/api/config/web/common_config"
                f"?filter=agent_config&device_platform=web&biz_id=3&app_id=3001"
            )
            response = self.client.get(config_url)
            
            if response.status_code == 200:
                data = response.json()
                # 检查响应中是否包含用户信息
                if "user_id" in str(data):
                    print(f"✓ API响应包含用户信息")
                    return True
        except:
            pass
        
        return False
    
    def extract_token_from_requests(self) -> Optional[str]:
        """
        尝试从请求历史中提取token
        """
        # 由于httpx不保留历史，我们需要用另一种方式
        # 再次访问需要token的API
        test_endpoints = [
            "/archon/api/v1/config",
            "/v1/api/config/web/common_config",
            "/api/user/profile"
        ]
        
        for endpoint in test_endpoints:
            url = f"{self.AGENT_BASE}{endpoint}"
            try:
                response = self.client.get(url)
                
                # 从URL中提取token（如果重定向包含token）
                if "token=" in response.url:
                    from urllib.parse import parse_qs, urlparse
                    parsed = urlparse(str(response.url))
                    params = parse_qs(parsed.query)
                    if "token" in params:
                        token = params["token"][0]
                        self.jwt_token = token
                        print(f"✓ 从重定向URL找到Token!")
                        return token
                
                # 从响应头提取
                auth_header = response.headers.get("authorization", "")
                if auth_header.startswith("Bearer "):
                    token = auth_header[7:]
                    self.jwt_token = token
                    print(f"✓ 从Authorization头找到Token!")
                    return token
                    
            except Exception as e:
                print(f"⚠ {endpoint} 请求失败: {e}")
        
        return None
    
    def parse_jwt_payload(self, token: str) -> Optional[Dict]:
        """解析JWT payload获取用户信息"""
        try:
            import base64
            parts = token.split(".")
            if len(parts) != 3:
                return None
            
            payload_b64 = parts[1]
            # 添加padding
            padding = 4 - (len(payload_b64) % 4)
            if padding != 4:
                payload_b64 += "=" * padding
            
            decoded = base64.b64decode(payload_b64).decode("utf-8")
            payload = json.loads(decoded)
            
            self.user_id = payload.get("user", {}).get("id", "")
            return payload
        except Exception as e:
            print(f"⚠ JWT解析失败: {e}")
            return None
    
    def login(self, phone: str, password: str) -> Tuple[bool, str, str]:
        """
        执行完整的登录流程
        
        Args:
            phone: 手机号
            password: 密码
        
        Returns:
            (是否成功, jwt_token, user_id)
        """
        print("=" * 60)
        print("🚀 MiniMax httpx 自动登录")
        print("=" * 60)
        print(f"\n[*] 准备登录账号: {phone}")
        
        # Step 1: 注册设备
        print("\n[1/4] 注册设备...")
        self.device_register()
        
        # Step 2: 构建OAuth状态
        print("\n[2/4] 构建OAuth状态...")
        state = self._build_oauth_state()
        print(f"✓ 状态参数已生成")
        
        # Step 3: 密码登录
        print("\n[3/4] 执行密码登录...")
        success, result = self.password_login(phone, password, state)
        
        if not success:
            print(f"\n❌ 登录失败: {result}")
            return False, "", ""
        
        print(f"✓ 登录请求成功")
        
        # Step 4: 获取Token
        print("\n[4/4] 获取JWT Token...")
        
        # 尝试多种方式获取token
        token_found = False
        
        # 方式1: 访问主页
        print("   尝试方式1: 访问主页...")
        if self.get_token_from_api():
            token_found = True
        
        # 方式2: 从API请求提取
        if not token_found:
            print("   尝试方式2: 从API请求提取...")
            if self.extract_token_from_requests():
                token_found = True
        
        if not self.jwt_token:
            print("\n❌ 无法自动获取JWT Token")
            print("\n💡 建议:")
            print("   1. 使用浏览器登录 https://agent.minimaxi.com")
            print("   2. 在开发者工具Network中查找token参数")
            print("   3. 手动复制token")
            return False, "", ""
        
        # 解析token获取user_id
        print("\n[*] 解析Token信息...")
        payload = self.parse_jwt_payload(self.jwt_token)
        
        if payload:
            print(f"✓ Token解析成功")
            print(f"   User ID: {self.user_id}")
            print(f"   Token长度: {len(self.jwt_token)}")
        
        print("\n" + "=" * 60)
        print("✅ 登录成功!")
        print("=" * 60)
        print(f"\n🔑 JWT Token:")
        print(f"   {self.jwt_token[:80]}...")
        print(f"\n👤 User ID: {self.user_id}")
        print()
        
        return True, self.jwt_token, self.user_id
    
    def close(self):
        """关闭HTTP客户端"""
        self.client.close()


def get_minimax_token(phone: str, password: str) -> Tuple[str, str]:
    """
    便捷函数：使用账号密码获取MiniMax JWT Token
    
    Args:
        phone: 手机号
        password: 密码
    
    Returns:
        (jwt_token, user_id)
    
    Raises:
        RuntimeError: 登录失败时抛出
    
    Example:
        jwt_token, user_id = get_minimax_token("19012345678", "mypassword")
    """
    login = MiniMaxHttpxLogin()
    try:
        success, token, user_id = login.login(phone, password)
        if not success:
            raise RuntimeError("登录失败，请检查账号密码或网络连接")
        return token, user_id
    finally:
        login.close()


async def async_get_minimax_token(phone: str, password: str) -> Tuple[str, str]:
    """
    异步版本的便捷函数
    
    Example:
        jwt_token, user_id = await async_get_minimax_token("19012345678", "mypassword")
    """
    import asyncio
    
    def _sync_login():
        return get_minimax_token(phone, password)
    
    return await asyncio.to_thread(_sync_login)


def main():
    """命令行入口"""
    if len(sys.argv) < 3:
        print("使用方法: python httpx_login.py <手机号> <密码>")
        print("示例: python httpx_login.py 19012345678 mypassword")
        sys.exit(1)
    
    phone = sys.argv[1]
    password = sys.argv[2]
    
    try:
        token, user_id = get_minimax_token(phone, password)
        print("\n✅ 可以使用以下token进行聊天:")
        print(f"Token: {token}")
        print(f"UserID: {user_id}")
    except RuntimeError as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
