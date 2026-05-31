#!/usr/bin/env python3
"""
测试MiniMax登录API
"""
import asyncio
import httpx
import json

async def test_minimax_login():
    """测试MiniMax的各种可能的登录API端点"""
    
    test_endpoints = [
        "https://api.minimaxi.com/api/v1/auth/password/login",
        "https://www.minimaxi.com/api/v1/auth/password/login",
        "https://agent.minimaxi.com/api/v1/auth/password/login",
        "https://api.minimax.com/api/v1/auth/password/login",
    ]
    
    mobile = "19065353709"
    password = "baobao615"
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json",
        "Origin": "https://www.minimaxi.com",
        "Referer": "https://www.minimaxi.com/",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"
    }
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # 先访问主页获取cookies
        print("正在访问主页...")
        await client.get("https://www.minimaxi.com", headers=headers)
        await client.get("https://agent.minimaxi.com", headers=headers)
        
        # 测试不同的登录端点
        for endpoint in test_endpoints:
            print(f"\n测试端点: {endpoint}")
            try:
                response = await client.post(
                    endpoint,
                    json={"mobile": mobile, "password": password},
                    headers=headers
                )
                print(f"  状态码: {response.status_code}")
                if response.status_code == 200:
                    print(f"  响应: {json.dumps(response.json(), ensure_ascii=False, indent=2)}")
                else:
                    print(f"  响应: {response.text[:500]}")
            except Exception as e:
                print(f"  错误: {str(e)}")
        
        # 也测试一下agent.minimaxi.com的其他API
        print("\n\n检查agent.minimaxi.com的其他API...")
        test_agent_apis = [
            "https://agent.minimaxi.com/api/v1/user/profile",
            "https://agent.minimaxi.com/api/v1/auth/status",
        ]
        
        for api in test_agent_apis:
            try:
                response = await client.get(api, headers=headers)
                print(f"\n{api}: {response.status_code}")
                print(f"  响应: {response.text[:300]}")
            except Exception as e:
                print(f"\n{api}: 错误 - {str(e)}")

if __name__ == "__main__":
    asyncio.run(test_minimax_login())
