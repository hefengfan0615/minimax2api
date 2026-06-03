"""test_ai_stream.py — 用新 JWT 端到端测试一次 MiniMax AI 对话。

流程:
  1. 读 config.json 里的 auth_token
  2. /v1/api/user/device/register 注册一个 device_id (每个 token 第一次都要)
  3. /matrix/api/v1/chat/send_msg 发送 "hi"
  4. /matrix/api/v1/chat/get_chat_detail 轮询 AI 回复
  5. 打印完整响应

复用 minimax_adapter.py 里的签名 / 设备指纹。
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

from minimax_adapter import (
    AGENT_BASE_URL,
    FAKE_HEADERS,
    FAKE_USER_DATA,
    _md5,
    _unix,
    _unix_ms,
    _uuid,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_ai")

CONFIG_PATH = Path(__file__).parent / "config.json"


def _build_qs(uid, did, jwt, real_uid, extra=None):
    extra = extra or {}
    d = dict(FAKE_USER_DATA)
    d["uuid"] = uid
    d["device_id"] = did
    d["unix"] = _unix_ms()
    d["user_id"] = real_uid
    d["token"] = jwt
    for k, v in extra.items():
        d[k] = v
    return "&".join(f"{k}={v}" for k, v in d.items() if v is not None)


def _compute_yy(uri, body):
    from urllib.parse import quote
    return _md5(f"{quote(uri, safe='')}_{body}{_md5(_unix_ms())}ooui")


def _compute_sig(ts, jwt, body):
    return _md5(f"{ts}{jwt}{body}")


def _decode(content, encoding):
    if "br" in (encoding or "").lower():
        try:
            import brotli
            content = brotli.decompress(content)
        except Exception:
            pass
    elif "gzip" in (encoding or "").lower():
        import gzip
        content = gzip.decompress(content)
    try:
        return json.loads(content.decode("utf-8", errors="replace"))
    except Exception:
        return {}


def _jwt_payload(token):
    import base64
    try:
        b64 = token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)
        return json.loads(base64.b64decode(b64))
    except Exception:
        return {}


def register_device(client, jwt, real_uid):
    """注册设备: uuid 必须是 random uuid, 不要带 device_id."""
    path = "/v1/api/user/device/register"
    random_uuid = _uuid()
    body = {"uuid": random_uuid}
    # device/register 时不要传 device_id
    qs = _build_qs(random_uuid, "", jwt, real_uid)  # device_id 空
    from urllib.parse import quote
    body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    uri = f"{path}?{qs}"
    yy = _compute_yy(uri, body_str)
    sig = _compute_sig(_unix(), jwt, body_str)
    headers = {
        **FAKE_HEADERS,
        "Content-Type": "application/json",
        "Referer": "https://agent.minimaxi.com/",
        "token": jwt,
        "x-timestamp": str(_unix()),
        "x-signature": sig,
        "yy": yy,
    }
    r = client.post(AGENT_BASE_URL + uri, content=body_str, headers=headers, timeout=20.0)
    d = _decode(r.content, r.headers.get("content-encoding", ""))
    log.info("device/register → HTTP %d  statusInfo=%s",
             r.status_code, d.get("statusInfo", {}).get("code"))
    log.info("  data: %s", json.dumps(d.get("data", {}), ensure_ascii=False)[:300])
    data = d.get("data", {})
    return data.get("deviceIDStr", "") or "0", data.get("realUserID", real_uid)


def make_signed_post(client, path, body_dict, jwt, real_uid, device_id, uuid_val):
    """发一个带完整签名的 POST。uuid = real_user_id (跟 device register 区别开)."""
    body = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)
    # API call 时 uuid 应该是 real_user_id
    qs_uuid = real_uid if not uuid_val or uuid_val == real_uid else uuid_val
    qs = _build_qs(qs_uuid, device_id, jwt, real_uid)
    from urllib.parse import quote
    uri = f"{path}?{qs}"
    yy = _compute_yy(uri, body)
    sig = _compute_sig(_unix(), jwt, body)
    headers = {
        **FAKE_HEADERS,
        "Content-Type": "application/json",
        "Referer": "https://agent.minimaxi.com/",
        "token": jwt,
        "x-timestamp": str(_unix()),
        "x-signature": sig,
        "yy": yy,
    }
    r = client.post(AGENT_BASE_URL + uri, content=body, headers=headers, timeout=20.0)
    return r, _decode(r.content, r.headers.get("content-encoding", ""))


def send_msg(client, jwt, real_uid, device_id, uuid_val, text):
    path = "/matrix/api/v1/chat/send_msg"
    body = {
        "msg_type": 1,
        "text": f"user:{text}",
        "chat_type": 1,
        "attachments": [],
        "selected_mcp_tools": [],
        "backend_config": {},
        "sub_agent_ids": [],
    }
    r, d = make_signed_post(client, path, body, jwt, real_uid, device_id, uuid_val)
    log.info("send_msg → HTTP %d  base_resp=%s",
             r.status_code, d.get("base_resp", {}).get("status_code"))
    log.info("  chat_id=%s  msg_id=%s", d.get("chat_id"), d.get("msg_id"))
    return d.get("chat_id", ""), d.get("msg_id", "")


def poll_response(client, jwt, real_uid, device_id, uuid_val, chat_id,
                  max_polls=120, poll_interval=0.5):
    """轮询 get_chat_detail 直到拿到 msg_type=2 的 AI 消息。"""
    path = "/matrix/api/v1/chat/get_chat_detail"
    body = {"chat_id": chat_id}

    log.info("开始轮询 chat_id=%s ...", chat_id)
    start = time.time()
    for i in range(max_polls):
        r, d = make_signed_post(client, path, body, jwt, real_uid, device_id, uuid_val)
        if r.status_code != 200:
            if i < 3:
                log.warning("  poll[%d] HTTP %d", i, r.status_code)
            time.sleep(poll_interval)
            continue

        messages = d.get("messages", [])
        ai_msgs = [m for m in messages if m.get("msg_type") == 2]
        if ai_msgs:
            ai_msg = ai_msgs[-1]
            if ai_msg.get("msg_content"):
                log.info("✓ 拿到 AI 回复 (第 %d 次轮询, 耗时 %.1fs)",
                         i + 1, time.time() - start)
                return ai_msg

        # 每 10 次打印一次进度
        if i % 10 == 0:
            elapsed = time.time() - start
            log.info("  poll[%d] %.1fs, messages=%d (等待中…)", i, elapsed, len(messages))
        time.sleep(poll_interval)

    raise RuntimeError(f"轮询超时 (>{max_polls * poll_interval}s), 没拿到 AI 回复")


def main() -> int:
    cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
    account = next((a for a in cfg.get("accounts", [])
                    if a.get("auth_mode") == "token"
                    and a.get("base_url", "").startswith("https://agent.minimaxi.com")), None)
    if not account:
        log.error("config.json 里没找到 web-token 账号")
        return 1
    jwt = account["auth_token"]
    real_uid = str(_jwt_payload(jwt).get("user", {}).get("id", "0"))
    log.info("使用账号: name=%s, userID=%s", account.get("name"), real_uid)

    text = "你好, 请用 3 句话介绍你自己"
    log.info("发送消息: %r", text)

    with httpx.Client(timeout=20.0) as client:
        # 1. 注册设备
        device_id, real_uid_actual = register_device(client, jwt, real_uid)
        if real_uid_actual and real_uid_actual != "0":
            real_uid = real_uid_actual
        # uuid_val 在 API call 时是 real_uid 本身
        uuid_val = real_uid

        # 2. 发送消息
        chat_id, msg_id = send_msg(client, jwt, real_uid, device_id, uuid_val, text)
        if not chat_id:
            log.error("没拿到 chat_id, 中断")
            log.info("完整响应: %s", json.dumps(d if 'd' in dir() else {}, ensure_ascii=False, default=str)[:500])
            return 1

        # 3. 轮询响应
        ai_msg = poll_response(client, jwt, real_uid, device_id, uuid_val, chat_id)

    # 4. 打印结果
    print("\n" + "=" * 60)
    print("AI 回复:")
    print("=" * 60)
    print(ai_msg.get("msg_content", "(空)"))
    print("=" * 60)
    thinking = ai_msg.get("extra_info", {}).get("thinking_content")
    if thinking:
        print("\n[思考过程]:")
        print(thinking)
    print()
    print(f"  chat_id:  {chat_id}")
    print(f"  msg_id:   {ai_msg.get('msg_id', msg_id)}")
    print(f"  model:    {ai_msg.get('extra_info', {}).get('model', '?')}")
    usage = ai_msg.get("usage", {})
    if usage:
        print(f"  tokens:   {usage}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
