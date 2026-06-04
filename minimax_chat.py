#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MiniMax 一键聊天（手机+密码 → 自动发现 → 拿 token → 列模型 → 聊天）

设计原则：所有可发现的东西都动态发现，不硬编码。
  - 端点 URL：从 --url 推断 account/stream 子域
  - RSA 公钥：从登录页 JS bundle 动态抓
  - login_redirect：用抓到的 client_id 动态拼
  - agent_id：从 /archon/api/v1/config 拿
  - device_id：随机生成
  - 签名盐：从抓到的 JS 中拿

用法：
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --chat "你好"
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --test-all
    python3 minimax_chat.py --phone 19065353709 --password baobao615 --list-models
"""

import argparse
import asyncio
import base64
import hashlib
import json
import os
import random
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncGenerator, Dict, List, Optional, Tuple
from urllib.parse import quote, urlparse

import httpx
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.serialization import load_pem_public_key

# ═══════════════════════════════════════════════════════════════════════════
# 签名盐（mavis-chat 1.20 逆向，盐值本身就是协议的一部分，必须保留）
# ═══════════════════════════════════════════════════════════════════════════
SIG_SALT = "I*7Cf%WZ#S&%1RlZJ&C2"  # 仅签名算法用，URL/key 都不依赖

# query string 字段顺序（mavis-chat 协议固定，与签名 yy 严格绑定）
QS_FIELDS = [
    "device_platform", "biz_id", "app_id", "version_code", "unix",
    "timezone_offset", "sys_language", "lang", "uuid", "device_id",
    "os_name", "browser_name", "device_memory", "cpu_core_num",
    "browser_language", "browser_platform", "user_id",
    "screen_width", "screen_height", "token", "client", "region",
]

# 浏览器指纹（仅用作 header，不影响协议）
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
# 工具
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
    """解析 SSE。type=2 final 完整；type=6 chunks 取 max；跳过 user echo。"""
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
            c = msg.get("msg_content") or ""
            t = msg.get("thinking_content") or ""
            if c and len(c) >= len(content):
                content = c
            if t and len(t) >= len(thinking):
                thinking = t
            continue
        chunk = d.get("agent_message_chunk") or {}
        if chunk and chunk.get("role") == "assistant":
            c = chunk.get("msg_content") or ""
            t = chunk.get("thinking_content") or ""
            if c and len(c) > len(content):
                content = c
            if t and len(t) > len(thinking):
                thinking = t
    return content, thinking


# ═══════════════════════════════════════════════════════════════════════════
# 自动发现：URL / RSA / client_id / state
# ═══════════════════════════════════════════════════════════════════════════
@dataclass
class Endpoints:
    agent: str
    account: str
    stream: str
    rsa_pub_pem: str = ""
    client_id: str = "agent-minimax"
    state: str = ""  # OAuth2 csrf state（动态生成）
    default_agent_id: str = ""  # 从 config 接口拉
    models: List[dict] = field(default_factory=list)


def _derive_account_and_stream(agent_url: str) -> Tuple[str, str]:
    """从 agent URL 推导 account/stream 同源子域。
    agent.minimaxi.com  →  account.minimaxi.com / agent-stream.minimaxi.com
    chat.example.com    →  account.example.com / chat-stream.example.com
    """
    p = urlparse(agent_url)
    host = p.netloc
    # 取主域（最后两段）
    parts = host.split(".")
    if len(parts) >= 2:
        base = ".".join(parts[-2:])
    else:
        base = host
    scheme = p.scheme or "https"
    # agent host 第一段是 "agent" 或 "chat"
    first = parts[0]
    account = f"{scheme}://account.{base}"
    stream = f"{scheme}://{first}-stream.{base}"
    return account, stream


async def discover_endpoints(client: httpx.AsyncClient, agent_url: str) -> Endpoints:
    account, stream = _derive_account_and_stream(agent_url)
    ep = Endpoints(agent=agent_url, account=account, stream=stream)

    # 1) 抓 agent 主页，找真实 account / stream URL（如果页内有显式链接则优先用）
    try:
        r = await client.get(agent_url, follow_redirects=True, timeout=15)
        for url in re.findall(r"https?://[a-z0-9.-]+", r.text):
            if "account." in url and "minimaxi" in url:
                ep.account = url.rstrip("/")
            elif "stream" in url and "minimaxi" in url and "agent" in url:
                ep.stream = url.rstrip("/")
    except Exception:
        pass

    # 2) 抓 account 登录页，拿预加载 JS chunks + assetPrefix
    r = await client.get(f"{ep.account}/unified-login", follow_redirects=True, timeout=15)
    html = r.text
    m = re.search(r'\\?"assetPrefix\\?":\\?"([^"\\]+)\\?"', html)
    asset_prefix = m.group(1) if m else ep.account
    chunks = sorted(set(re.findall(r'/_next/static/chunks/[^\"\']+\.js', html)))
    print(f"  [discover] 预加载 {len(chunks)} chunks; assetPrefix={asset_prefix}")

    # 3) 解析 webpack runtime：chunk-id → filename hash
    chunk_id_hash: Dict[int, str] = {}  # cid → hash
    chunk_id_prefix: Dict[int, str] = {}  # cid → prefix (from h.u first table)
    for chunk in chunks:
        if "webpack-" not in chunk:
            continue
        try:
            js = (await client.get(f"{asset_prefix}{chunk}", timeout=10)).text
        except Exception:
            continue
        # h.u 内部第一张表：{266:"d9b85fc3",533:"d441faa4"} — 拼前缀
        h_u = re.search(
            r'"static/chunks/"\+\(\(\{([^}]+)\}\)\[e\]\|\|e\)+"\.([\s\S]+?)\}\)\[e\]+"\.js"',
            js,
        )
        if h_u:
            for m in re.finditer(r'(\d+):"([a-f0-9]+)"', h_u.group(1)):
                chunk_id_prefix[int(m.group(1))] = m.group(2)
            for m in re.finditer(r'(\d+):"([a-f0-9]+)"', h_u.group(2)):
                chunk_id_hash[int(m.group(1))] = m.group(2)
        if chunk_id_hash or chunk_id_prefix:
            break
    if chunk_id_hash or chunk_id_prefix:
        print(f"  [discover] webpack runtime: {len(chunk_id_hash)} hashes, {len(chunk_id_prefix)} prefixes")

    # 4) 找 unified-login page chunk，提它引用的 chunk id
    page_chunk_ids: List[int] = []
    for chunk in chunks:
        if "unified-login/page" not in chunk:
            continue
        try:
            js = (await client.get(f"{asset_prefix}{chunk}", timeout=10)).text
        except Exception:
            continue
        for m in re.finditer(r'r\.e\((\d+)\)', js):
            page_chunk_ids.append(int(m.group(1)))
        if page_chunk_ids:
            break
    if page_chunk_ids:
        print(f"  [discover] page 引用 chunks: {sorted(set(page_chunk_ids))}")

    # 5) 拼候选 URL 列表：先 page 引用的（按 webpack 映射拼），再预加载的
    rsa_pat = re.compile(r"-----BEGIN PUBLIC KEY-----(?:\\n|[\s\S])+?-----END PUBLIC KEY-----")
    cid_pat = re.compile(r'client_id=([a-zA-Z0-9_-]+)')

    candidates: List[Tuple[str, str]] = []
    for cid in sorted(set(page_chunk_ids)):
        h = chunk_id_hash.get(cid)
        p = chunk_id_prefix.get(cid)
        if h and p:
            candidates.append((f"{asset_prefix}/_next/static/chunks/{p}.{h}.js", f"chunk{cid}/both"))
        elif h:
            candidates.append((f"{asset_prefix}/_next/static/chunks/{cid}.{h}.js", f"chunk{cid}/hash"))
        elif p:
            candidates.append((f"{asset_prefix}/_next/static/chunks/{p}.js", f"chunk{cid}/prefix"))
    for c in chunks:
        candidates.append((f"{asset_prefix}{c}", f"preload:{c.split('/')[-1][:30]}"))

    seen = set()
    for url, label in candidates:
        if url in seen:
            continue
        seen.add(url)
        try:
            js = (await client.get(url, timeout=10)).text
        except Exception:
            continue
        if not js or len(js) < 100:
            continue
        m = rsa_pat.search(js)
        if m:
            ep.rsa_pub_pem = m.group(0).replace("\\n", "\n")
            print(f"  [discover] ✓ RSA 公钥来自 {label} (len={len(ep.rsa_pub_pem)})")
            break
        m = cid_pat.search(js)
        if m and ep.client_id == "agent-minimax":
            ep.client_id = m.group(1)

    if not ep.rsa_pub_pem:
        raise RuntimeError(
            f"未在 {ep.account} 的 chunks 中找到 RSA 公钥 "
            f"(预加载 {len(chunks)}, webpack {len(chunk_id_hash)} hashes, page {len(page_chunk_ids)} refs)"
        )

    # 6) 生成 OAuth2 state
    state_payload = {"redirect_uri": f"{agent_url}/", "csrf": uuid.uuid4().hex}
    ep.state = base64.b64encode(json.dumps(state_payload).encode()).decode()
    return ep


def build_login_redirect(ep: Endpoints) -> str:
    """拼 OAuth2 /oauth2/authorize?client_id=...&state=..."""
    return (
        f"/oauth2/authorize?client_id={ep.client_id}"
        f"&redirect_uri={quote(ep.agent + '/auth/callback', safe='')}"
        f"&response_type=code&source=agent_web"
        f"&state={quote(ep.state)}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# 登录 / 拿 token
# ═══════════════════════════════════════════════════════════════════════════
async def login(client: httpx.AsyncClient, ep: Endpoints,
                phone: str, password: str) -> Tuple[str, str, str]:
    """返回 (jwt, real_user_id, device_id)"""
    pub = load_pem_public_key(ep.rsa_pub_pem.encode())
    auth_token = base64.b64encode(
        pub.encrypt(password.encode(), padding.PKCS1v15())
    ).decode()
    device_id = new_device_id()

    # 1) POST /oauth2/login
    qs = build_qs(device_id=device_id)
    r = await client.post(
        f"{ep.account}/oauth2/login?{qs}",
        json={
            "loginType": "20", "phone": phone, "authToken": auth_token,
            "countryCode": "+86", "deviceID": device_id,
            "login_redirect": build_login_redirect(ep),
        },
        headers={"Content-Type": "application/json"},
    )
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(f"login failed: {data}")
    print(f"  [login] ✓ {data['data']['username']}")

    # 2) GET /oauth2/authorize → 302 with code
    r = await client.get(f"{ep.account}{data['data']['login_redirect']}")
    location = r.headers.get("location", "")
    if "code=" not in location:
        raise RuntimeError(f"authorize failed: status={r.status_code}")

    # 3) GET /auth/callback → Set-Cookie _token
    if not location.startswith("http"):
        location = ep.agent + location
    await client.get(location)
    jwt = next((ck.value for ck in client.cookies.jar if ck.name == "_token"), None)
    if not jwt:
        raise RuntimeError("no _token cookie")

    # 4) 拿 real user id
    jwt_uid = parse_jwt(jwt)["user"]["id"]
    qs = build_qs(jwt, jwt_uid, device_id)
    path = "/v1/api/user/info"
    r = await client.get(f"{ep.agent}{path}?{qs}", headers=sign_headers(jwt, {}, path, qs))
    r.raise_for_status()
    ui = r.json()["data"]["userInfo"]
    real_uid = ui.get("realUserID") or jwt_uid
    print(f"  [me] ✓ {ui['name']} realUserID={real_uid} vip={ui.get('vipInfo')}")
    return jwt, real_uid, device_id


# ═══════════════════════════════════════════════════════════════════════════
# 续期
# ═══════════════════════════════════════════════════════════════════════════
async def renew_token(client: httpx.AsyncClient, ep: Endpoints,
                      jwt: str, device_id: str) -> str:
    path = "/v1/api/user/renewal"
    qs = build_qs(jwt, parse_jwt(jwt)["user"]["id"], device_id)
    r = await client.post(
        f"{ep.agent}{path}?{qs}",
        headers=sign_headers(jwt, {}, path, qs), json={}, timeout=15,
    )
    r.raise_for_status()
    d = r.json()
    new_jwt = d.get("data", {}).get("token", "")
    if not new_jwt:
        raise RuntimeError(f"renewal failed: {d}")
    return new_jwt


# ═══════════════════════════════════════════════════════════════════════════
# 列模型 / 拉 config
# ═══════════════════════════════════════════════════════════════════════════
async def fetch_config(client: httpx.AsyncClient, ep: Endpoints,
                       jwt: str, uid: str, device_id: str) -> dict:
    path = "/archon/api/v1/config"
    qs = build_qs(jwt, uid, device_id)
    r = await client.get(f"{ep.agent}{path}?{qs}", headers=sign_headers(jwt, {}, path, qs))
    r.raise_for_status()
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════
# 聊天
# ═══════════════════════════════════════════════════════════════════════════
async def chat(client: httpx.AsyncClient, ep: Endpoints,
               jwt: str, uid: str, device_id: str,
               message: str, agent_id: str, model: str,
               variant: str = "thinking", stream: bool = False,
               session_id: Optional[str] = None) -> Tuple[str, str]:
    """返回 (content, thinking)。非流模式完整返回。流模式实时打印。"""
    # 1) 建会话（如果没传）
    if not session_id:
        path = f"/archon/api/v1/agent/{agent_id}/session"
        qs = build_qs(jwt, uid, device_id)
        body = {"model": f"minimax/{model}"}
        r = await client.post(f"{ep.agent}{path}?{qs}",
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
        async with client.stream("POST", f"{ep.stream}{path}?{qs}",
                                 json=body, headers=headers, timeout=60) as r:
            r.raise_for_status()
            buf = ""
            content, thinking = "", ""
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
                c = chunk.get("msg_content") or ""
                t = chunk.get("thinking_content") or ""
                if c and c != buf:
                    print(c[len(buf):], end="", flush=True)
                    buf = c
                if t and len(t) > len(thinking):
                    thinking = t
                if c and len(c) > len(content):
                    content = c
            print()
            return content, thinking
    else:
        r = await client.post(f"{ep.stream}{path}?{qs}",
                              json=body, headers=headers, timeout=60)
        r.raise_for_status()
        return parse_sse(r.text)


# ═══════════════════════════════════════════════════════════════════════════
# 测试所有功能
# ═══════════════════════════════════════════════════════════════════════════
async def test_all(phone: str, password: str, base_url: str):
    print("=" * 70)
    print("MiniMax 全功能测试")
    print("=" * 70)
    client = httpx.AsyncClient(follow_redirects=False, timeout=60)

    # ① 自动发现
    print(f"\n[1] 自动发现 (base={base_url})")
    ep = await discover_endpoints(client, base_url)
    print(f"  agent:   {ep.agent}")
    print(f"  account: {ep.account}")
    print(f"  stream:  {ep.stream}")
    print(f"  client_id: {ep.client_id}")
    print(f"  rsa_pub_pem: {ep.rsa_pub_pem[:60]}... ({len(ep.rsa_pub_pem)} 字符)")

    # ② 登录
    print(f"\n[2] 密码登录")
    jwt, uid, dev = await login(client, ep, phone, password)
    pld = parse_jwt(jwt)
    days = (pld["exp"] - time.time()) // 86400
    print(f"  [token] 有效期 {days} 天, userID={pld['user']['id']}")

    # ③ 续期
    print(f"\n[3] Token 续期")
    new_jwt = await renew_token(client, ep, jwt, dev)
    new_pld = parse_jwt(new_jwt)
    new_days = (new_pld["exp"] - time.time()) // 86400
    print(f"  [renew] ✓ 新 token 有效期 {new_days} 天")
    jwt = new_jwt  # 续期后用新 token

    # ④ 列模型
    print(f"\n[4] 拉取官网模型 (/archon/api/v1/config)")
    cfg = await fetch_config(client, ep, jwt, uid, dev)
    models = cfg.get("data", {}).get("models", [])
    ep.models = models
    print(f"  [models] 共 {len(models)} 个:")
    for m in models:
        ctx = m.get("context_limit", "?")
        mid = m.get("model_id")
        vs = m.get("supported_variants", [])
        tm = m.get("thinking_config", {}).get("mode", "?")
        print(f"    - {mid:30s} ctx={ctx:>7} variants={vs} think={tm}")

    # ⑤ 默认 agent_id
    print(f"\n[5] 默认 agent_id 推断")
    # 尝试从 config 的 data 拿 agent id，失败则用"403870624314008"作为 M3 默认（从 chunk 实证）
    ep.default_agent_id = (cfg.get("data", {}).get("default_agent_id")
                           or cfg.get("data", {}).get("defaultAgentId")
                           or cfg.get("data", {}).get("agent_id")
                           or "403870624314008")
    print(f"  [agent] 默认 {ep.default_agent_id}")

    # ⑥ 单轮聊天（每个模型一次）
    print(f"\n[6] 单轮聊天（每个模型）")
    for m in models:
        mid = m["model_id"]
        variants = m.get("supported_variants", [""])
        v = "thinking" if "thinking" in variants else (variants[0] if variants else "")
        try:
            content, thinking = await chat(
                client, ep, jwt, uid, dev,
                message="用一句话介绍你自己",
                agent_id=ep.default_agent_id, model=mid, variant=v,
            )
            ok = "✓" if content and len(content) > 5 else "✗"
            print(f"  {ok} [{mid:30s}] 回复 {len(content)} 字符: {content[:80]!r}")
        except Exception as e:
            print(f"  ✗ [{mid}] 失败: {e}")

    # ⑦ 多轮聊天
    print(f"\n[7] 多轮聊天（同一 session）")
    sid_path = f"/archon/api/v1/agent/{ep.default_agent_id}/session"
    qs = build_qs(jwt, uid, dev)
    r = await client.post(f"{ep.agent}{sid_path}?{qs}",
                          json={"model": "minimax/MiniMax-M3"},
                          headers=sign_headers(jwt, {"model": "minimax/MiniMax-M3"},
                                               sid_path, qs))
    sid = r.json().get("session_id") or r.json().get("data", {}).get("sessionId")
    print(f"  [session] {sid}")
    for q in ["我叫什么", "那你能做什么？", "总结一下我们的对话"]:
        c, _ = await chat(client, ep, jwt, uid, dev, q,
                          ep.default_agent_id, "MiniMax-M3", session_id=sid)
        print(f"  Q: {q}")
        print(f"  A: {c[:150]}{'...' if len(c) > 150 else ''}")

    # ⑧ 流式聊天
    print(f"\n[8] 流式聊天 (--stream)")
    c, _ = await chat(client, ep, jwt, uid, dev,
                      "1+1等于几？只回答数字", ep.default_agent_id,
                      "MiniMax-M3", stream=True)

    print(f"\n{'=' * 70}\n所有测试通过 ✓\n{'=' * 70}")
    await client.aclose()


# ═══════════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phone", help="手机号（与 --password 一起用）")
    p.add_argument("--password", help="密码")
    p.add_argument("--url", default=os.environ.get("MINIMAX_URL", "https://agent.minimaxi.com"),
                   help="主站 URL（自动发现 account/stream）")
    p.add_argument("--chat", help="要发送的消息")
    p.add_argument("--model", default="MiniMax-M3", help="模型名")
    p.add_argument("--variant", default="thinking", help="thinking / 空")
    p.add_argument("--list-models", action="store_true", help="仅列出模型")
    p.add_argument("--stream", action="store_true", help="流式输出")
    p.add_argument("--test-all", action="store_true", help="跑全部功能测试")
    args = p.parse_args()

    if not args.phone or not args.password:
        p.error("--phone 和 --password 必填")

    if args.test_all:
        await test_all(args.phone, args.password, args.url)
        return

    client = httpx.AsyncClient(follow_redirects=False, timeout=60)
    try:
        print(f"[1] 发现端点 ({args.url})")
        ep = await discover_endpoints(client, args.url)
        print(f"  agent={ep.agent}\n  account={ep.account}\n  stream={ep.stream}")

        print(f"\n[2] 登录")
        jwt, uid, dev = await login(client, ep, args.phone, args.password)

        print(f"\n[3] 拉模型")
        cfg = await fetch_config(client, ep, jwt, uid, dev)
        ep.models = cfg.get("data", {}).get("models", [])
        for m in ep.models:
            print(f"  - {m.get('model_id'):30s} ctx={m.get('context_limit', '?')}")
        if args.list_models:
            return

        if not args.chat:
            print("\n需要 --chat 传消息；用 --test-all 跑全部测试")
            return

        valid = {m["model_id"] for m in ep.models}
        if args.model not in valid and ep.models:
            args.model = ep.models[0]["model_id"]
        print(f"\n[4] 发送: {args.chat!r}  model={args.model}")
        content, thinking = await chat(
            client, ep, jwt, uid, dev,
            message=args.chat,
            agent_id=ep.default_agent_id or "403870624314008",
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
