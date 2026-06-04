#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax 一键聊天（手机+密码登录 → 拿 token → 选模型 → 聊天）

用法：
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --chat "你好"
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --stream --chat "你好"
"""

import argparse
import asyncio
import base64
import hashlib
import json
import time
import uuid
from urllib.parse import quote
from typing import Tuple

import httpx
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from cryptography.hazmat.primitives.asymmetric import padding

# ════════ 端点 ════════
ACCOUNT = "https://account.minimaxi.com"
AGENT = "https://agent.minimaxi.com"
STREAM = "https://agent-stream.minimaxi.com"

# 登录页加密密码的 RSA-1024 公钥（mmx-account v0.1.9 → 210.2ba1237c3dcf28f0.js）
RSA_PUB_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDF5ndG2/UB4L5tbvQaNLHSoBTW
DKbrNBuOmUIP23eCmC2ELMx3kppEikxTp5cV8NxUZl6ii+KLwKugioAXApzypHXb
gXbq13kTKA7OCA1xtAoMdH9cltjBiFAUJlgmVjr0MuJCknhVAjWLjCVRHege+Atl
gkUBUeGa9O+cWcPEwQIDAQAB
-----END PUBLIC KEY-----"""

LOGIN_REDIRECT = (
    "/oauth2/authorize?client_id=agent-minimax"
    "&redirect_uri=https%3A%2F%2Fagent.minimaxi.com%2Fauth%2Fcallback"
    "&response_type=code&source=agent_web"
    "&state=eyJyZWRpcmVjdF91cmkiOiJodHRwczovL2FnZW50Lm1pbmltYXhpLmNvbS8iLCJjc3JmIjoiMWE3Nzk0MjctMmQ1NS00YWUzLTg4ODktYWVjOGNhMzgzMTVmIn0%3D"
)

# query string 字段顺序（必须严格，签名 yy 依赖）
QS_FIELDS = [
    "device_platform", "biz_id", "app_id", "version_code", "unix",
    "timezone_offset", "sys_language", "lang", "uuid", "device_id",
    "os_name", "browser_name", "device_memory", "cpu_core_num",
    "browser_language", "browser_platform", "user_id",
    "screen_width", "screen_height", "token", "client", "region",
]


# ════════ 工具函数 ════════
def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def build_qs(jwt: str = "", user_id: str = "") -> str:
    values = {
        "device_platform": "web", "biz_id": "3", "app_id": "3001",
        "version_code": "22201", "unix": str(int(time.time() * 1000)),
        "timezone_offset": 0, "sys_language": "zh", "lang": "zh",
        "uuid": str(uuid.uuid4()), "device_id": "31711366",
        "os_name": "Linux", "browser_name": "Chrome",
        "device_memory": 4, "cpu_core_num": 2,
        "browser_language": "en-US", "browser_platform": "Linux x86_64",
        "user_id": user_id, "screen_width": 800, "screen_height": 600,
        "token": jwt, "client": "web", "region": "cn",
    }
    return "&".join(f"{k}={values[k]}" for k in QS_FIELDS)


def sign_headers(jwt: str, body: dict, path: str, qs: str) -> dict:
    """yy + x-signature 签名（mavis-chat 1.20 逆向）"""
    ts = int(time.time())
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    full_uri = f"{path}?{qs}"
    yy = md5(f"{quote(full_uri, safe='')}_{body_json}{md5(str(int(time.time()*1000)))}ooui")
    sig = md5(f"{ts}I*7Cf%WZ#S&%1RlZJ&C2{body_json}")
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "token": jwt,
        "x-signature": sig,
        "x-timestamp": str(ts),
        "yy": yy,
    }


def parse_jwt(jwt: str) -> dict:
    parts = jwt.split(".")
    b64 = parts[1] + "=" * ((4 - len(parts[1]) % 4) % 4)
    return json.loads(base64.b64decode(b64))


