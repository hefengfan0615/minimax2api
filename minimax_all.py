#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax 一站式：登录 → 拉模型 → 聊天 → 删除会话

用法：
    # 单条消息
    python3 minimax_all.py --phone 19065353709 --password baobao615 \
        --chat "今天有什么科技新闻？"

    # 多轮对话（每轮独立 session + 自动删除）
    python3 minimax_all.py --phone 19065353709 --password baobao615 --multi \
        --chat "你好" --chat "1+1" --chat "再见"

    # 列出模型
    python3 minimax_all.py --phone 19065353709 --password baobao615 --list-models

    # 全功能测试
    python3 minimax_all.py --phone 19065353709 --password baobao615 --test-all
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
from typing import List, Optional, Tuple

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# ═══════════════════════════════════════════════════════════════════════════
# 硬编码：协议常量
# ═══════════════════════════════════════════════════════════════════════════
AGENT_URL   = "https://agent.minimaxi.com"
ACCOUNT_URL = "https://account.minimaxi.com"
STREAM_URL  = "https://agent-stream.minimaxi.com"
CLIENT_ID   = "agent-minimax"
SIG_SALT    = "I*7Cf%WZ#S&%1RlZJ&C2"
DEFAULT_AGENT_ID = "403870624314008"  # M3 默认

# v0.1.10 RSA-1024 公钥（account.minimaxi.com 的 chunk 210）
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
FP = {"device_platform": "web", "biz_id": "3", "app_id": "3001",
      "version_code": "22201", "os_name": "Linux", "browser_name": "Chrome",
      "device_memory": 4, "cpu_core_num": 2,
      "browser_language": "en-US", "browser_platform": "Linux x86_64",
      "screen_width": 800, "screen_height": 600,
      "lang": "zh", "timezone_offset": 0, "sys_language": "zh",
      "client": "web", "region": "cn"}


# ═══════════════════════════════════════════════════════════════════════════
# 通用工具
# ═══════════════════════════════════════════════════════════════════════════
def md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def new_device_id() -> str:
    return str(random.randint(10_000_000, 99_999_999))


def parse_jwt(jwt: str) -> dict:
    p = jwt.split(".")[1] + "=" * ((4 - len(jwt.split(".")[1]) % 4) % 4)
    return json.loads(base64.b64decode(p))


def build_qs(jwt: str = "", user_id: str = "", device_id: Optional[str] = None) -> str:
    v = dict(FP)
    v.update({"unix": str(int(time.time() * 1000)),
              "uuid": str(uuid.uuid4()),
              "device_id": device_id or new_device_id(),
              "user_id": user_id, "token": jwt})
    return "&".join(f"{k}={v[k]}" for k in QS_FIELDS)


def sign_headers(jwt: str, body, path: str, qs: str) -> dict:
    from urllib.parse import quote
    ts = int(time.time())
    bj = json.dumps(body, separators=(",", ":"), ensure_ascii=False) if body else ""
    full = f"{path}?{qs}"
    yy = md5(f"{quote(full, safe='')}_{bj}{md5(str(int(time.time()*1000)))}ooui")
    sig = md5(f"{ts}{SIG_SALT}{bj}") if bj else md5(f"{ts}{SIG_SALT}")
    return {"Content-Type": "application/json",
            "Accept": "application/json, text/plain, */*",
            "token": jwt, "x-signature": sig,
            "x-timestamp": str(ts), "yy": yy}


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
        m = d.get("agent_message") or {}
        if m and m.get("role") == "assistant":
            c, t = m.get("msg_content") or "", m.get("thinking_content") or ""
            if c and len(c) >= len(content): content = c
            if t and len(t) >= len(thinking): thinking = t
            continue
        c2 = d.get("agent_message_chunk") or {}
        if c2 and c2.get("role") == "assistant":
            c, t = c2.get("msg_content") or "", c2.get("thinking_content") or ""
            if c and len(c) > len(content): content = c
            if t and len(t) > len(thinking): thinking = t
    return content, thinking


