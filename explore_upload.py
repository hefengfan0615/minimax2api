#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""探索 MiniMax 文件上传 + OpenAI 兼容 API。

设计：跑两件事
  1) 探测文件上传 endpoint（常见命名 + 各种 content-type）
  2) 探测 OpenAI 兼容的 /v1/chat/completions

用法：
    python3 explore_upload.py
    python3 explore_upload.py --phone 19065353709 --password baobao615
    python3 explore_upload.py --probe-only   # 不登录，只跑 HTTP 探测
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
from typing import Dict, List, Optional, Tuple

import httpx

# ═══ 硬编码协议常量（与 minimax_chat.py 一致） ═══
AGENT_URL   = "https://agent.minimaxi.com"
ACCOUNT_URL = "https://account.minimaxi.com"
STREAM_URL  = "https://agent-stream.minimaxi.com"
CLIENT_ID   = "agent-minimax"
SIG_SALT    = "I*7Cf%WZ#S&%1RlZJ&C2"

RSA_PUB_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDF5ndG2/UB4L5tbvQaNLHSoBTW
DKbrNBuOmUIP23eCmC2ELMx3kppEikxTp5cV8NxUZl6ii+KLwKugioAXApzypHXb
gXbq13kTKA7OCA1xtAoMdH9cltjBiFAUJlgmVjr0MuJCknhVAjWLjCVRHege+Atl
gkUBUeGa9O+cWcPEwQIDAQAB
-----END PUBLIC KEY-----"""

QS_FIELDS = ["device_platform","biz_id","app_id","version_code","unix",
             "timezone_offset","sys_language","lang","uuid","device_id",
             "os_name","browser_name","device_memory","cpu_core_num",
             "browser_language","browser_platform","user_id",
             "screen_width","screen_height","token","client","region"]

FP = {"device_platform":"web","biz_id":"3","app_id":"3001","version_code":"22201",
      "os_name":"Linux","browser_name":"Chrome","device_memory":4,"cpu_core_num":2,
      "browser_language":"en-US","browser_platform":"Linux x86_64",
      "screen_width":800,"screen_height":600,"lang":"zh","timezone_offset":0,
      "sys_language":"zh","client":"web","region":"cn"}


# ═══ 工具（同 minimax_chat.py） ═══
def md5(s): return hashlib.md5(s.encode()).hexdigest()

def new_device_id(): return str(random.randint(10_000_000, 99_999_999))

def parse_jwt(jwt):
    p = jwt.split(".")[1] + "=" * ((4 - len(jwt.split(".")[1]) % 4) % 4)
    return json.loads(base64.b64decode(p))

def build_qs(jwt="", user_id="", device_id=None):
    v = dict(FP)
    v.update({"unix":str(int(time.time()*1000)),"uuid":str(uuid.uuid4()),
              "device_id":device_id or new_device_id(),"user_id":user_id,"token":jwt})
    return "&".join(f"{k}={v[k]}" for k in QS_FIELDS)

def sign_headers(jwt, body, path, qs):
    from urllib.parse import quote
    ts = int(time.time())
    bj = json.dumps(body, separators=(",",":"), ensure_ascii=False) if body else ""
    full = f"{path}?{qs}"
    yy = md5(f"{quote(full, safe='')}_{bj}{md5(str(int(time.time()*1000)))}ooui")
    sig = md5(f"{ts}{SIG_SALT}{bj}") if bj else md5(f"{ts}{SIG_SALT}")
    return {"Content-Type":"application/json","Accept":"application/json, text/plain, */*",
            "token":jwt, "x-signature":sig, "x-timestamp":str(ts), "yy":yy}


# ═══ 登录（同 minimax_chat.py） ═══
async def login(client, phone, password):
    from urllib.parse import quote
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    pub = load_pem_public_key(RSA_PUB_PEM.encode())
    auth_token = base64.b64encode(pub.encrypt(password.encode(), padding.PKCS1v15())).decode()
    dev = new_device_id()
    qs = build_qs(device_id=dev)
    state = base64.b64encode(json.dumps({"redirect_uri":f"{AGENT_URL}/","csrf":uuid.uuid4().hex}).encode()).decode()
    lr = (f"/oauth2/authorize?client_id={CLIENT_ID}"
          f"&redirect_uri={quote(AGENT_URL+'/auth/callback', safe='')}"
          f"&response_type=code&source=agent_web&state={quote(state)}")
    r = await client.post(f"{ACCOUNT_URL}/oauth2/login?{qs}", json={
        "loginType":"20","phone":phone,"authToken":auth_token,
        "countryCode":"+86","deviceID":dev,"login_redirect":lr},
        headers={"Content-Type":"application/json"})
    r.raise_for_status()
    if r.json().get("code")!=0: raise RuntimeError(f"login: {r.json()}")
    print(f"  [login] ✓ {r.json()['data']['username']}")
    r = await client.get(f"{ACCOUNT_URL}{r.json()['data']['login_redirect']}")
    loc = r.headers.get("location","")
    if not loc.startswith("http"): loc = AGENT_URL+loc
    await client.get(loc)
    jwt = next(c.value for c in client.cookies.jar if c.name=="_token")
    uid0 = parse_jwt(jwt)["user"]["id"]
    p = "/v1/api/user/info"; qs = build_qs(jwt, uid0, dev)
    r = await client.get(f"{AGENT_URL}{p}?{qs}", headers=sign_headers(jwt,{},p,qs))
    uid = r.json()["data"]["userInfo"].get("realUserID") or uid0
    print(f"  [me] ✓ realUserID={uid}")
    return jwt, uid, dev


# ═══════════════════════════════════════════════════════════════════════════
# 探测 1: 文件上传 endpoint
# ═══════════════════════════════════════════════════════════════════════════
async def probe_upload_endpoints(client, jwt, uid, dev):
    """探测各种可能的文件上传 endpoint"""
    # 准备一个最小文件 (10 字节)
    file_content = b"hello probe" * 10
    file_name = "probe.txt"

    # 候选 endpoint 列表（按可能性排序）
    candidates = [
        # archon (Mavis Agent)
        ("/archon/api/v1/file/upload", "agent", "form"),
        ("/archon/api/v1/files/upload", "agent", "form"),
        ("/archon/api/v1/attachment/upload", "agent", "form"),
        ("/archon/api/v1/attachments/upload", "agent", "form"),
        ("/archon/api/v1/upload", "agent", "form"),
        ("/archon/api/v1/oss/upload", "agent", "form"),
        # /v1/api 命名
        ("/v1/api/file/upload", "agent", "form"),
        ("/v1/api/files/upload", "agent", "form"),
        ("/v1/api/attachment/upload", "agent", "form"),
        ("/v1/api/attachments/upload", "agent", "form"),
        ("/v1/api/upload", "agent", "form"),
        ("/v1/api/upload/file", "agent", "form"),
        ("/v1/api/upload/attachment", "agent", "form"),
        ("/v1/api/oss/upload", "agent", "form"),
        ("/v1/api/oss/file", "agent", "form"),
        # 常见 chat 平台命名
        ("/v1/api/chat/upload", "agent", "form"),
        ("/v1/api/message/upload", "agent", "form"),
        ("/v1/api/image/upload", "agent", "form"),
        ("/v1/api/media/upload", "agent", "form"),
        # stream 域名
        ("/v1/api/file/upload", "stream", "form"),
    ]

    print(f"\n{'='*70}\n[探测 1] 文件上传 endpoint\n{'='*70}")
    found = []
    for path, host, kind in candidates:
        base = AGENT_URL if host == "agent" else STREAM_URL
        qs = build_qs(jwt, uid, dev)
        # 不签名 form-data 上传（很多 endpoint 接受裸 multipart）
        files = {"file": (file_name, file_content, "text/plain")}
        try:
            r = await client.post(f"{base}{path}?{qs}", files=files, timeout=10)
            status = r.status_code
            body = r.text[:300]
            # 解析 body
            try:
                j = r.json()
                code = j.get("code") or j.get("base_resp", {}).get("status_code")
                msg = j.get("msg") or j.get("message") or j.get("base_resp", {}).get("status_msg", "")
            except Exception:
                j = None
                code, msg = None, body[:100]
            # 200 / 业务 code 0 = 命中
            hit = status in (200, 201) and (code == 0 or (j and "data" in j))
            partial = status == 200
            marker = "✓" if hit else ("·" if partial else " ")
            if status != 404:
                print(f"  {marker} {status} {path:50s}  code={code}  {msg[:80]!r}")
            if hit:
                found.append((path, host, j))
        except Exception as e:
            pass  # ignore
    return found


# ═══════════════════════════════════════════════════════════════════════════
# 探测 2: OpenAI 兼容 API
# ═══════════════════════════════════════════════════════════════════════════
async def probe_openai_compat(client, jwt, uid, dev):
    """探测 /v1/chat/completions 等 OpenAI 兼容 endpoint"""
    print(f"\n{'='*70}\n[探测 2] OpenAI 兼容 API\n{'='*70}")

    candidates = [
        # 路径 / 域名组合
        ("/v1/chat/completions", AGENT_URL),
        ("/v1/chat/completions", STREAM_URL),
        ("/v1/models", AGENT_URL),
        ("/v1/models", STREAM_URL),
        ("/openai/v1/chat/completions", AGENT_URL),
        ("/openai/v1/models", AGENT_URL),
        ("/api/v1/chat/completions", AGENT_URL),
        ("/api/v1/models", AGENT_URL),
        # 也试 mavis/mavis-chat 命名
        ("/mavis/api/v1/chat/completions", AGENT_URL),
    ]
    found = []
    for path, base in candidates:
        qs = build_qs(jwt, uid, dev)
        # 试 GET 看模型列表
        if path.endswith("/models"):
            try:
                r = await client.get(f"{base}{path}?{qs}", timeout=10)
                if r.status_code == 200:
                    print(f"  ✓ GET  {status(r)} {path} {r.text[:200]}")
                    found.append((path, "GET", r.json()))
            except Exception as e:
                pass
        else:
            # POST chat completion
            body = {
                "model": "MiniMax-M3",
                "messages": [{"role": "user", "content": "1+1=?  只回答数字"}],
                "max_tokens": 10,
            }
            try:
                r = await client.post(f"{base}{path}?{qs}", json=body,
                                      headers=sign_headers(jwt, body, path, qs), timeout=15)
                if r.status_code == 200 and len(r.text) > 50:
                    print(f"  ✓ POST {r.status_code} {path}")
                    print(f"      {r.text[:300]}")
                    found.append((path, "POST", r.text))
                else:
                    print(f"  · {r.status_code} {path}  {r.text[:80]!r}")
            except Exception as e:
                print(f"  · ERR {path}: {e}")
    return found


# ═══════════════════════════════════════════════════════════════════════════
# 探测 3: 监听 mavis 已知 endpoints（通过 archon-config + skill-hub 类比）
# ═══════════════════════════════════════════════════════════════════════════
async def probe_archon_api(client, jwt, uid, dev):
    """探测 /archon/api/v1/* 下的所有路由（HEAD/OPTIONS 探测）"""
    print(f"\n{'='*70}\n[探测 3] /archon/api/v1/* 路由扫\n{'='*70}")

    # 已知存在的 routes + 推测的
    candidates = [
        "/archon/api/v1/config",                # GET 已知
        "/archon/api/v1/skill-hub",             # GET 已知
        "/archon/api/v1/agent/list",            # ?
        "/archon/api/v1/agent/session/list",    # ?
        "/archon/api/v1/session/list",          # ?
        "/archon/api/v1/session/{sid}/message", # POST 已知
        "/archon/api/v1/session/{sid}/file",    # ?
        "/archon/api/v1/file/upload",           # 推测
        "/archon/api/v1/agent/{aid}/session",   # POST 已知
        "/archon/api/v1/agent/{aid}/file",      # 推测
    ]
    for path in candidates:
        for method in ("GET", "POST"):
            qs = build_qs(jwt, uid, dev)
            url = f"{AGENT_URL}{path}?{qs}"
            try:
                r = await client.request(method, url, headers=sign_headers(jwt, {}, path, qs),
                                         json={}, timeout=8)
                marker = "✓" if r.status_code == 200 else ("·" if r.status_code != 404 else " ")
                if r.status_code != 404:
                    body = r.text[:100].replace("\n", " ")
                    print(f"  {marker} {method:4s} {r.status_code} {path:50s}  {body}")
            except Exception as e:
                pass


# ═══════════════════════════════════════════════════════════════════════════
# 探测 4: 直接抓登录后实际的网络请求（需要登录态；这里复用 minimax_chat.py）
# ═══════════════════════════════════════════════════════════════════════════
async def dump_jwt_to_file(jwt, uid, dev):
    """把 token 写到 env vars / 文件，方便 curl 直接用"""
    path = "/tmp/minimax_token.txt"
    with open(path, "w") as f:
        f.write(f"JWT={jwt}\n")
        f.write(f"UID={uid}\n")
        f.write(f"DEVICE_ID={dev}\n")
    os.chmod(path, 0o600)
    print(f"  [token] saved → {path}")


# ═══════════════════════════════════════════════════════════════════════════
# 完整文件上传（浏览器探到的流程：request_policy → PUT to OSS → policy_callback）
# ═══════════════════════════════════════════════════════════════════════════
async def upload_file(client, jwt, uid, dev, file_path: str) -> dict:
    """完整上传：拿 STS → PUT 到 OSS → callback。返回服务端最终确认的文件元数据。"""
    import hashlib, mimetypes, os, uuid
    file_name = os.path.basename(file_path)
    mime = mimetypes.guess_type(file_name)[0] or "application/octet-stream"
    with open(file_path, "rb") as f:
        content = f.read()

    # 1) request_policy — 拿 STS
    p1 = "/v1/api/files/request_policy"
    qs1 = build_qs(jwt, uid, dev)
    r = await client.get(f"{AGENT_URL}{p1}?{qs1}",
                         headers=sign_headers(jwt, {}, p1, qs1), timeout=15)
    r.raise_for_status()
    d = r.json()["data"]
    print(f"  [policy] dir={d['dir']}, expiry={d['expiration']}")

    # 2) PUT 到 OSS（直传）
    object_name = f"{uuid.uuid4()}{os.path.splitext(file_name)[1]}"
    oss_key = f"{d['dir']}/{object_name}"
    oss_url = f"https://{d['bucketName']}.{d['endpoint']}/{oss_key}"

    import oss2
    auth = oss2.StsAuth(d["accessKeyId"], d["accessKeySecret"], d["securityToken"])
    bucket = oss2.Bucket(auth, f"https://{d['endpoint']}", d["bucketName"])
    headers = {"Content-Disposition": f"attachment;filename={file_name};",
               "Content-Type": mime}
    bucket.put_object(oss_key, content, headers=headers)
    file_md5 = hashlib.md5(content).hexdigest()
    print(f"  [oss] PUT {oss_url[:80]}...  md5={file_md5}")

    # 3) policy_callback — 通知服务端
    p3 = "/v1/api/files/policy_callback"
    qs3 = build_qs(jwt, uid, dev)
    cb_body = {
        "fileName": object_name,
        "originFileName": file_name,
        "dir": d["dir"],
        "endpoint": d["endpoint"],
        "bucketName": d["bucketName"],
        "size": str(len(content)),
        "mimeType": mime,
        "fileMd5": file_md5,
    }
    r = await client.post(f"{AGENT_URL}{p3}?{qs3}", json=cb_body,
                          headers=sign_headers(jwt, cb_body, p3, qs3), timeout=15)
    r.raise_for_status()
    cb = r.json()
    print(f"  [callback] {cb.get('statusInfo', {}).get('message', '?')}")
    file_url = f"https://{d['bucketName']}.{d['endpoint']}/{oss_key}"
    return {**cb.get("data", {}), "url": file_url, "size": len(content),
            "md5": file_md5, "name": file_name, "mime": mime}


# ═══════════════════════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════════════════════
async def main():
    p = argparse.ArgumentParser()
    p.add_argument("--phone", default=os.environ.get("MM_PHONE"))
    p.add_argument("--password", default=os.environ.get("MM_PASSWORD"))
    p.add_argument("--probe-only", action="store_true",
                   help="只跑 HTTP 探测（不登录）")
    p.add_argument("--upload-only", action="store_true")
    p.add_argument("--openai-only", action="store_true")
    p.add_argument("--upload", help="上传本地文件路径")
    args = p.parse_args()

    if not args.probe_only and (not args.phone or not args.password):
        p.error("--phone 和 --password 必填（或用 --probe-only）")

    client = httpx.AsyncClient(follow_redirects=False, timeout=30)
    try:
        if not args.probe_only:
            print(f"[0] 登录")
            jwt, uid, dev = await login(client, args.phone, args.password)
            await dump_jwt_to_file(jwt, uid, dev)
        else:
            jwt, uid, dev = "", "0", new_device_id()

        if args.upload:
            print(f"\n[上传] {args.upload}")
            meta = await upload_file(client, jwt, uid, dev, args.upload)
            print(f"\n[结果] {json.dumps(meta, indent=2, ensure_ascii=False)[:600]}")
            return

        if not args.openai_only:
            await probe_upload_endpoints(client, jwt, uid, dev)
            await probe_archon_api(client, jwt, uid, dev)
        if not args.upload_only:
            await probe_openai_compat(client, jwt, uid, dev)

    finally:
        await client.aclose()


def status(r): return r.status_code

if __name__ == "__main__":
    asyncio.run(main())