def parse_sse(sse: str) -> Tuple[str, str]:
    """解析 SSE 响应，返回 (msg_content, thinking_content)

    关键：type=2 final 才是完整内容，type=6 chunks 不累积（要拿 max）。
    要跳过 user echo（role=user 的 type=2）。
    """
    content, thinking = "", ""
    for line in sse.split("\n"):
        line = line.strip()
        # 服务端格式：data:{...}（无空格）
        if not line.startswith("data:") or len(line) <= 5 or line[5] not in "{[":
            continue
        payload = line[5:].lstrip()
        if payload == "[DONE]":
            break
        try:
            d = json.loads(payload)
        except Exception:
            continue
        # type=2 完整消息（assistant 才是答案，跳过 user echo）
        msg = d.get("agent_message") or {}
        if msg and msg.get("role") == "assistant":
            c = msg.get("msg_content") or ""
            t = msg.get("thinking_content") or ""
            # final 的 type=2 是最完整的；如果之前 chunks 拿到部分内容，这里覆盖
            if c and len(c) >= len(content):
                content = c
            if t and len(t) >= len(thinking):
                thinking = t
            continue
        # type=6 chunks：取 max（chunks 顺序累积，所以 max = 最新 = 最完整）
        chunk = d.get("agent_message_chunk") or {}
        if chunk and chunk.get("role") == "assistant":
            c = chunk.get("msg_content") or ""
            t = chunk.get("thinking_content") or ""
            if c and len(c) > len(content):
                content = c
            if t and len(t) > len(thinking):
                thinking = t
    return content, thinking


# ════════ 核心流程 ════════
async def login_and_get_token(phone: str, password: str) -> Tuple[str, str, str]:
    """密码登录，返回 (jwt, real_user_id, device_uuid)"""
    pub = load_pem_public_key(RSA_PUB_PEM.encode())
    auth_token = base64.b64encode(pub.encrypt(password.encode(), padding.PKCS1v15())).decode()
    device_uuid = str(uuid.uuid4())

    async with httpx.AsyncClient(follow_redirects=False, timeout=30) as c:
        # 1) POST /oauth2/login
        qs = build_qs()
        r = await c.post(f"{ACCOUNT}/oauth2/login?{qs}", json={
            "loginType": "20", "phone": phone, "authToken": auth_token,
            "countryCode": "+86", "deviceID": "31711366",
            "login_redirect": LOGIN_REDIRECT,
        }, headers={"Content-Type": "application/json"})
        r.raise_for_status()
        data = r.json()
        if data.get("code") != 0:
            raise RuntimeError(f"login failed: {data}")
        print(f"  [1] ✓ 登录成功: {data['data']['username']}")

        # 2) GET /oauth2/authorize → 302 with code
        r = await c.get(f"{ACCOUNT}{data['data']['login_redirect']}")
        location = r.headers.get("location", "")
        if "code=" not in location:
            raise RuntimeError(f"authorize failed: status={r.status_code}")

        # 3) GET /auth/callback → Set-Cookie _token
        if not location.startswith("http"):
            location = AGENT + location
        await c.get(location)
        jwt = None
        for ck in c.cookies.jar:
            if ck.name == "_token":
                jwt = ck.value
                break
        if not jwt:
            raise RuntimeError("no _token cookie")

    # 4) 拿 real user id
    payload = parse_jwt(jwt)
    jwt_user_id = payload["user"]["id"]
    real_uid = jwt_user_id
    async with httpx.AsyncClient(timeout=15) as c:
        qs = build_qs(jwt, jwt_user_id)
        path = "/v1/api/user/info"
        r = await c.get(f"{AGENT}{path}?{qs}", headers=sign_headers(jwt, {}, path, qs))
        r.raise_for_status()
        ui = r.json()["data"]["userInfo"]
        real_uid = ui.get("realUserID") or jwt_user_id
        print(f"  [2] ✓ 用户: {ui['name']} realUserID={real_uid} 余额={ui.get('vipInfo')}")

    return jwt, real_uid, device_uuid