# ═══════════════════════════════════════════════════════════════════════════
# ① 密码登录 → JWT
# ═══════════════════════════════════════════════════════════════════════════
async def login(client: httpx.AsyncClient, phone: str, password: str
                ) -> Tuple[str, str, str]:
    """返回 (jwt, real_user_id, device_id)"""
    from urllib.parse import quote
    pub = load_pem_public_key(RSA_PUB_PEM.encode())
    auth_token = base64.b64encode(
        pub.encrypt(password.encode(), padding.PKCS1v15())
    ).decode()
    dev = new_device_id()
    qs = build_qs(device_id=dev)
    state = base64.b64encode(json.dumps({
        "redirect_uri": f"{AGENT_URL}/", "csrf": uuid.uuid4().hex
    }).encode()).decode()
    lr = (f"/oauth2/authorize?client_id={CLIENT_ID}"
          f"&redirect_uri={quote(AGENT_URL+'/auth/callback', safe='')}"
          f"&response_type=code&source=agent_web&state={quote(state)}")
    r = await client.post(f"{ACCOUNT_URL}/oauth2/login?{qs}", json={
        "loginType": "20", "phone": phone, "authToken": auth_token,
        "countryCode": "+86", "deviceID": dev, "login_redirect": lr,
    }, headers={"Content-Type": "application/json"})
    r.raise_for_status()
    if r.json().get("code") != 0:
        raise RuntimeError(f"login: {r.json()}")
    print(f"  [login] ✓ {r.json()['data']['username']}")

    r = await client.get(f"{ACCOUNT_URL}{r.json()['data']['login_redirect']}")
    loc = r.headers.get("location", "")
    if not loc.startswith("http"):
        loc = AGENT_URL + loc
    await client.get(loc)
    jwt = next(c.value for c in client.cookies.jar if c.name == "_token")
    jwt_uid = parse_jwt(jwt)["user"]["id"]

    p = "/v1/api/user/info"
    qs = build_qs(jwt, jwt_uid, dev)
    r = await client.get(f"{AGENT_URL}{p}?{qs}",
                         headers=sign_headers(jwt, {}, p, qs))
    r.raise_for_status()
    real_uid = r.json()["data"]["userInfo"].get("realUserID") or jwt_uid
    print(f"  [me] ✓ realUserID={real_uid}")
    return jwt, real_uid, dev


# ═══════════════════════════════════════════════════════════════════════════
# ② 拉取官网模型列表
# ═══════════════════════════════════════════════════════════════════════════
async def fetch_models(client: httpx.AsyncClient, jwt: str, uid: str,
                       dev: str) -> List[dict]:
    p = "/archon/api/v1/config"
    qs = build_qs(jwt, uid, dev)
    r = await client.get(f"{AGENT_URL}{p}?{qs}",
                         headers=sign_headers(jwt, {}, p, qs))
    r.raise_for_status()
    d = r.json()
    if isinstance(d.get("data"), dict) and "models" in d["data"]:
        return d["data"]["models"]
    if "models" in d:
        return d["models"]
    return []


# ═══════════════════════════════════════════════════════════════════════════
# ③ 建会话 / 发消息 / 流式 / 收回答
# ═══════════════════════════════════════════════════════════════════════════
async def new_session(client, jwt, uid, dev, agent_id, model) -> str:
    p = f"/archon/api/v1/agent/{agent_id}/session"
    qs = build_qs(jwt, uid, dev)
    body = {"model": f"minimax/{model}"}
    r = await client.post(f"{AGENT_URL}{p}?{qs}", json=body,
                          headers=sign_headers(jwt, body, p, qs))
    r.raise_for_status()
    return (r.json().get("session_id")
            or r.json().get("data", {}).get("sessionId"))


async def chat_once(client, jwt, uid, dev, agent_id, model, message: str,
                    variant: str = "thinking",
                    stream: bool = False) -> Tuple[str, str, str]:
    """返回 (session_id, content, thinking)"""
    sid = await new_session(client, jwt, uid, dev, agent_id, model)
    p = f"/archon/api/v1/session/{sid}/message"
    qs = build_qs(jwt, uid, dev)
    body = {
        "content": message,
        "model": {"provider_id": "minimax", "model_id": model,
                  "variant": variant},
        "turn_id": str(uuid.uuid4()),
        "enable_team": True, "worktreeMode": False,
    }
    headers = sign_headers(jwt, body, p, qs)
    headers["Accept"] = "text/event-stream"

    if stream:
        print(f"\n[assistant] ", end="", flush=True)
        async with client.stream("POST", f"{STREAM_URL}{p}?{qs}",
                                 json=body, headers=headers, timeout=120) as r:
            r.raise_for_status()
            content, thinking, buf = "", "", ""
            async for line in r.aiter_lines():
                if not line.startswith("data:") or len(line) <= 5 or line[5] not in "{[":
                    continue
                pl = line[5:].lstrip()
                if pl == "[DONE]":
                    break
                try:
                    d = json.loads(pl)
                except Exception:
                    continue
                ch = d.get("agent_message_chunk") or d.get("agent_message") or {}
                if ch.get("role") != "assistant":
                    continue
                c, t = ch.get("msg_content") or "", ch.get("thinking_content") or ""
                if c and c != buf:
                    print(c[len(buf):], end="", flush=True); buf = c
                if c and len(c) > len(content): content = c
                if t and len(t) > len(thinking): thinking = t
            print()
            return sid, content, thinking
    else:
        r = await client.post(f"{STREAM_URL}{p}?{qs}", json=body,
                              headers=headers, timeout=120)
        r.raise_for_status()
        c, t = parse_sse(r.text)
        return sid, c, t


