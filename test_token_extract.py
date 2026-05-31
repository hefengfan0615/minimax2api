"""
Test JWT token extraction functionality
"""

import asyncio
import sys
from auto_login import login_with_credentials


async def test_login():
    """Test login and token extraction."""
    # 从命令行参数获取账号密码，或者使用默认值
    username = sys.argv[1] if len(sys.argv) > 1 else "19065353709"
    password = sys.argv[2] if len(sys.argv) > 2 else "baobao615"
    
    print(f"[*] Testing login with username: {username}")
    print("[*] Password: ******")
    print()
    
    try:
        jwt_token, real_user_id = await login_with_credentials(username, password)
        
        print("=" * 60)
        print("✅ LOGIN SUCCESSFUL!")
        print("=" * 60)
        print(f"👤 Real User ID: {real_user_id}")
        print(f"🔑 JWT Token (first 100 chars):")
        print(f"   {jwt_token[:100]}...")
        print(f"🔑 JWT Token Length: {len(jwt_token)}")
        print()
        
        # 验证 token 格式
        if jwt_token.count('.') == 2:
            print("✅ Token format looks valid (has 2 dots)")
            parts = jwt_token.split('.')
            print(f"   - Header part: {len(parts[0])} chars")
            print(f"   - Payload part: {len(parts[1])} chars")
            print(f"   - Signature part: {len(parts[2])} chars")
        else:
            print("⚠️ Token format may be invalid")
        
        print()
        print("=" * 60)
        print("✅ Test passed! You can now use this token for chatting.")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        print("=" * 60)
        print(f"❌ LOGIN FAILED: {e}")
        print("=" * 60)
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = asyncio.run(test_login())
    sys.exit(0 if success else 1)
