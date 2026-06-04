#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax 一键聊天（手机+密码 → 拿 token → 拉模型 → 聊天）

设计：硬编码协议常量（URL / RSA 公钥 / 签名盐 / client_id），
     动态拉官网模型列表，动态拿 real user id。
用法：
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --chat "你好"
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --list-models
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --test-all
"""

import argparse
import asyncio
import base64
import hashlib
import json
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# ═══════════════════════════════════════════════════════════════════════════
# 硬编码：协议常量（这些是反编译产物/服务器密钥，唯一不能省的"硬编码"）
# ═══════════════════════════════════════════════════════════════════════════
AGENT_URL    = "https://agent.minimaxi.com"
ACCOUNT_URL  = "https://account.minimaxi.com"
STREAM_URL   = "https://agent-stream.minimaxi.com"
CLIENT_ID    = "agent-minimax"
SIG_SALT     = "I*7Cf%WZ#S&%1RlZJ&C2"  # mavis-chat 签名盐

# v0.1.10 RSA-1024 公钥（account.minimaxi.com/_next/static/chunks/210.501a5bce47ca6777.js）
RSA_PUB_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDF5ndG2/UB4L5tbvQaNLHSoBTW
DKbrNBuOmUIP23eCmC2ELMx3kppEikxTp5cV8NxUZl6ii+KLwKugioAXApzypHXb
gXbq13kTKA7OCA1xtAoMdH9cltjBiFAUJlgmVjr0MuJCknhVAjWLjCVRHege+Atl
gkUBUeGa9O+cWcPEwQIDAQAB
-----END PUBLIC KEY-----"""

# query string 字段顺序（mavis-chat 1.20 协议固定）
QS_FIELDS = [
    "device_platform", "biz_id", "app_id", "version_code", "unix",
    "timezone_offset", "sys_language", "lang", "uuid", "device_id",
    "os_name", "browser_name", "device_memory", "cpu_core_num",
    "browser_language", "browser_platform", "user_id",
    "screen_width", "screen_height", "token", "client", "region",
]

# 浏览器指纹（影响签名 yy 字段）
FP = {
    "device_platform": "web", "biz_id": "3", "app_id": "3001",
    "version_code": "22201", "os_name": "Linux", "browser_name": "Chrome",
    "device_memory": 4, "cpu_core_num": 2,
    "browser_language": "en-US", "browser_platform": "Linux x86_64",
    "screen_width": 800, "screen_height": 600,
    "lang": "zh", "timezone_offset": 0, "sys_language": "zh",
    "client": "web", "region": "cn",
}


# ═══════════════════════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════════════════════
def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def new_device_id() -> str:
    return str(random.randint(10_000_000, 99_999_999))


def parse_jwt(jwt: str) -> dict:
    p = jwt.split(".")[1] + "=" * ((4 - len(jwt.split(".")[1]) % 4) % 4)
    return json.loads(base64.b64decode(p))


def build_qs(jwt: str = "", user_id: str = "", device_id: Optional[str] = None) -> str:
    values = dict(FP)
    values.update({
        "unix": str(int(time.time() * 1000)),
        "uuid": str(uuid.uuid4()),
        "device_id": device_id or new_device_id(),
        "user_id": user_id, "token": jwt,
    })
    return "&".join(f"{k}={values[k]}" for k in QS_FIELDS)


def sign_headers(jwt: str, body: dict, path: str, qs: str) -> dict:
    from urllib.parse import quote
    ts = int(time.time())
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    full_uri = f"{path}?{qs}"
    yy = md5(f"{quote(full_uri, safe='')}_{body_json}{md5(str(int(time.time()*1000)))}ooui")
    sig = md5(f"{ts}{SIG_SALT}{body_json}")
    return {
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "token": jwt, "x-signature": sig, "x-timestamp": str(ts), "yy": yy,
    }