# ═══════════════════════════════════════════════════════════════════════════
# ④ 删除会话（auto-cleanup）
# ═══════════════════════════════════════════════════════════════════════════
async def delete_session(client, jwt, uid, dev, session_id: str) -> bool:
    p = f"/archon/api/v1/session/{session_id}"
    qs = build_qs(jwt, uid, dev)
    r = await client.request("DELETE", f"{AGENT_URL}{p}?{qs}",
                             headers=sign_headers(jwt, {}, p, qs), timeout=15)
    if r.status_code == 200:
        j = r.json()
        return (j.get("success") is True
                or j.get("base_resp", {}).get("status_code") == 0
                or j.get("code") == 0)
    return False


# ═══════════════════════════════════════════════════════════════════════════
# 高层：一站式 ask（登录→（列模型）→ 聊天 → 删）
# ═══════════════════════════════════════════════════════════════════════════
async def ask(phone, password, message, model="MiniMax-M3",
              variant="thinking", stream=False, list_models=False) -> dict:
    """一站式：登录→（列模型）→ 聊天 → 删 session"""
    client = httpx.AsyncClient(follow_redirects=False, timeout=120)
    result = {"ok": False, "session_id": None, "reply": "", "thinking": ""}
    try:
        # ① 登录
        print(f"\n[1/4] 登录")
        jwt, uid, dev = await login(client, phone, password)

        # ② 拉模型
        print(f"\n[2/4] 拉取模型")
        models = await fetch_models(client, jwt, uid, dev)
        print(f"  共 {len(models)} 个：")
        for m in models:
            print(f"    - {m.get('model_id'):30s} ctx={m.get('context_limit','?')}")
        if list_models:
            result["models"] = models
            return result
        if model not in {m["model_id"] for m in models} and models:
            model = models[0]["model_id"]

        # ③ 聊天
        print(f"\n[3/4] 聊天  model={model}  stream={stream}")
        sid, content, thinking = await chat_once(
            client, jwt, uid, dev, DEFAULT_AGENT_ID, model, message,
            variant=variant, stream=stream,
        )
        result["session_id"] = sid
        result["reply"] = content
        result["thinking"] = thinking
        result["ok"] = bool(content)

        if not stream:
            if thinking:
                print(f"\n  [thinking] {thinking[:500]}")
            print(f"\n  [回复] {content}")

        # ④ 删 session
        print(f"\n[4/4] 清理 session {sid}")
        ok = await delete_session(client, jwt, uid, dev, sid)
        print(f"  [cleanup] {'✓ 已删除' if ok else '✗ 删除失败'}")
        result["deleted"] = ok
        return result
    finally:
        await client.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# 全功能测试
# ═══════════════════════════════════════════════════════════════════════════
async def test_all(phone, password):
    print("=" * 70)
    print("MiniMax 全功能测试（一站式：登录→模型→聊天→删除）")
    print("=" * 70)

    # Test A: 非流式
    print(f"\n--- 测试 A: 非流式聊天 ---")
    r = await ask(phone, password, "用一句话介绍你自己")
    assert r["ok"] and r["deleted"], f"A 失败: {r}"

    # Test B: 流式
    print(f"\n--- 测试 B: 流式聊天 ---")
    r = await ask(phone, password, "1+1=?  只回答数字", stream=True)
    assert r["ok"] and r["deleted"], f"B 失败: {r}"

    # Test C: 不同模型（不传 --model，自动用第一个）
    print(f"\n--- 测试 C: 不传模型参数（自动选第一个） ---")
    r = await ask(phone, password, "用一句话介绍你自己", model=None)
    assert r["ok"] and r["deleted"], f"C 失败: {r}"

    print(f"\n{'=' * 70}\n所有测试通过 ✓\n{'=' * 70}")


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phone", required=True)
    p.add_argument("--password", required=True)
    p.add_argument("--chat", action="append", help="消息（可多次）")
    p.add_argument("--model", default=None,
                   help="模型名（不传则自动用第一个可用模型）")
    p.add_argument("--variant", default="thinking")
    p.add_argument("--stream", action="store_true")
    p.add_argument("--list-models", action="store_true")
    p.add_argument("--test-all", action="store_true")
    p.add_argument("--multi", action="store_true",
                   help="多轮对话（每轮单独 session + 单独删除）")
    args = p.parse_args()

    if args.test_all:
        await test_all(args.phone, args.password)
        return

    if not args.chat:
        p.error("需要 --chat 传消息（可用 --test-all 跑全部）")

    if args.multi and len(args.chat) > 1:
        for i, msg in enumerate(args.chat, 1):
            print(f"\n>>> 第 {i}/{len(args.chat)} 轮")
            await ask(args.phone, args.password, msg,
                      model=args.model, variant=args.variant,
                      stream=args.stream,
                      list_models=(i == 1 and args.list_models))
    else:
        await ask(args.phone, args.password, args.chat[-1],
                  model=args.model, variant=args.variant,
                  stream=args.stream, list_models=args.list_models)


if __name__ == "__main__":
    asyncio.run(main())
