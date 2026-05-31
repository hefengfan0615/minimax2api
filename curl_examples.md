# MiniMax自动登录使用指南

## 方式一：使用curl命令直接调用API

### 1. 启动服务
首先启动minimax_all_in_one.py服务：
```bash
python minimax_all_in_one.py
```

### 2. 使用curl自动登录MiniMax
使用测试账号（19065353709 / baobao615）自动登录：

```bash
curl -X POST http://localhost:8000/api/minimax/login \
  -H "Content-Type: application/json" \
  -d '{
    "mobile": "19065353709",
    "password": "baobao615"
  }'
```

### 3. 验证登录结果
查看当前配置的账号：
```bash
curl http://localhost:8000/api/config
```

### 4. 测试账号是否工作
测试刚配置的账号：
```bash
curl -X POST http://localhost:8000/api/test-account/0
```

---

## 方式二：使用Python脚本

```bash
python auto_login.py
```

---

## API接口说明

### POST /api/minimax/login
使用MiniMax账号密码自动登录并配置账号

**请求体：**
```json
{
  "mobile": "手机号",
  "password": "密码"
}
```

**成功响应：**
```json
{
  "success": true,
  "message": "账号登录并配置成功",
  "account_name": "账号名称",
  "user_id": "用户ID",
  "user_info": {
    "id": "用户ID",
    "name": "用户名",
    ...
  }
}
```

---

## 其他常用curl命令

### 获取健康检查
```bash
curl http://localhost:8000/health
```

### 获取可用模型列表
```bash
curl -H "Authorization: Bearer sk-minimax" http://localhost:8000/v1/models
```

### 发送聊天请求
```bash
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Authorization: Bearer sk-minimax" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": false
  }'
```
