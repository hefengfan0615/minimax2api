#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""all_in_one.py — MiniMax 端到端聊天脚本（密码登录 + 拿 token + 聊天）

═══════════════════════════════════════════════════════════════════════════════
【一句话用法】
    python3 all_in_one.py --phone 19065353709 --password baobao615 --chat "你好"

【支持的所有用法】
    1) 密码登录 + 拿 token + 聊天（一键）：
       python3 all_in_one.py --phone 19065353709 --password baobao615 --chat "你好"

    2) 已有 token，直接聊天：
       python3 all_in_one.py --token "<JWT>" --chat "你好"
       python3 all_in_one.py --token-file /tmp/jwt.txt --chat "你好"

    3) 从浏览器 state.json 加载（推荐长期使用）：
       agent-browser --state ./state.json open https://agent.minimaxi.com
       # ...手动登录一次...
       agent-browser state save ./state.json
       python3 all_in_one.py --state ./state.json --chat "你好"

    4) 列模型：
       python3 all_in_one.py --phone 19065353709 --password baobao615 --list-models

    5) 启动 OpenAI 兼容 HTTP 服务（端口 8910）：
       python3 all_in_one.py --phone 19065353709 --password baobao615 --serve

    6) 流式输出：
       python3 all_in_one.py --phone 19065353709 --password baobao615 --stream --chat "你好"

═══════════════════════════════════════════════════════════════════════════════
【真实抓取的端点（2026-06-03，mmx-account v0.1.9 / mavis-chat v0.1.20）】

  密码登录：   POST  https://account.minimaxi.com/oauth2/login    loginType=20
  授权回调：   GET   https://account.minimaxi.com/oauth2/authorize
  Token回调：  GET   https://agent.minimaxi.com/auth/callback?code=ory_ac_...
  用户信息：   GET   https://agent.minimaxi.com/v1/api/user/info
  列 agent：   GET   https://agent.minimaxi.com/archon/api/v1/agent
  建会话：     POST  https://agent.minimaxi.com/archon/api/v1/agent/{aid}/session
  发消息：     POST  https://agent-stream.minimaxi.com/archon/api/v1/session/{sid}/message   (SSE)

  密码加密：  RSA-1024 + PKCS#1 v1.5
              公钥见 SM2_PUB_KEY_PEM
  默认 agent: 403870624314008  (MiniMax-M3)
  默认模型:   MiniMax-M3       (variant=thinking)

═══════════════════════════════════════════════════════════════════════════════
【签名算法】（mavis-chat 1.20 抓包逆向）

  query 字段顺序（必须严格）:
    device_platform, biz_id, app_id, version_code, unix, timezone_offset,
    sys_language, lang, uuid, device_id, os_name, browser_name,
    device_memory, cpu_core_num, browser_language, browser_platform,
    user_id, screen_width, screen_height, token, client, region

  yy          = md5(quote(uri + "?" + qs) + "_" + body_json + md5(unix_ms) + "ooui")
  x-signature = md5(timestamp + "I*7Cf%WZ#S&%1RlZJ&C2" + body_json)
  x-timestamp = 秒级 unix 时间
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import os
import sys
import time
import uuid as uuid_mod
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

import httpx

# ════════════════════════════════════════════════════════════════════════════
# 常量
# ════════════════════════════════════════════════════════════════════════════

ACCOUNT_BASE = "https://account.minimaxi.com"
AGENT_BASE = "https://agent.minimaxi.com"
STREAM_BASE = "https://agent-stream.minimaxi.com"

DEFAULT_AGENT_ID = "403870624314008"  # MiniMax-M3 默认 agent
DEFAULT_MODEL = "MiniMax-M3"

