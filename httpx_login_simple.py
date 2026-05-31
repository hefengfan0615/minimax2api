"""
MiniMax httpx 简化版自动登录脚本
直接模拟浏览器登录流程获取Token

使用方法:
    python httpx_login_simple.py 19065353709 baobao615
"""

import asyncio
import json
import re
import sys
import time
import urllib.parse
from typing import Dict, Optional, Tuple

import httpx


class MiniMaxSimpleLogin:
    """简化版MiniMax登录器"""
    
    ACCOUNT_BASE = "https://account.minimaxi.com"
    AGENT_BASE = "https://agent.minimaxi.com"
    
    def __init__(self):
        self.client = httpx.Client(
            timeout=60.0,
            follow_redirects=False,  # 不自动重定向，我们需要捕获重定向URL
            headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
            }
        )
        self.jwt_token: Optional[str] = None
        self.user_id: Optional[str] = None
    
    def get_login_page(self) -> str:
        """获取登录页面"""
        url = f"{self.ACCOUNT_BASE}/unified-login"
        params = {
            "login_redirect": urllib.parse.quote(
                f"/oauth2/authorize?client_id=agent-minimax"
                f"&redirect_uri=https%3A%2F%2F{self.AGENT_BASE}%2Fauth%2Fcallback"
                f"&response_type=code&state=test",
                safe=""
            )
        }
        
        response = self.client.get(url, params=params)
        return response.text
    
    def submit_login(self, phone: str, password: str) -> Tuple[bool, str]:
        """
        提交登录表单
        
        Returns:
            (是否成功, 重定向URL或错误信息)
        """
        url = f"{self.ACCOUNT_BASE}/unified-login"
        
        # 构建表单数据
        data = {
            "account": phone,
            "password": password,
            "login_type": "2",  # 密码登录
            "captcha": "",
            "device_id": ""
        }
        
        headers = {
            **self.client.headers,
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": url,
            "Origin": self.ACCOUNT_BASE
        }
        
        response = self.client.post(url, data=data, headers=headers)
        
        # 检查是否重定向
        if 300 <= response.status_code < 400:
            location = response.headers.get("Location", "")
            return True, location
        
        # 检查响应内容
        if response.status_code == 200:
            # 可能需要验证码或其他验证
            return False, f"需要额外验证: {response.status_code}"
        
        return False, f"HTTP {response.status_code}"
    
    def exchange_code(self, redirect_url: str) -> Tuple[bool, str]:
        """
        交换授权码获取token
        
        Args:
            redirect_url: 登录后的重定向URL
        
        Returns:
            (是否成功, token或错误信息)
        """
        if not redirect_url:
            return False, "无重定向URL"
        
        # 解析URL获取code
        parsed = urllib.parse.urlparse(redirect_url)
        query_params = urllib.parse.parse_qs(parsed.query)
        
        code = query_params.get("code", [None])[0]
        if not code:
            return False, "重定向URL中无code参数"
        
        print(f"[*] 获取到授权码: {code[:20]}...")
        
        # 使用code访问回调URL
        callback_url = f"{self.AGENT_BASE}/auth/callback"
        params = {"code": code}
        
        response = self.client.get(callback_url, params=params, follow_redirects=True)
        
        print(f"[*] Callback响应状态: {response.status_code}")
        print(f"[*] 最终URL: {response.url}")
        
        # 从URL中提取token
        final_url = str(response.url)
        if "token=" in final_url:
            from urllib.parse import parse_qs, urlparse
            parsed = urlparse(final_url)
            params = parse_qs(parsed.query)
            if "token" in params:
                token = params["token"][0]
                self.jwt_token = token
                return True, token
        
        # 尝试从cookies获取
        for name, value in self.client.cookies.items():
            if len(value) > 200 and "." in value:
                self.jwt_token = value
                return True, value
        
        # 尝试从页面内容提取
        content = response.text
        
        # 查找localStorage.setItem
        token_patterns = [
            r'localStorage\.setItem\([^,]+,\s*["\']([a-zA-Z0-9_\-\.]+)["\']',
            r'"token"\s*:\s*"([a-zA-Z0-9_\-\.]+)"',
            r'token["\s=]+([a-zA-Z0-9_\-\.]+)'
        ]
        
        for pattern in token_patterns:
            matches = re.findall(pattern, content)
            for match in matches:
                if len(match) > 100:
                    self.jwt_token = match
                    return True, match
        
        return False, "无法从响应中提取token"
    
    def parse_jwt(self, token: str) -> Optional[Dict]:
        """解析JWT获取用户信息"""
        try:
            import base64
            
            parts = token.split(".")
            if len(parts) != 3:
                return None
            
            payload_b64 = parts[1]
            padding = 4 - (len(payload_b64) % 4)
            if padding != 4:
                payload_b64 += "=" * padding
            
            decoded = base64.b64decode(payload_b64).decode("utf-8")
            payload = json.loads(decoded)
            
            self.user_id = payload.get("user", {}).get("id", "")
            return payload
        except:
            return None
    
    def login(self, phone: str, password: str) -> Tuple[bool, str, str]:
        """
        执行登录流程
        
        Returns:
            (成功标志, jwt_token, user_id)
        """
        print("=" * 60)
        print("🚀 MiniMax httpx 自动登录")
        print("=" * 60)
        print(f"[*] 账号: {phone}")
        
        # Step 1: 获取登录页面
        print("\n[1/3] 获取登录页面...")
        try:
            self.get_login_page()
            print("✓ 登录页面已加载")
        except Exception as e:
            print(f"✗ 获取登录页面失败: {e}")
            return False, "", ""
        
        # Step 2: 提交登录表单
        print("\n[2/3] 提交登录表单...")
        success, result = self.submit_login(phone, password)
        
        if not success:
            print(f"✗ 登录失败: {result}")
            return False, "", ""
        
        print(f"✓ 登录表单提交成功")
        print(f"[*] 重定向URL: {result[:80]}...")
        
        # Step 3: 交换授权码
        print("\n[3/3] 交换授权码获取Token...")
        success, token = self.exchange_code(result)
        
        if not success:
            print(f"✗ 获取Token失败: {token}")
            print("\n💡 提示: MiniMax可能需要验证码验证")
            print("   请尝试浏览器登录后手动获取Token")
            return False, "", ""
        
        # 解析JWT
        payload = self.parse_jwt(token)
        
        print("\n" + "=" * 60)
        print("✅ 登录成功!")
        print("=" * 60)
        print(f"\n🔑 JWT Token:")
        print(f"   {token[:80]}...")
        print(f"\n👤 User ID: {self.user_id}")
        print()
        
        return True, token, self.user_id
    
    def close(self):
        """关闭客户端"""
        self.client.close()


def get_token(phone: str, password: str) -> Tuple[str, str]:
    """
    获取MiniMax Token的便捷函数
    
    Example:
        token, user_id = get_token("19012345678", "password")
    """
    login = MiniMaxSimpleLogin()
    try:
        success, token, user_id = login.login(phone, password)
        if not success:
            raise RuntimeError("登录失败")
        return token, user_id
    finally:
        login.close()


def main():
    """命令行入口"""
    if len(sys.argv) < 3:
        print("使用方法: python httpx_login_simple.py <手机号> <密码>")
        print("示例: python httpx_login_simple.py 19012345678 mypassword")
        sys.exit(1)
    
    phone = sys.argv[1]
    password = sys.argv[2]
    
    success, token, user_id = get_token(phone, password)
    
    if success:
        print("\n✅ 登录成功!")
        print(f"Token: {token}")
        print(f"UserID: {user_id}")
    else:
        print("\n❌ 登录失败")
        sys.exit(1)


if __name__ == "__main__":
    main()