async def list_official_models(jwt: str, user_id: str) -> list:
    """从官方 /archon/api/v1/agent 拉取模型列表"""
    async with httpx.AsyncClient(timeout=15) as c:
        for path in ("/archon/api/v1/agent", "/archon/api/v1/config"):
            try:
                qs = build_qs(jwt, user_id)
                r = await c.get(f"{AGENT}{path}?{qs}", headers=sign_headers(jwt, {}, path, qs))
                r.raise_for_status()
                bots = r.json().get("data", {}).get("agents") or []
                if bots:
                    return bots
            except Exception:
                continue
    return []


async def chat(jwt: str, user_id: str, device_uuid: str,
               message: str, agent_id: str, model: str,
               variant: str = "thinking", stream: bool = False) -> str:
    """建会话 + 发消息 + 解析响应"""
    async with httpx.AsyncClient(timeout=60) as c:
        # 1) 建会话
        path = f"/archon/api/v1/agent/{agent_id}/session"
        qs = build_qs(jwt, user_id)
        body = {"model": f"minimax/{model}"}
        r = await c.post(f"{AGENT}{path}?{qs}",
                         json=body, headers=sign_headers(jwt, body, path, qs))
        r.raise_for_status()
        sid = r.json().get("session_id") or r.json().get("data", {}).get("sessionId")

        # 2) 发消息
        path = f"/archon/api/v1/session/{sid}/message"
        qs = build_qs(jwt, user_id)
        body = {
            "content": message,
            "model": {"provider_id": "minimax", "model_id": model, "variant": variant},
            "turn_id": str(uuid.uuid4()),
            "enable_team": True,
            "worktreeMode": False,
        }
        headers = sign_headers(jwt, body, path, qs)
        headers["Accept"] = "text/event-stream"

        if stream:
            print(f"\n[assistant] ", end="", flush=True)
            async with c.stream("POST", f"{STREAM}{path}?{qs}",
                                json=body, headers=headers, timeout=60) as r:
                r.raise_for_status()
                buffer = ""
                async for line in r.aiter_lines():
                    if not line.startswith("data:") or len(line) <= 5 or line[5] not in "{[":
                        continue
                    payload = line[5:].lstrip()
                    if payload == "[DONE]":
                        break
                    try:
                        d = json.loads(payload)
                    except Exception:
                        continue
                    chunk = d.get("agent_message_chunk") or d.get("agent_message") or {}
                    c_text = chunk.get("msg_content") or ""
                    if c_text and c_text != buffer:
                        print(c_text[len(buffer):], end="", flush=True)
                        buffer = c_text
            print()
            return buffer
        else:
            r = await c.post(f"{STREAM}{path}?{qs}", json=body, headers=headers, timeout=60)
            r.raise_for_status()
            content, thinking = parse_sse(r.text)
            if thinking:
                print(f"\n[thinking] {thinking[:500]}")
            return content


# ════════ CLI ════════
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phone", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--chat", required=True, help="要发送的消息")
    p.add_argument("--stream", action="store_true", help="流式输出")
    args = p.parse_args()

    print(f"[1] 登录: phone={args.phone}")
    jwt, uid, dev = await login_and_get_token(args.phone, args.password)

    print(f"\n[2] 拉取官网模型...")
    bots = await list_official_models(jwt, uid)
    if bots:
        for b in bots[:5]:
            print(f"  - {b.get('id')} {b.get('name')}")
        agent_id, model = bots[0]["id"], bots[0].get("name", "MiniMax-M3")
    else:
        # 官网接口空时使用内置默认
        agent_id, model = "403870624314008", "MiniMax-M3"
        print(f"  (空，使用内置默认)")

    print(f"\n[3] 发送: {args.chat!r}")
    reply = await chat(jwt, uid, dev, args.chat, agent_id, model, stream=args.stream)
    if not args.stream:
        print(f"\n[回复] {reply}")


if __name__ == "__main__":
    asyncio.run(main())
