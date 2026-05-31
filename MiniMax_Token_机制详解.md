# MiniMax Token 机制详解

## 核心问题回答

### ❓ 需要什么Token？

MiniMax聊天功能需要**JWT Token**（不是API Key），这是MiniMax网页端的认证令牌。

### ❓ Token从哪里来？

从MiniMax官网 https://agent.minimaxi.com 获取，需要：

1. 打开浏览器访问 https://agent.minimaxi.com
2. 登录你的MiniMax账号
3. 打开浏览器开发者工具（F12）
4. 在Console中执行：`console.log(localStorage._token)`
5. 复制输出的JWT Token

### ❓ Token如何工作？

```
用户请求 → 解析Token获取user_id → 注册设备 → 发送消息 → 轮询响应 → 返回结果
```

---

## 两种认证模式对比

| 模式 | auth_mode | Token类型 | 来源 | 适用场景 |
|------|-----------|-----------|------|---------|
| API Key | `api_key` | API Key | MiniMax官网API | 标准API调用 |
| Token | `token` | JWT Token | 网页端获取 | Web Agent功能 |

---

## JWT Token的结构

MiniMax的JWT Token格式：
```
eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE3ODI0Njk5MDIsInVzZXI6eyJpZCI6IjI3NTMxODM4MjExMDQyOTE4NCIsIm5hbWUiOiLnmb3ok50iLCJ...}.8o6_ron6WmBU9F4JIAyPxqFRin5xtXzjLsCjovjy4qo
```

Token分为三部分（用`.`分隔）：
1. **Header** - 算法信息
2. **Payload** - 用户信息（包含user.id等）
3. **Signature** - 签名验证

---

## 完整聊天流程

### 流程图

```
┌─────────────────────────────────────────────────────────┐
│  用户发起聊天请求                                          │
│  POST /v1/chat/completions                              │
│  Authorization: Bearer sk-minimax                       │
└──────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  解析账号配置                                             │
│  从 config.json 获取 auth_mode="token" 的账号             │
│  提取 auth_token (JWT) 和 base_url                       │
└──────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Step 1: 解析JWT获取user_id                              │
│  _parse_jwt_user_id(token)                              │
│  从Token的Payload中提取 "user.id" 字段                    │
│  示例: "275318382110429184"                              │
└──────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Step 2: 注册设备（首次或缓存过期）                        │
│  POST /v1/api/user/device/register                      │
│  参数: token, user_id, random_uuid                      │
│  响应: {deviceId: "xxx", realUserID: "xxx", uuid: "xxx"} │
│  缓存10800秒（3小时）                                    │
└──────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Step 3: 构建签名请求头                                  │
│  _build_signed_headers()                                │
│  包含:                                                   │
│  - token: JWT Token                                      │
│  - x-signature: MD5(timestamp + token + body)           │
│  - yy: MD5(url_encoded + body_json + MD5(timestamp))    │
│  - x-timestamp: 当前Unix时间戳                           │
│  - Query String: 包含token, user_id, device_id等        │
└──────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Step 4: 发送消息                                        │
│  POST /matrix/api/v1/chat/send_msg                      │
│  请求体: {msg_type:1, text:"用户消息", chat_type:1}      │
│  响应: {chat_id: "xxx", msg_id: "xxx"}                  │
└──────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Step 5: 轮询响应（最多240次，每次0.5秒）                │
│  POST /matrix/api/v1/chat/get_chat_detail               │
│  轮询直到: chat_status == 2 且有AI回复                   │
│  响应: {messages: [{msg_type:2, msg_content:"AI回复"}]} │
└──────────────────┬────────────────────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────────────────┐
│  Step 6: 转换为OpenAI格式并返回                          │
│  {                                                     │
│    id: "chatcmpl-xxx",                                  │
│    choices: [{message: {content: "AI回复"}}],           │
│    model: "MiniMax-M2.7"                                │
│  }                                                      │
└─────────────────────────────────────────────────────────┘
```

---

## 配置文件示例

### 配置账号为Token模式

```json
{
  "accounts": [
    {
      "api_key": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "name": "my-account",
      "base_url": "https://agent.minimaxi.com/v1",
      "auth_mode": "token",
      "auth_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
      "is_active": true
    }
  ]
}
```

---

## 为什么需要复杂的签名机制？

MiniMax的Web Agent API使用了类似OAuth的签名机制：

1. **防止Token泄露** - Token在每次请求中都需要签名验证
2. **请求完整性** - 使用MD5确保请求内容未被篡改
3. **时间验证** - timestamp和yy签名包含时间戳，防止重放攻击
4. **设备绑定** - device_id确保请求来自已注册的设备

---

## 常见问题

### Q: Token过期了怎么办？
A: JWT Token通常有较长的有效期（如几天到几周）。过期后需要重新从浏览器获取新Token。

### Q: 可以同时使用多个账号吗？
A: 可以！系统支持多账号轮换和冷却机制。

### Q: 账号密码登录能否自动获取Token？
A: MiniMax官方API可能不直接提供账号密码登录获取Token的接口，需要通过网页端OAuth流程。目前的实现尝试了多个可能的端点，但如果都失败，需要手动从浏览器获取Token。

### Q: Token缓存机制是什么？
A: 设备信息缓存10800秒（3小时），避免频繁注册设备。

---

## 代码中的关键函数

| 函数 | 功能 | 位置 |
|------|------|------|
| `_parse_jwt_user_id()` | 从JWT Token提取用户ID | minimax_all_in_one.py:500 |
| `parse_token()` | 解析Token格式（支持user_id+token格式） | minimax_all_in_one.py:561 |
| `register_device()` | 注册设备并缓存 | minimax_all_in_one.py:568 |
| `_build_signed_headers()` | 构建带签名的请求头 | minimax_all_in_one.py:541 |
| `_send_message()` | 发送聊天消息 | minimax_all_in_one.py:668 |
| `_poll_response()` | 轮询获取AI响应 | minimax_all_in_one.py:705 |
| `web_agent_chat()` | 完整聊天流程（非流式） | minimax_all_in_one.py:755 |
| `web_agent_chat_stream()` | 完整聊天流程（流式） | minimax_all_in_one.py:783 |