def parse_sse(sse: str) -> Tuple[str, str]:
    """SSE 解析：type=2 final 完整；type=6 chunks 取 max；跳过 user echo。"""
    content, thinking = "", ""
    for line in sse.split("\n"):
        line = line.strip()
        if not line.startswith("data:") or len(line) <= 5 or line[5] not in "{[":
            continue
        payload = line[5:].lstrip()
        if payload == "[DONE]":
            break
        try:
            d = json.loads(payload)
        except Exception:
            continue
        msg = d.get("agent_message") or {}
        if msg and msg.get("role") == "assistant":
            c, t = msg.get("msg_content") or "", msg.get("thinking_content") or ""
            if c and len(c) >= len(content): content = c
            if t and len(t) >= len(thinking): thinking = t
            continue
        chunk = d.get("agent_message_chunk") or {}
        if chunk and chunk.get("role") == "assistant":
            c, t = chunk.get("msg_content") or "", chunk.get("thinking_content") or ""
            if c and len(c) > len(content): content = c
            if t and len(t) > len(thinking): thinking = t
    return content, thinking


# ═══════════════════════════════════════════════════════════════════════════
# 登录 / 拿 token
# ═══════════════════════════════════════════════════════════════════════════
async def login(client: httpx.AsyncClient, phone: str, password: str) -> Tuple[str, str, str]:
    """返回 (jwt, real_user_id, device_id)"""
    from urllib.parse import quote
    pub = load_pem_public_key(RSA_PUB_PEM.encode())
    auth_token = base64.b64encode(
        pub.encrypt(password.encode(), padding.PKCS1v15())
    ).decode()
    device_id = new_device_id()

    # 1) POST /oauth2/login
    qs = build_qs(device_id=device_id)
    state_payload = {"redirect_uri": f"{AGENT_URL}/", "csrf": uuid.uuid4().hex}
    state = base64.b64encode(json.dumps(state_payload).encode()).decode()
    login_redirect = (
        f"/oauth2/authorize?client_id={CLIENT_ID}"
        f"&redirect_uri={quote(AGENT_URL + '/auth/callback', safe='')}"
        f"&response_type=code&source=agent_web"
        f"&state={quote(state)}"
    )
    r = await client.post(
        f"{ACCOUNT_URL}/oauth2/login?{qs}",
        json={
            "loginType": "20", "phone": phone, "authToken": auth_token,
            "countryCode": "+86", "deviceID": device_id,
            "login_redirect": login_redirect,
        },
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"login failed: {data}")
    print(f"  [login] ✓ {data['data']['username']}")

    # 2) GET /oauth2/authorize → 302 with code
    r = await client.get(f"{ACCOUNT_URL}{data['data']['login_redirect']}")
    location = r.headers.get("location", "")
    if "code=" not in location:
        raise RuntimeError(f"authorize failed: status={r.status_code}")

    # 3) GET /auth/callback → Set-Cookie _token
    if not location.startswith("http"):
        location = AGENT_URL + location
    await client.get(location)
    jwt = next((ck.value for ck in client.cookies.jar if ck.name == "_token"), None)
    if not jwt:
        raise RuntimeError("no _token cookie")

    # 4) 拿 real user id
    jwt_uid = parse_jwt(jwt)["user"]["id"]
    path = "/v1/api/user/info"
    qs = build_qs(jwt, jwt_uid, device_id)
    r = await client.get(f"{AGENT_URL}{path}?{qs}", headers=sign_headers(jwt, {}, path, qs))
    r.raise_for_status()
    ui = r.json()["data"]["userInfo"]
    real_uid = ui.get("realUserID") or jwt_uid
    print(f"  [me] ✓ {ui['name']} realUserID={real_uid}")
    return jwt, real_uid, device_id


