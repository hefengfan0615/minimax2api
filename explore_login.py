import requests
import json

# 先尝试获取登录页面
base_url = "https://agent.minimaxi.com"

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
})

try:
    # 获取首页
    print("正在访问首页...")
    response = session.get(base_url)
    print(f"状态码: {response.status_code}")
    print(f"Response 长度: {len(response.text)}")
    print(f"Headers: {dict(response.headers)}")
    
    # 尝试查找可能的登录相关 API 路径
    print("\n查找可能的登录 API 路径...")
    common_login_paths = [
        "/api/login",
        "/api/auth/login",
        "/api/user/login",
        "/api/v1/login",
        "/auth/login",
        "/login"
    ]
    
    for path in common_login_paths:
        try:
            test_url = base_url + path
            test_response = session.get(test_url, allow_redirects=False)
            print(f"  {path}: 状态码 {test_response.status_code}")
        except Exception as e:
            print(f"  {path}: 错误 - {e}")
            
    print("\n探索完成!")
    
except Exception as e:
    print(f"错误: {e}")
