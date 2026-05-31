# MiniMax Agent 自动登录 & 聊天

现在你可以直接使用账号密码登录，无需手动复制 JWT token 了！

## 📁 新增文件

- `auto_login.py` - 自动登录模块，通过账号密码获取 JWT token
- `chat_example.py` - 聊天示例程序

## 🚀 快速开始

### 1. 单次聊天模式

```bash
python chat_example.py -u 19065353709 -p baobao615 -m "你好，请介绍一下自己"
```

### 2. 交互式聊天模式

```bash
python chat_example.py -u 19065353709 -p baobao615
```

### 3. 指定模型

```bash
python chat_example.py -u 19065353709 -p baobao615 --model MiniMax-M2.7-highspeed -m "你好"
```

## 💻 在代码中使用

### 自动登录获取 Token

```python
import asyncio
from auto_login import login_with_credentials

async def main():
    # 登录并获取 JWT token 和 user_id
    jwt_token, user_id = await login_with_credentials("19065353709", "baobao615")
    print(f"User ID: {user_id}")
    print(f"JWT Token: {jwt_token}")

asyncio.run(main())
```

### 完整聊天

```python
import asyncio
from auto_login import login_with_credentials
from minimax_adapter import web_agent_chat

async def chat():
    # 1. 登录
    jwt_token, user_id = await login_with_credentials("19065353709", "baobao615")
    
    # 2. 聊天
    messages = [{"role": "user", "content": "你好"}]
    response = await web_agent_chat("MiniMax-M2.7", messages, jwt_token, user_id)
    
    print(response["choices"][0]["message"]["content"])

asyncio.run(chat())
```

## 🎯 可用模型

- `MiniMax-M2.7` (默认)
- `MiniMax-M2.7-highspeed`
- `MiniMax-M2.5`
- `MiniMax-M2.5-highspeed`
- `MiniMax-M2.1`
- `MiniMax-M2.1-highspeed`

## 🔐 工作原理

1. 使用账号密码调用 `/v1/auth/password_login` 获取 access_token
2. 使用 access_token 调用 `/v1/user/profile` 获取 JWT token 和 user_id
3. 使用 JWT token 进行后续的聊天 API 调用

## 📝 注意事项

- 请妥善保管你的账号密码
- Token 有效期较长，但会自动刷新
- 建议使用环境变量或配置文件存储敏感信息