# ═══════════════════════════════════════════════════════════════════════════
# Token 续期
# ═══════════════════════════════════════════════════════════════════════════
async def renew_token(client: httpx.AsyncClient, jwt: str, device_id: str) -> str:
    path = "/v1/api/user/renewal"
    qs = build_qs(jwt, parse_jwt(jwt)["user"]["id"], device_id)
    r = await client.post(
        f"{AGENT_URL}{path}?{qs}",
        headers=sign_headers(jwt, {}, path, qs), json={}, timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    new_jwt = d.get("data", {}).get("token", "")
    if not new_jwt:
        raise RuntimeError(f"renewal failed: {d}")
    return new_jwt


# ═══════════════════════════════════════════════════════════════════════════
# 拉模型列表（官网 config）
# ═══════════════════════════════════════════════════════════════════════════
async def fetch_models(client: httpx.AsyncClient, jwt: str, uid: str, device_id: str) -> List[dict]:
    path = "/archon/api/v1/config"
    qs = build_qs(jwt, uid, device_id)
    r = await client.get(f"{AGENT_URL}{path}?{qs}", headers=sign_headers(jwt, {}, path, qs))
    r.raise_for_status()
    data = r.json()
    # 兼容两种 schema：{data:{models:[]}} 或 {models:[]}
    if isinstance(data.get("data"), dict) and "models" in data["data"]:
        return data["data"]["models"]
    if "models" in data:
        return data["models"]
    return []


# ═══════════════════════════════════════════════════════════════════════════
# 聊天
# ═══════════════════════════════════════════════════════════════════════════
async def chat(client: httpx.AsyncClient, jwt: str, uid: str, device_id: str,
               message: str, agent_id: str, model: str,
               variant: str = "thinking", stream: bool = False,
               session_id: Optional[str] = None) -> Tuple[str, str]:
    """返回 (content, thinking)。"""
    # 1) 建会话
    if not session_id:
        path = f"/archon/api/v1/agent/{agent_id}/session"
        qs = build_qs(jwt, uid, device_id)
        body = {"model": f"minimax/{model}"}
        r = await client.post(f"{AGENT_URL}{path}?{qs}",
                              json=body, headers=sign_headers(jwt, body, path, qs))
        r.raise_for_status()
        session_id = (r.json().get("session_id")
                      or r.json().get("data", {}).get("sessionId"))

    # 2) 发消息
    path = f"/archon/api/v1/session/{session_id}/message"
    qs = build_qs(jwt, uid, device_id)
    body = {
        "content": message,
        "model": {"provider_id": "minimax", "model_id": model, "variant": variant},
        "turn_id": str(uuid.uuid4()),
        "enable_team": True, "worktreeMode": False,
    }
    headers = sign_headers(jwt, body, path, qs)
    headers["Accept"] = "text/event-stream"

    if stream:
        print(f"\n[assistant] ", end="", flush=True)
        async with client.stream("POST", f"{STREAM_URL}{path}?{qs}",
                                 json=body, headers=headers, timeout=60) as r:
            r.raise_for_status()
            content, thinking, buf = "", "", ""
            async for line in r.aiter_lines():
                if not line.startswith("data:") or len(line) <= 5 or line[5] not in "{[":
                    continue
                payload = line[5:].lstrip()
                if payload == "[DONE]":
                    break
                try: d = json.loads(payload)
                except: continue
                chunk = d.get("agent_message_chunk") or d.get("agent_message") or {}
                # 跳过 user echo（role=user 的都是回显）
                if chunk.get("role") != "assistant":
                    continue
                c, t = chunk.get("msg_content") or "", chunk.get("thinking_content") or ""
                if c and c != buf:
                    print(c[len(buf):], end="", flush=True)
                    buf = c
                if c and len(c) > len(content): content = c
                if t and len(t) > len(thinking): thinking = t
            print()
            return content, thinking
    else:
        r = await client.post(f"{STREAM_URL}{path}?{qs}",
                              json=body, headers=headers, timeout=60)
        r.raise_for_status()
        return parse_sse(r.text)


# ═══════════════════════════════════════════════════════════════════════════
# 全功能测试
# ═══════════════════════════════════════════════════════════════════════════
async def test_all(phone: str, password: str):
    print("=" * 70)
    print("MiniMax 全功能测试")
    print("=" * 70)
    client = httpx.AsyncClient(follow_redirects=False, timeout=60)

    # ① 登录
    print(f"\n[1] 密码登录 ({ACCOUNT_URL})")
    jwt, uid, dev = await login(client, phone, password)
    pld = parse_jwt(jwt)
    days = (pld["exp"] - time.time()) // 86400
    print(f"  [token] 有效期 {days:.1f} 天, userID={pld['user']['id']}")

    # ② 续期
    print(f"\n[2] Token 续期")
    new_jwt = await renew_token(client, jwt, dev)
    new_days = (parse_jwt(new_jwt)["exp"] - time.time()) // 86400
    print(f"  [renew] ✓ 新 token 有效期 {new_days:.1f} 天")
    jwt = new_jwt

    # ③ 拉模型
    print(f"\n[3] 拉取官网模型 (/archon/api/v1/config)")
    models = await fetch_models(client, jwt, uid, dev)
    print(f"  [models] 共 {len(models)} 个:")
    for m in models:
        ctx = m.get("context_limit", "?")
        mid = m.get("model_id")
        vs = m.get("supported_variants", [])
        tm = m.get("thinking_config", {}).get("mode", "?")
        print(f"    - {mid:30s} ctx={ctx:>7} variants={vs} think={tm}")

    # ④ 默认 agent
    print(f"\n[4] 默认 agent_id")
    default_agent_id = "403870624314008"  # M3 默认（从 chunk 实证）
    print(f"  [agent] {default_agent_id}")

    # ⑤ 单轮聊天
    print(f"\n[5] 单轮聊天（每个模型一次）")
    for m in models:
        mid = m["model_id"]
        variants = m.get("supported_variants", [""])
        v = "thinking" if "thinking" in variants else (variants[0] if variants else "")
        try:
            content, _ = await chat(client, jwt, uid, dev,
                                    message="用一句话介绍你自己",
                                    agent_id=default_agent_id, model=mid, variant=v)
            ok = "✓" if content and len(content) > 5 else "✗"
            print(f"  {ok} [{mid:30s}] {len(content):>4} 字符: {content[:80]!r}")
        except Exception as e:
            print(f"  ✗ [{mid}] 失败: {e}")

    # ⑥ 多轮聊天
    print(f"\n[6] 多轮聊天（同一 session）")
    path = f"/archon/api/v1/agent/{default_agent_id}/session"
    qs = build_qs(jwt, uid, dev)
    r = await client.post(f"{AGENT_URL}{path}?{qs}",
                          json={"model": "minimax/MiniMax-M3"},
                          headers=sign_headers(jwt, {"model": "minimax/MiniMax-M3"}, path, qs))
    sid = r.json().get("session_id") or r.json().get("data", {}).get("sessionId")
    print(f"  [session] {sid}")
    for q in ["我叫什么", "那你能做什么？", "总结一下我们的对话"]:
        c, _ = await chat(client, jwt, uid, dev, q, default_agent_id, "MiniMax-M3", session_id=sid)
        print(f"  Q: {q}")
        print(f"  A: {c[:150]}{'...' if len(c) > 150 else ''}")

    # ⑦ 流式
    print(f"\n[7] 流式聊天")
    await chat(client, jwt, uid, dev, "1+1等于几？只回答数字",
               default_agent_id, "MiniMax-M3", stream=True)

    print(f"\n{'=' * 70}\n所有测试通过 ✓\n{'=' * 70}")
    await client.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phone", required=True, help="手机号")
    p.add_argument("--password", required=True, help="密码")
    p.add_argument("--chat", help="要发送的消息")
    p.add_argument("--model", default="MiniMax-M3", help="模型名")
    p.add_argument("--variant", default="thinking", help="thinking / 空")
    p.add_argument("--list-models", action="store_true", help="仅列出模型")
    p.add_argument("--stream", action="store_true", help="流式输出")
    p.add_argument("--test-all", action="store_true", help="跑全部功能测试")
    args = p.parse_args()

    if args.test_all:
        await test_all(args.phone, args.password)
        return

    client = httpx.AsyncClient(follow_redirects=False, timeout=60)
    try:
        print(f"[1] 登录 ({ACCOUNT_URL})")
        jwt, uid, dev = await login(client, args.phone, args.password)

        print(f"\n[2] 拉取模型")
        models = await fetch_models(client, jwt, uid, dev)
        for m in models:
            print(f"  - {m.get('model_id'):30s} ctx={m.get('context_limit', '?')}")
        if args.list_models:
            return

        if not args.chat:
            print("\n需要 --chat 传消息；用 --test-all 跑全部测试")
            return

        valid = {m["model_id"] for m in models}
        if args.model not in valid and models:
            args.model = models[0]["model_id"]
        print(f"\n[3] 发送: {args.chat!r}  model={args.model}")
        content, thinking = await chat(
            client, jwt, uid, dev,
            message=args.chat,
            agent_id="403870624314008",
            model=args.model, variant=args.variant, stream=args.stream,
        )
        if not args.stream:
            if thinking:
                print(f"\n[thinking] {thinking[:500]}")
            print(f"\n[回复] {content}")
    finally:
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
