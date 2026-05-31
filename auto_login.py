#!/usr/bin/env python3
"""
MiniMax账号自动登录脚本
使用账号密码自动登录MiniMax并配置到系统中
"""

import asyncio
import httpx
import json

BASE_URL = "http://localhost:8000"

async def auto_login_minimax(mobile: str, password: str):
    """
    自动登录MiniMax并配置账号
    
    Args:
        mobile: 手机号
        password: 密码
    """
    print(f"正在使用账号 {mobile} 登录MiniMax...")
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        # 调用自动登录API
        try:
            response = await client.post(
                f"{BASE_URL}/api/minimax/login",
                json={
                    "mobile": mobile,
                    "password": password
                },
                headers={
                    "Content-Type": "application/json"
                }
            )
            
            if response.status_code == 200:
                result = response.json()
                if result.get("success"):
                    print("\n✅ 登录成功！")
                    print(f"   账号名称: {result.get('account_name')}")
                    print(f"   用户ID: {result.get('user_id')}")
                    print(f"   用户信息: {json.dumps(result.get('user_info', {}), ensure_ascii=False, indent=6)}")
                    print(f"\n✅ 账号已自动配置到系统中")
                    
                    # 获取配置信息
                    config_resp = await client.get(f"{BASE_URL}/api/config")
                    if config_resp.status_code == 200:
                        config = config_resp.json()
                        print(f"\n当前配置的账号数量: {len(config.get('accounts', []))}")
                        for i, acc in enumerate(config.get('accounts', [])):
                            print(f"  账号 {i+1}: {acc.get('name')} ({acc.get('auth_mode')})")
                else:
                    print(f"\n❌ 登录失败: {result.get('error', '未知错误')}")
            else:
                print(f"\n❌ 请求失败: HTTP {response.status_code}")
                print(f"   响应: {response.text[:500]}")
                
        except Exception as e:
            print(f"\n❌ 发生错误: {str(e)}")

async def main():
    # 默认测试账号
    mobile = "19065353709"
    password = "baobao615"
    
    print("="*60)
    print("MiniMax账号自动登录工具")
    print("="*60)
    print(f"目标服务器: {BASE_URL}")
    print(f"手机号: {mobile}")
    print(f"密码: {'*'*len(password)}")
    print("="*60)
    print()
    
    # 检查服务是否运行
    print("正在检查服务状态...")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            health_resp = await client.get(f"{BASE_URL}/health")
            if health_resp.status_code == 200:
                print("✅ 服务运行正常")
                print()
                await auto_login_minimax(mobile, password)
            else:
                print(f"❌ 服务状态异常: HTTP {health_resp.status_code}")
                print(f"   请先启动服务: python minimax_all_in_one.py")
    except Exception as e:
        print(f"❌ 无法连接到服务: {str(e)}")
        print(f"   请先启动服务: python minimax_all_in_one.py")

if __name__ == "__main__":
    asyncio.run(main())