# mmx-account 登录页用来加密密码的 RSA-1024 公钥（X.509 SPKI 格式）
# 抓自 cdn.hailuoai.com/mmx-account/prod-web-sh-0.1.9 的 210.2ba1237c3dcf28f0.js
SM2_PUB_KEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDF5ndG2/UB4L5tbvQaNLHSoBTW
DKbrNBuOmUIP23eCmC2ELMx3kppEikxTp5cV8NxUZl6ii+KLwKugioAXApzypHXb
gXbq13kTKA7OCA1xtAoMdH9cltjBiFAUJlgmVjr0MuJCknhVAjWLjCVRHege+Atl
gkUBUeGa9O+cWcPEwQIDAQAB
-----END PUBLIC KEY-----"""

# query string 字段顺序（必须严格，签名 yy 依赖此顺序）
QS_FIELDS = [
    "device_platform", "biz_id", "app_id", "version_code", "unix",
    "timezone_offset", "sys_language", "lang", "uuid", "device_id",
    "os_name", "browser_name", "device_memory", "cpu_core_num",
    "browser_language", "browser_platform", "user_id",
    "screen_width", "screen_height", "token", "client", "region",
]

# 浏览器指纹（headless chrome 默认值，避免被风控）
BROWSER_FP = {
    "device_platform": "web",
    "biz_id": "3",
    "app_id": "3001",
    "version_code": "22201",
    "os_name": "Linux",
    "browser_name": "Chrome",
    "device_memory": 4,
    "cpu_core_num": 2,
    "browser_language": "en-US",
    "browser_platform": "Linux x86_64",
    "screen_width": 800,
    "screen_height": 600,
    "lang": "zh",
    "timezone_offset": 0,
    "sys_language": "zh",
    "client": "web",
    "region": "cn",
}

# 密码登录 redirect_uri 参数（OAuth2 必带）
LOGIN_REDIRECT = (
    "/oauth2/authorize?client_id=agent-minimax"
    "&redirect_uri=https%3A%2F%2Fagent.minimaxi.com%2Fauth%2Fcallback"
    "&response_type=code&source=agent_web"
    "&state=eyJyZWRpcmVjdF91cmkiOiJodHRwczovL2FnZW50Lm1pbmltYXhpLmNvbS8iLCJjc3JmIjoiMWE3Nzk0MjctMmQ1NS00YWUzLTg4ODktYWVjOGNhMzgzMTVmIn0%3D"
)

# ════════════════════════════════════════════════════════════════════════════
# 工具函数
# ════════════════════════════════════════════════════════════════════════════

def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def unix() -> int:
    return int(time.time())


def unix_ms() -> str:
    return str(int(time.time() * 1000))


def new_uuid() -> str:
    return str(uuid_mod.uuid4())


def parse_token_obj(token_raw: str) -> Tuple[str, str]:
    """_token 可能是裸 JWT，也可能是 JSON {"token":..., "uuid":...}。返回 (jwt, uuid)。"""
    s = token_raw.strip().strip('"')
    if s.startswith("{"):
        try:
            d = json.loads(s)
            return d.get("token", ""), d.get("uuid", "")
        except Exception:
            pass
    return s, ""


def parse_jwt_payload(jwt: str) -> dict:
    try:
        parts = jwt.split(".")
        if len(parts) != 3:
            return {}
        b64 = parts[1]
        b64 += "=" * ((4 - len(b64) % 4) % 4)
        return json.loads(base64.b64decode(b64))
    except Exception:
        return {}


def extract_user_id(jwt: str) -> str:
    p = parse_jwt_payload(jwt)
    return p.get("user", {}).get("id", "")


# ════════════════════════════════════════════════════════════════════════════
# RSA 加密密码（authToken 字段）
# ════════════════════════════════════════════════════════════════════════════

def encrypt_password(password: str) -> str:
    """用 RSA-1024 公钥 + PKCS#1 v1.5 加密密码，返回 base64 字符串。

    注：每次加密结果不同（随机 padding），但服务端用私钥解密都能得到原密码。
    """
    try:
        from cryptography.hazmat.primitives.serialization import load_pem_public_key
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
    except ImportError:
        raise RuntimeError("缺少 cryptography：pip install cryptography")

    pub = load_pem_public_key(SM2_PUB_KEY_PEM.encode("utf-8"))
    ct = pub.encrypt(password.encode("utf-8"), asym_padding.PKCS1v15())
    return base64.b64encode(ct).decode("ascii")


# ════════════════════════════════════════════════════════════════════════════
# Query String + 签名
# ════════════════════════════════════════════════════════════════════════════

def build_qs(
    jwt: str = "", user_id: str = "",
    device_uuid: Optional[str] = None, device_id: str = "31711366",
) -> str:
    """严格按顺序构造 query string。

    登录时 jwt/user_id 为空字符串。
    """
    values = {
        "device_platform": BROWSER_FP["device_platform"],
        "biz_id": BROWSER_FP["biz_id"],
        "app_id": BROWSER_FP["app_id"],
        "version_code": BROWSER_FP["version_code"],
        "unix": unix_ms(),
        "timezone_offset": BROWSER_FP["timezone_offset"],
        "sys_language": BROWSER_FP["sys_language"],
        "lang": BROWSER_FP["lang"],
        "uuid": device_uuid or new_uuid(),
        "device_id": device_id,
        "os_name": BROWSER_FP["os_name"],
        "browser_name": BROWSER_FP["browser_name"],
        "device_memory": BROWSER_FP["device_memory"],
        "cpu_core_num": BROWSER_FP["cpu_core_num"],
        "browser_language": BROWSER_FP["browser_language"],
        "browser_platform": BROWSER_FP["browser_platform"],
        "user_id": user_id,
        "screen_width": BROWSER_FP["screen_width"],
        "screen_height": BROWSER_FP["screen_height"],
        "token": jwt,
        "client": BROWSER_FP["client"],
        "region": BROWSER_FP["region"],
    }
    return "&".join(f"{k}={values[k]}" for k in QS_FIELDS)


def sign_request(jwt: str, body: dict, path: str, qs: str) -> Dict[str, str]:
    """计算 yy / x-signature 头（mavis-chat 1.20 抓包逆向）。

    x-signature = md5(timestamp + "I*7Cf%WZ#S&%1RlZJ&C2" + body_json)
    yy        = md5(quote(uri + "?" + qs) + "_" + body_json + md5(unix_ms) + "ooui")
    """
    from urllib.parse import quote
    ts = unix()
    ts_ms = unix_ms()
    body_json = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    full_uri = f"{path}?{qs}"
    yy = md5(
        f"{quote(full_uri, safe='')}_{body_json}{md5(ts_ms)}ooui"
    )
    XSALT = "I*7Cf%WZ#S&%1RlZJ&C2"
    sig = md5(f"{ts}{XSALT}{body_json}")
    return {
        "yy": yy,
        "x-signature": sig,
        "x-timestamp": str(ts),
        "body_json": body_json,
    }


def build_headers(jwt: str, body: dict, path: str, qs: str) -> Dict[str, str]:
    sigs = sign_request(jwt, body, path, qs)
    return {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Content-Type": "application/json",
        "Origin": AGENT_BASE,
        "Referer": f"{AGENT_BASE}/",
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) HeadlessChrome/149.0.0.0 Safari/537.36"
        ),
        "token": jwt,
        "x-signature": sigs["x-signature"],
        "x-timestamp": sigs["x-timestamp"],
        "yy": sigs["yy"],
    }


# ════════════════════════════════════════════════════════════════════════════
# Session 数据类
# ════════════════════════════════════════════════════════════════════════════

@dataclass
class Session:
    jwt: str
    user_id: str
    device_uuid: str = field(default_factory=new_uuid)
    device_id: str = "31711366"
    user_name: str = ""
    expires_at: int = 0

    @property
    def is_expired(self) -> bool:
        return bool(self.expires_at) and unix() >= self.expires_at - 86400


# ════════════════════════════════════════════════════════════════════════════
# 密码登录（端到端）
# ════════════════════════════════════════════════════════════════════════════

async def password_login(
    client: httpx.AsyncClient, phone: str, password: str,
) -> Session:
    """密码登录完整流程：

      1. RSA-1024 + PKCS#1 v1.5 加密密码 → base64
      2. POST https://account.minimaxi.com/oauth2/login   loginType=20
      3. GET  /oauth2/authorize?client_id=agent-minimax...  → 302 to /auth/callback?code=...
      4. GET  https://agent.minimaxi.com/auth/callback?code=...  → 302 + Set-Cookie: _token=...

    返回 Session（含 JWT、user_id、device_uuid 等）。
    """
    device_uuid = new_uuid()
    device_id = "31711366"

    # ---- 1) 加密密码 ----
    print(f"  [1.1] RSA-1024 加密密码...")
    auth_token = encrypt_password(password)

    # ---- 2) POST /oauth2/login ----
    qs = build_qs("", "", device_uuid, device_id)
    url = f"{ACCOUNT_BASE}/oauth2/login?{qs}"
    body = {
        "loginType": "20",
        "phone": phone,
        "authToken": auth_token,
        "countryCode": "+86",
        "deviceID": device_id,
        "login_redirect": LOGIN_REDIRECT,
    }
    print(f"  [1.2] POST /oauth2/login ...")
    r = await client.post(url, json=body, headers={"Content-Type": "application/json"}, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(f"login http {r.status_code}: {r.text[:500]}")
    data = r.json()
    code = data.get("code")
    if code != 0:
        raise RuntimeError(f"login failed: code={code} msg={data.get('msg')}")
    username = data["data"]["username"]
    print(f"  [1.3] ✓ 登录成功: username={username}")

    # ---- 3) GET /oauth2/authorize → 302 with code ----
    auth_redirect = data["data"]["login_redirect"]
    print(f"  [2.1] GET /oauth2/authorize ...")
    r2 = await client.get(
        f"{ACCOUNT_BASE}{auth_redirect}",
        follow_redirects=False, timeout=15,
    )
    location = r2.headers.get("location", "")
    if "code=" not in location:
        raise RuntimeError(f"authorize no code: status={r2.status_code} body={r2.text[:300]}")
    code = location.split("code=")[1].split("&")[0]
    print(f"  [2.2] ✓ 拿到 code: {code[:30]}...")

    # ---- 4) GET /auth/callback → 302 + Set-Cookie _token ----
    print(f"  [3.1] GET /auth/callback ...")
    if not location.startswith("http"):
        location = AGENT_BASE + location
    r3 = await client.get(location, follow_redirects=False, timeout=15)

    # 从 cookies 拿 _token
    jwt = ""
    for c in client.cookies.jar:
        if c.name == "_token" and "minimax" in (c.domain or ""):
            jwt = c.value
            break
    if not jwt:
        # 尝试从 response body / Location query string 拿
        # 兜底：查找 token= 参数
        for c in client.cookies.jar:
            if c.name == "_token":
                jwt = c.value
                break
    if not jwt:
        raise RuntimeError(f"callback no _token cookie: cookies={[(c.name,c.domain) for c in client.cookies.jar]}")

    payload = parse_jwt_payload(jwt)
    user_id = payload.get("user", {}).get("id", "")
    user_name = payload.get("user", {}).get("name", "")
    expires_at = payload.get("exp", 0)
    print(f"  [3.2] ✓ 拿到 JWT: userID={user_id} expires_in={(expires_at-unix())//86400} 天")

    return Session(
        jwt=jwt, user_id=user_id, device_uuid=device_uuid,
        device_id=device_id, user_name=user_name, expires_at=expires_at,
    )


# ════════════════════════════════════════════════════════════════════════════
# Token 管理
# ════════════════════════════════════════════════════════════════════════════

def load_session_from_state(state_path: str) -> Session:
    """从 agent-browser 的 state.json 加载 session。"""
    with open(state_path) as f:
        data = json.load(f)
    cookies = {c["name"]: c["value"] for c in data.get("cookies", [])}
    origins = data.get("origins", [])
    ls = {}
    for o in origins:
        if "agent.minimaxi.com" in o.get("origin", "") or "minimaxi" in o.get("origin", ""):
            for item in o.get("localStorage", []):
                ls[item["name"]] = item["value"]

    jwt = ls.get("_token") or cookies.get("_token")
    if not jwt:
        raise ValueError("state.json 里没找到 _token（请先在浏览器里登录）")
    jwt, _saved_uuid = parse_token_obj(jwt)
    payload = parse_jwt_payload(jwt)
    user_id = payload.get("user", {}).get("id", "")
    user_name = payload.get("user", {}).get("name", "")
    expires_at = payload.get("exp", 0)
    device_uuid = ls.get("UNIQUE_USER_ID", new_uuid()) or _saved_uuid or new_uuid()
    return Session(
        jwt=jwt, user_id=user_id, device_uuid=device_uuid,
        user_name=user_name, expires_at=expires_at,
    )


async def renew_token(client: httpx.AsyncClient, old_jwt: str) -> str:
    """POST /v1/api/user/renewal 无 body，返回新 JWT（30 天 rolling）。"""
    path = "/v1/api/user/renewal"
    qs = build_qs(old_jwt, extract_user_id(old_jwt), new_uuid())
    url = f"{AGENT_BASE}{path}?{qs}"
    body: dict = {}
    headers = build_headers(old_jwt, body, path, qs)
    r = await client.post(url, headers=headers, json=body, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("statusInfo", {}).get("code") != 0:
        raise RuntimeError(f"renewal failed: {data}")
    new_jwt = data.get("data", {}).get("token", "")
    if not new_jwt:
        raise RuntimeError(f"no token in renewal: {data}")
    return new_jwt


async def fetch_user_info(client: httpx.AsyncClient, sess: Session) -> dict:
    path = "/v1/api/user/info"
    qs = build_qs(sess.jwt, sess.user_id, sess.device_uuid, sess.device_id)
    url = f"{AGENT_BASE}{path}?{qs}"
    body: dict = {}
    headers = build_headers(sess.jwt, body, path, qs)
    r = await client.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    return r.json()


async def fetch_real_user_id(client: httpx.AsyncClient, sess: Session) -> str:
    """从 /v1/api/user/info 拿真实 user_id（realUserID），用于 query string。"""
    try:
        data = await fetch_user_info(client, sess)
        ui = data.get("data", {}).get("userInfo", {})
        return (
            ui.get("realUserID")
            or ui.get("userID")
            or sess.user_id
        )
    except Exception:
        return sess.user_id


# ════════════════════════════════════════════════════════════════════════════
# 聊天：列模型 / 创建 session / 发消息 (非流 + SSE)
# ════════════════════════════════════════════════════════════════════════════

async def list_models(client: httpx.AsyncClient, sess: Session) -> List[dict]:
    """列可用 agent / 模型。"""
    out: List[dict] = []
    for path in ("/archon/api/v1/agent", "/archon/api/v1/config"):
        qs = build_qs(sess.jwt, sess.user_id, sess.device_uuid, sess.device_id)
        url = f"{AGENT_BASE}{path}?{qs}"
        body: dict = {}
        headers = build_headers(sess.jwt, body, path, qs)
        try:
            r = await client.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            d = r.json()
            agents = (
                d.get("data", {}).get("agents")
                or d.get("data", {}).get("configs")
                or d.get("data", {}).get("bots")
                or []
            )
            if agents:
                out = agents
                break
        except Exception:
            continue
    return out


async def create_session(
    client: httpx.AsyncClient, sess: Session,
    agent_id: str = DEFAULT_AGENT_ID, model: str = DEFAULT_MODEL,
) -> str:
    """POST /archon/api/v1/agent/{aid}/session 创建会话，返回 session_id。"""
    path = f"/archon/api/v1/agent/{agent_id}/session"
    qs = build_qs(sess.jwt, sess.user_id, sess.device_uuid, sess.device_id)
    url = f"{AGENT_BASE}{path}?{qs}"
    body = {"model": f"minimax/{model}"}
    headers = build_headers(sess.jwt, body, path, qs)
    r = await client.post(url, json=body, headers=headers, timeout=15)
    if r.status_code >= 400:
        raise RuntimeError(
            f"create_session {r.status_code}: {r.text[:1000]}"
        )
    data = r.json()
    sid = (
        data.get("session_id") or data.get("sessionId") or data.get("id")
        or data.get("data", {}).get("sessionId")
        or data.get("data", {}).get("session_id")
    )
    if not sid:
        raise RuntimeError(f"create_session failed: {data}")
    return sid


def _extract_sse_text(sse_text: str) -> Tuple[str, str]:
    """从 SSE 文本提取最终 assistant 内容 + 思考内容。

    注：SSE 格式为 data:{...}（无空格）。
    """
    final_content = ""
    final_thinking = ""
    for line in sse_text.split("\n"):
        line = line.strip()
        # 服务端 SSE 格式：data:{...}（无空格）— data: 后面直接是 { 或 [
        if not (line.startswith("data:") and len(line) > 5 and line[5] in "{["):
            continue
        payload = line[5:].lstrip()  # data: 后面紧跟 { 之类的，去掉可能的前导空格
        if payload == "[DONE]":
            break
        try:
            d = json.loads(payload)
        except Exception:
            continue
        # type=6: 流式 chunk（最后一个 chunk 包含完整文本）
        chunk = d.get("agent_message_chunk") or {}
        if chunk:
            c = chunk.get("msg_content")
            t = chunk.get("thinking_content")
            if c:
                final_content = c
            if t:
                final_thinking = t
            continue
        # type=2: 完整消息（兜底）
        msg = d.get("agent_message") or {}
        if msg:
            c = msg.get("msg_content")
            t = msg.get("thinking_content")
            if c and not final_content:
                final_content = c
            if t and not final_thinking:
                final_thinking = t
    return final_content, final_thinking


async def chat(
    client: httpx.AsyncClient, sess: Session,
    message: str,
    agent_id: str = DEFAULT_AGENT_ID,
    model: str = DEFAULT_MODEL,
    variant: str = "thinking",
    stream: bool = False,
) -> str:
    """单轮聊天：建 session + 发消息 + 解析响应。"""
    sid = await create_session(client, sess, agent_id, model)
    path = f"/archon/api/v1/session/{sid}/message"
    qs = build_qs(sess.jwt, sess.user_id, sess.device_uuid, sess.device_id)
    url = f"{STREAM_BASE}{path}?{qs}"
    body = {
        "content": message,
        "model": {
            "provider_id": "minimax",
            "model_id": model,
            "variant": variant,
        },
        "turn_id": new_uuid(),
        "enable_team": True,
        "worktreeMode": False,
    }
    headers = build_headers(sess.jwt, body, path, qs)
    headers["Accept"] = "text/event-stream"

    if stream:
        sys.stdout.write(f"\n[assistant] ")
        sys.stdout.flush()
        async with client.stream("POST", url, json=body, headers=headers, timeout=60) as r:
            r.raise_for_status()
            buffer = ""
            async for line in r.aiter_lines():
                # SSE 格式：data:{...}（无空格）— data: 后面直接是 { 或 [
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
                c = chunk.get("msg_content") or ""
                if c and c != buffer:
                    sys.stdout.write(c[len(buffer):])
                    sys.stdout.flush()
                    buffer = c
            sys.stdout.write("\n")
            sys.stdout.flush()
            # 等一拍确保管道/终端有足够时间读取
            import asyncio as _aio
            await _aio.sleep(0.05)
            return buffer
    else:
        r = await client.post(url, json=body, headers=headers, timeout=60)
        if r.status_code >= 400:
            raise RuntimeError(f"chat http {r.status_code}: {r.text[:1000]}")
        content, thinking = _extract_sse_text(r.text)
        if thinking:
            print(f"\n[thinking] {thinking[:500]}")
        return content


# ════════════════════════════════════════════════════════════════════════════
# OpenAI 兼容 HTTP 服务
# ════════════════════════════════════════════════════════════════════════════

def make_openai_app(sess: Session):
    """构造一个 OpenAI 兼容的 FastAPI app（/v1/models, /v1/chat/completions）。"""
    try:
        from fastapi import FastAPI, Request
        from fastapi.responses import StreamingResponse
    except ImportError:
        raise RuntimeError("pip install fastapi uvicorn")

    app = FastAPI(title="MiniMax OpenAI Bridge")
    holder: Dict[str, Any] = {"c": None}

    @app.on_event("startup")
    async def _startup():
        holder["c"] = httpx.AsyncClient(timeout=60.0)

    @app.on_event("shutdown")
    async def _shutdown():
        if holder["c"]:
            await holder["c"].aclose()

    @app.get("/v1/models")
    async def _list_models():
        bots = await list_models(holder["c"], sess)
        return {
            "object": "list",
            "data": [
                {"id": b.get("name", ""), "object": "model", "owned_by": "MiniMax"}
                for b in bots
            ] or [{"id": DEFAULT_MODEL, "object": "model", "owned_by": "MiniMax"}],
        }

    @app.post("/v1/chat/completions")
    async def _chat(req: Request):
        body = await req.json()
        msgs = body.get("messages", [])
        last = msgs[-1]["content"] if msgs else ""
        model = body.get("model", DEFAULT_MODEL)
        if model.startswith("minimax/"):
            model = model.split("/", 1)[1]
        is_stream = body.get("stream", False)

        if is_stream:
            async def gen():
                async for chunk_text in chat_stream(holder["c"], sess, last, model=model):
                    yield "data: " + json.dumps({
                        "id": "chatcmpl-stream",
                        "object": "chat.completion.chunk",
                        "model": model,
                        "choices": [{
                            "index": 0,
                            "delta": {"content": chunk_text},
                        }],
                    }, ensure_ascii=False) + "\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(gen(), media_type="text/event-stream")
        else:
            text = await chat(holder["c"], sess, last, model=model)
            return {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }],
            }

    return app


async def chat_stream(
    client: httpx.AsyncClient, sess: Session, message: str,
    agent_id: str = DEFAULT_AGENT_ID, model: str = DEFAULT_MODEL,
) -> AsyncGenerator[str, None]:
    """流式聊天（生成器）。"""
    sid = await create_session(client, sess, agent_id, model)
    path = f"/archon/api/v1/session/{sid}/message"
    qs = build_qs(sess.jwt, sess.user_id, sess.device_uuid, sess.device_id)
    url = f"{STREAM_BASE}{path}?{qs}"
    body = {
        "content": message,
        "model": {
            "provider_id": "minimax",
            "model_id": model,
            "variant": "thinking",
        },
        "turn_id": new_uuid(),
        "enable_team": True,
        "worktreeMode": False,
    }
    headers = build_headers(sess.jwt, body, path, qs)
    headers["Accept"] = "text/event-stream"
    prev_len = 0
    async with client.stream("POST", url, json=body, headers=headers, timeout=60) as r:
        r.raise_for_status()
        async for line in r.aiter_lines():
            # SSE 格式：data:{...}（无空格）
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
            c = chunk.get("msg_content") or ""
            if c and len(c) > prev_len:
                yield c[prev_len:]
                prev_len = len(c)


# ════════════════════════════════════════════════════════════════════════════
# CLI 主流程
# ════════════════════════════════════════════════════════════════════════════

async def amain(args) -> int:
    # 1. 准备 session
    sess: Optional[Session] = None

    if args.phone and args.password:
        # ===== 密码登录 =====
        print(f"\n[1] 密码登录: phone={args.phone}")
        async with httpx.AsyncClient(follow_redirects=False, timeout=30) as login_client:
            sess = await password_login(login_client, args.phone, args.password)
        # 续期用 client 重新建
        client = httpx.AsyncClient(timeout=30)
    elif args.state:
        sess = load_session_from_state(args.state)
        print(f"[1] State 加载: user={sess.user_name!r} userID={sess.user_id} "
              f"expires_in={(sess.expires_at - unix()) // 86400} 天")
        client = httpx.AsyncClient(timeout=30)
    elif args.token:
        jwt, saved_uuid = parse_token_obj(args.token)
        sess = Session(
            jwt=jwt, user_id=extract_user_id(jwt),
            device_uuid=saved_uuid or new_uuid(),
            expires_at=parse_jwt_payload(jwt).get("exp", 0),
        )
        print(f"[1] 直接 token: userID={sess.user_id} expires_in="
              f"{(sess.expires_at - unix()) // 86400} 天")
        client = httpx.AsyncClient(timeout=30)
    elif args.token_file:
        raw = open(args.token_file).read().strip()
        jwt, saved_uuid = parse_token_obj(raw)
        sess = Session(
            jwt=jwt, user_id=extract_user_id(jwt),
            device_uuid=saved_uuid or new_uuid(),
            expires_at=parse_jwt_payload(jwt).get("exp", 0),
        )
        print(f"[1] 从文件加载 token: {args.token_file} userID={sess.user_id}")
        client = httpx.AsyncClient(timeout=30)
    else:
        print("ERROR: 必须提供以下之一：")
        print("  --phone <手机号> --password <密码>    (密码登录)")
        print("  --state <state.json 路径>            (从浏览器 state 加载)")
        print("  --token <JWT>                        (直接给 JWT)")
        print("  --token-file <JWT 文件路径>          (从文件读 JWT)")
        return 1

    try:
        # 2. 续期
        if sess.is_expired:
            print(f"[2] Token 快过期，续期中...")
            new_jwt = await renew_token(client, sess.jwt)
            sess.jwt = new_jwt
            payload = parse_jwt_payload(new_jwt)
            sess.user_id = payload.get("user", {}).get("id", sess.user_id)
            sess.expires_at = payload.get("exp", 0)
            print(f"[2] ✓ 续期成功: userID={sess.user_id} "
                  f"expires_in={(sess.expires_at - unix()) // 86400} 天")
        else:
            days = (sess.expires_at - unix()) // 86400 if sess.expires_at else 0
            print(f"[2] Token 还有 {days} 天，无需续期")

        # 3. 拿 real user_id
        info = await fetch_user_info(client, sess)
        ui = info.get("data", {}).get("userInfo", {})
        real_uid = ui.get("realUserID") or ui.get("userID")
        if real_uid and real_uid != sess.user_id:
            print(f"[3] 修正 user_id: {sess.user_id} -> {real_uid} (realUserID)")
            sess.user_id = real_uid
        print(f"[3] 用户验证: name={ui.get('name')!r} phone={ui.get('phone')!r} "
              f"realUserID={sess.user_id} vipInfo={ui.get('vipInfo')}")

        # 4. 列模型
        if args.list_models:
            print(f"[4] 列可用 agent...")
            try:
                bots = await list_models(client, sess)
                if not bots:
                    print(f"     (官方 /archon/api/v1/agent 返回空，使用内置默认)")
                    print(f"     - {DEFAULT_AGENT_ID}  {DEFAULT_MODEL}  variant=thinking")
                else:
                    print(f"[4] ✓ 可用 agent ({len(bots)} 个):")
                    for b in bots[:20]:
                        print(f"     - id={b.get('id', '?')} name={b.get('name', '?')!r} "
                              f"type={b.get('type', '?')}")
            except Exception as e:
                print(f"[4] 列模型失败: {e}")

        # 5. 聊天
        if args.chat:
            print(f"[5] 发消息: {args.chat!r}")
            reply = await chat(
                client, sess, args.chat,
                model=args.model, stream=args.stream,
            )
            if not args.stream:
                print(f"[5] ✓ 回复: {reply[:2000]}")
            else:
                print()

        # 6. OpenAI 兼容服务
        if args.serve:
            try:
                import uvicorn
            except ImportError:
                print("ERROR: pip install uvicorn")
                return 1
            app = make_openai_app(sess)
            print(f"\n[6] OpenAI 兼容服务已启动: http://0.0.0.0:{args.port}/v1")
            print(f"    试试: curl http://localhost:{args.port}/v1/models")
            uvicorn.run(app, host="0.0.0.0", port=args.port)
    finally:
        await client.aclose()

    return 0


def main():
    p = argparse.ArgumentParser(
        description="MiniMax 端到端聊天：密码登录 + 拿 token + 列模型 + 聊天",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例：
  %(prog)s --phone 19065353709 --password baobao615 --chat "你好"
  %(prog)s --token-file /tmp/jwt.txt --chat "你好"
  %(prog)s --state ./state.json --list-models
  %(prog)s --phone 19065353709 --password baobao615 --serve --port 8910
        """,
    )
    p.add_argument("--phone", help="手机号（与 --password 一起做密码登录）")
    p.add_argument("--password", help="密码")
    p.add_argument("--state", help="agent-browser state.json 路径")
    p.add_argument("--token", help="直接给 JWT token")
    p.add_argument("--token-file", help="从文件读 JWT token")
    p.add_argument("--model", default=DEFAULT_MODEL,
                   help=f"模型名（默认 {DEFAULT_MODEL}）")
    p.add_argument("--chat", help="单轮聊天消息")
    p.add_argument("--stream", action="store_true", help="流式输出")
    p.add_argument("--list-models", action="store_true", help="列可用 agent / 模型")
    p.add_argument("--serve", action="store_true", help="启动 OpenAI 兼容服务")
    p.add_argument("--port", type=int, default=8910, help="OpenAI 服务端口（默认 8910）")
    args = p.parse_args()
    sys.exit(asyncio.run(amain(args)))


if __name__ == "__main__":
    main()
