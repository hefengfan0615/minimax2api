# MiniMax Agent 自动登录 & 聊天工具

现在你可以使用账号密码直接和 MiniMax Agent 聊天了！

## 📦 新增文件

1. **`auto_login.py`** - 自动登录模块
   - 支持浏览器自动化登录 (Playwright)
   - 支持手动输入 Token 模式
   - JWT Token 解析功能

2. **`chat_example.py`** - 聊天示例程序
   - 单次消息模式
   - 交互式聊天模式
   - 支持直接使用已有 Token

3. **`test_chat.py`** - 测试脚本
   - 快速测试聊天功能
   - Token 验证

## 🚀 快速开始

### 前置要求

```bash
# 安装依赖
pip install -r requirements.txt

# 可选：安装 Playwright 用于浏览器自动化登录
pip install playwright
playwright install chromium
```

### 方式一：使用已有的 JWT Token（推荐，最简单）

如果你已经有 JWT Token 和 User ID：

```bash
# 单次聊天
python chat_example.py \
  --token "你的JWT_TOKEN" \
  --user-id "你的USER_ID" \
  --message "你好，请介绍一下你自己"

# 交互式聊天
python chat_example.py \
  --token "你的JWT_TOKEN" \
  --user-id "你的USER_ID"
```

### 方式二：获取 JWT Token

1. 打开 https://agent.minimaxi.com
2. 登录你的账号
3. 打开浏览器开发者工具 (F12)
4. 切换到 "Network" (网络) 标签
5. 刷新页面
6. 点击任意 API 请求
7. 在请求 URL 中找到 `?token=...` 参数
8. 复制这个 JWT Token

### 方式三：浏览器自动化登录 (需要 Playwright)

```bash
# 安装 Playwright
pip install playwright
playwright install chromium

# 使用账号密码登录聊天
python chat_example.py \
  --username "190xxxxxxx" \
  --password "your_password" \
  --message "你好"
```

## 💻 在代码中使用

### 基本聊天示例

```python
import asyncio
from auto_login import login_with_credentials
from minimax_adapter import web_agent_chat

async def main():
    # 方式一：使用已有 token
    jwt_token = "你的_JWT_TOKEN"
    user_id = "你的_USER_ID"
    
    # 方式二：自动登录
    # jwt_token, user_id = await login_with_credentials("手机号", "密码")
    
    # 发送消息
    messages = [{"role": "user", "content": "你好，请介绍一下你自己"}]
    response = await web_agent_chat(
        "MiniMax-M2.7", 
        messages, 
        jwt_token, 
        user_id
    )
    
    print("AI回复:", response["choices"][0]["message"]["content"])

asyncio.run(main())
```

### 多轮对话

```python
import asyncio
from minimax_adapter import web_agent_chat

async def multi_turn_chat():
    jwt_token = "你的_JWT_TOKEN"
    user_id = "你的_USER_ID"
    
    # 维护对话历史
    conversation = []
    
    # 第一轮
    conversation.append({"role": "user", "content": "什么是Python?"})
    response = await web_agent_chat("MiniMax-M2.7", conversation, jwt_token, user_id)
    ai_response = response["choices"][0]["message"]["content"]
    conversation.append({"role": "assistant", "content": ai_response})
    print("AI:", ai_response)
    
    # 第二轮
    conversation.append({"role": "user", "content": "能举个例子吗?"})
    response = await web_agent_chat("MiniMax-M2.7", conversation, jwt_token, user_id)
    print("AI:", response["choices"][0]["message"]["content"])

asyncio.run(multi_turn_chat())
```

## 🎯 可用模型

- `MiniMax-M2.7` (默认，最强大)
- `MiniMax-M2.7-highspeed` (快速版)
- `MiniMax-M2.5`
- `MiniMax-M2.5-highspeed`
- `MiniMax-M2.1`
- `MiniMax-M2.1-highspeed`

## 📋 完整 API 示例

```python
import asyncio
from minimax_adapter import web_agent_chat, web_agent_chat_stream

async def example():
    jwt_token = "..."
    user_id = "..."
    
    # 非流式响应
    response = await web_agent_chat(
        model="MiniMax-M2.7",
        messages=[{"role": "user", "content": "你好"}],
        jwt_token=jwt_token,
        real_user_id=user_id
    )
    print(response["choices"][0]["message"]["content"])
    
    # 流式响应
    async for chunk in web_agent_chat_stream(
        model="MiniMax-M2.7",
        messages=[{"role": "user", "content": "你好"}],
        jwt_token=jwt_token,
        real_user_id=user_id
    ):
        print(chunk, end="")

asyncio.run(example())
```

## 🔐 安全建议

1. **不要**将 Token 提交到代码仓库
2. 使用环境变量存储敏感信息
3. 定期更换密码和 Token

## ⚠️ 注意事项

- JWT Token 有有效期，过期后需要重新获取
- 建议使用环境变量或配置文件管理敏感信息
- 遵守 MiniMax 使用条款

## 🐛 故障排除

### Token 无效或 401 错误
- Token 可能已过期，重新获取
- 检查 User ID 是否正确

### 登录失败
- 确认账号密码正确
- 检查网络连接
- 尝试手动获取 Token 方式
