"""auto_login.py — 自动登录 agent.minimaxi.com 并把 JWT 写回 config.json。

真相（curl 验证过）：
1. 登录端点是 POST /v1/api/user/login/phone
2. JS 里有两套 login：SMS 验证码（LOGINTYPEPHONE=""），和"密码"（LOGINTYPEPASSWORD="20"）
3. 但服务端 **不实现** loginType=20，不管传什么字段都返回 1200019「服务端开小差了」
4. loginType=""（SMS）走通逻辑但 baobao615 必被当作 SMS code，返回 1200009「验证码有误」

也就是说，**纯密码登录走不通**。这是 MiniMax 服务端的真实行为，不是签名/字段问题。

现实方案：
  A) 让用户用手机/邮箱收短信验证码 → 脚本自动完成（需要先解 geetest/腾讯滑块 CAPTCHA）
  B) 直接拿现有 JWT 续期 → /v1/api/user/renewal 端点存在但 POST 无 body 时返回 400
  C) 用户在浏览器登录一次 → 脚本引导从 localStorage._token 导出，写回 config.json

本脚本实现的是 **A + C 的合并版**：
  - 默认先尝试 SMS 验证码登录（不接 captcha，需要人工输入）
  - 失败时打印如何在浏览器导出 _token 的步骤

依赖：pip install httpx
"""

from __future__ import annotations

import base64
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import quote

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
log = logging.getLogger("auto_login")

# ╔══════════════════════════════════════════════════════════════════╗
# ║ 配置                                                              ║
# ╚══════════════════════════════════════════════════════════════════╝

# 下面 phone/password 是用户原话写死的。脚本启动后会先用现有 JWT 反查
# 真正的账号手机号并打印，用户可对照修正后再跑。
PHONE = "19065353709"          # 用户口述的手机号
COUNTRY_CODE = "+86"
PASSWORD = "baobao615"         # 用户口述的密码（已确认服务端不接受 password login）

LOGIN_PATH = "/v1/api/user/login/phone"
SMS_SEND_PATH = "/v1/api/user/login/sms/send"
INFO_PATH = "/v1/api/user/info"
CONFIG_PATH = Path(__file__).parent / "config.json"

EXPIRY_REFRESH_THRESHOLD = 6 * 3600


# ── 通用签名 / 设备指纹 ─────────────────────────────────────────────

def _build_qs(uid: str, did: str, jwt: str = "", real_uid: str = "0") -> str:
    d = dict(FAKE_USER_DATA)
    d["uuid"] = uid
    d["device_id"] = did
    d["unix"] = _unix_ms()
    d["user_id"] = real_uid
    if jwt:
        d["token"] = jwt
    return "&".join(f"{k}={v}" for k, v in d.items() if v is not None)


def _compute_yy(full_uri: str, body: str) -> str:
    encoded = quote(full_uri, safe="")
    return _md5(f"{encoded}_{body}{_md5(_unix_ms())}ooui")


def _compute_xsig(timestamp: int, jwt: str, body: str) -> str:
    return _md5(f"{timestamp}{jwt}{body}")


def _decode_jwt_exp(token: str) -> Optional[int]:
    try:
        parts = token.split(".")
        b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.b64decode(b64).decode("utf-8"))
        return int(payload.get("exp")) if "exp" in payload else None
    except Exception:
        return None


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        return json.loads(base64.b64decode(b64).decode("utf-8"))
    except Exception:
        return {}


# ── 反查账号真实手机号（用现有 JWT） ─────────────────────────────────

def fetch_real_account(jwt: str) -> dict:
    """用现有 token 调 /v1/api/user/info，拿到真实 phone/userID/name。"""
    pl = _decode_jwt_payload(jwt)
    real_uid = str(pl.get("user", {}).get("id", "0"))

    qs = _build_qs(_uuid(), _uuid(), jwt, real_uid)
    body = ""
    uri = f"{INFO_PATH}?{qs}"
    sig = _compute_xsig(_unix(), jwt, body)
    yy = _compute_yy(uri, body)

    headers = {
        **FAKE_HEADERS,
        "Referer": "https://agent.minimaxi.com/",
        "token": jwt,
        "x-timestamp": str(_unix()),
        "x-signature": sig,
        "yy": yy,
    }
    r = httpx.get(AGENT_BASE_URL + uri, headers=headers, timeout=15.0)
    return _safe_json(r)


def _safe_json(r: httpx.Response) -> dict:
    """Decode response, handling brotli/gzip (httpx 不自动解 brotli)."""
    content = r.content
    enc = r.headers.get("content-encoding", "").lower()
    if "br" in enc:
        try:
            import brotli
            content = brotli.decompress(content)
        except Exception:
            pass
    elif "gzip" in enc:
        import gzip
        content = gzip.decompress(content)
    try:
        return json.loads(content.decode("utf-8", errors="replace"))
    except Exception:
        return {}


# ── 登录端点（已知服务端只接 PHONE/SMS） ──────────────────────────────

def send_sms(phone: str) -> dict:
    """POST /v1/api/user/login/sms/send。注意：正式登录页面要先解腾讯滑块 captcha。"""
    body_dict = {"phone": phone, "countryCode": COUNTRY_CODE}
    body_json = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)
    qs = _build_qs(_uuid(), _uuid())
    uri = f"{SMS_SEND_PATH}?{qs}"
    yy = _compute_yy(uri, body_json)
    sig = _compute_xsig(_unix(), "", body_json)

    headers = {
        **FAKE_HEADERS,
        "Content-Type": "application/json",
        "Referer": "https://agent.minimaxi.com/login",
        "x-timestamp": str(_unix()),
        "x-signature": sig,
        "yy": yy,
    }
    r = httpx.post(AGENT_BASE_URL + uri, content=body_json, headers=headers, timeout=15.0)
    d = _safe_json(r)
    info = d.get("statusInfo", {})
    log.info("SMS 发送结果: HTTP %d code=%s msg=%s",
             r.status_code, info.get("code"), info.get("message"))
    if info.get("code") not in (0, None):
        log.warning("SMS 没发出去。可能是要 captcha：先在浏览器里完成滑块，"
                    "看 Network 抓包里 sms/send 请求带的 ticket / randStr "
                    "再粘给我加进 headers")
    return d


def login_with_sms_code(phone: str, code: str) -> dict:
    """POST /v1/api/user/login/phone，loginType=空（PHONE），code=短信验证码。"""
    body_dict = {
        "phone": phone,
        "code": str(code),
        "countryCode": COUNTRY_CODE,
        "loginType": "",
    }
    body_json = json.dumps(body_dict, separators=(",", ":"), ensure_ascii=False)
    qs = _build_qs(_uuid(), _uuid())
    uri = f"{LOGIN_PATH}?{qs}"
    yy = _compute_yy(uri, body_json)
    sig = _compute_xsig(_unix(), "", body_json)

    headers = {
        **FAKE_HEADERS,
        "Content-Type": "application/json",
        "Referer": "https://agent.minimaxi.com/login",
        "x-timestamp": str(_unix()),
        "x-signature": sig,
        "yy": yy,
    }
    r = httpx.post(AGENT_BASE_URL + uri, content=body_json, headers=headers, timeout=15.0)
    d = _safe_json(r)
    info = d.get("statusInfo", {})
    log.info("SMS 登录结果: HTTP %d code=%s msg=%s",
             r.status_code, info.get("code"), info.get("message"))
    return d


# ── 写回 config.json ───────────────────────────────────────────────

def get_current_token() -> Optional[str]:
    if not CONFIG_PATH.exists():
        return None
    cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
    for a in cfg.get("accounts", []):
        if a.get("auth_mode") == "token" and a.get("base_url", "").startswith("https://agent.minimaxi.com"):
            return a.get("auth_token") or a.get("api_key")
    return None


def save_token(jwt: str) -> None:
    if not CONFIG_PATH.exists():
        log.error("找不到 %s，请先跑一次 python main.py 初始化", CONFIG_PATH)
        sys.exit(2)
    cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
    accounts = cfg.setdefault("accounts", [])

    target = None
    for a in accounts:
        if a.get("auth_mode") == "token" and a.get("base_url", "").startswith("https://agent.minimaxi.com"):
            target = a
            break
    if target is None and accounts:
        target = accounts[0]
    if target is None:
        target = {
            "name": PHONE,
            "base_url": "https://agent.minimaxi.com/v1",
            "auth_mode": "token",
            "is_active": True,
            "request_count": 0,
        }
        accounts.append(target)

    target["api_key"] = jwt
    target["auth_token"] = jwt
    target["auth_mode"] = "token"
    target["base_url"] = "https://agent.minimaxi.com/v1"
    target.setdefault("is_active", True)
    target["request_count"] = 0

    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    log.info("✓ 已写回 %s", CONFIG_PATH.name)


# ── 入口 ───────────────────────────────────────────────────────────

def main() -> int:
    cur = get_current_token()
    if cur:
        exp = _decode_jwt_exp(cur)
        remain = exp - int(time.time()) if exp else None
        log.info("当前 token 剩余 %s",
                 f"{remain // 3600}h{(remain % 3600) // 60}m (exp={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))})" if remain else "(无法解析 exp)")

        # 反查真实账号
        log.info("反查账号真实信息…")
        acc = fetch_real_account(cur)
        if isinstance(acc, dict) and acc.get("statusInfo", {}).get("code") == 0:
            ui = (acc.get("data") or {}).get("userInfo") or {}
            log.info("  账号姓名: %s", ui.get("name"))
            log.info("  真实手机号: %s（你给的 %s）", ui.get("phone"), PHONE)
            if ui.get("phone") and ui.get("phone") != PHONE:
                log.warning("  ⚠ 真实手机号 ≠ 你给我的号。改脚本顶部 PHONE 后再跑。")
        else:
            log.warning("  反查失败：%s", json.dumps(acc, ensure_ascii=False)[:200] if isinstance(acc, dict) else acc)

    log.info("")
    log.info("=" * 60)
    log.info("已知约束：")
    log.info("  1. 服务端 /v1/api/user/login/phone 不支持 loginType=20 (PASSWORD)")
    log.info("     任何密码都会返回 1200019 '服务端开小差了'")
    log.info("  2. SMS 登录走 loginType=PHONE(=空)，但发码前要先解腾讯滑块")
    log.info("  3. 自动续期 /v1/api/user/renewal POST 无 body 返回 400")
    log.info("=" * 60)
    log.info("")

    # 让用户选择路径
    print("请选择登录方式：")
    print("  [1] 用手机/邮箱收 SMS 验证码（需要你手动收短信 + 输码）")
    print("  [2] 走浏览器登录后导出 _token（最稳妥）")
    print("  [3] 退出（保留当前 token）")
    choice = input("输入 1/2/3（默认 2）: ").strip() or "2"

    if choice == "3":
        log.info("保留当前 token。")
        return 0

    if choice == "1":
        phone = input(f"要发短信的手机号（默认 {PHONE}）: ").strip() or PHONE
        log.info("1) 发送短信验证码到 %s …", phone)
        r = send_sms(phone)
        info = r.get("statusInfo", {})
        if info.get("code") not in (0, None):
            log.error("发送失败。可能要先在浏览器里完成滑块 CAPTCHA。")
            log.error("建议改走方式 2。")
            return 1
        code = input("输入收到的 6 位验证码: ").strip()
        if not code:
            log.error("验证码不能为空")
            return 1
        d = login_with_sms_code(phone, code)
        info = d.get("statusInfo", {})
        if info.get("code") != 0:
            log.error("登录失败：%s", info.get("message"))
            return 1
        token = (d.get("data") or {}).get("token") or (d.get("data") or {}).get("data", {}).get("token")
        if not token:
            log.error("未拿到 token: %s", json.dumps(d, ensure_ascii=False)[:300])
            return 1

    else:  # choice == "2"
        log.info("=" * 60)
        log.info("方式 2：浏览器登录后导出 _token")
        log.info("=" * 60)
        log.info("步骤：")
        log.info("  1. 浏览器打开 https://agent.minimaxi.com/ ，扫码/SMS/微信登录")
        log.info("  2. F12 → Console 粘贴下面这行，回车：")
        log.info("")
        log.info("     copy(localStorage.getItem('_token'))")
        log.info("")
        log.info("  3. 回到这里，把 token 粘到下面")
        log.info("=" * 60)
        token = input("粘贴 JWT（按 Ctrl+V，回车确认）: ").strip().strip('"').strip("'")
        if not token or token.count(".") != 2:
            log.error("看起来不像 JWT（需要两个点）")
            return 1

    # 写回
    exp = _decode_jwt_exp(token)
    if exp:
        log.info("新 token 剩余 %dh%dm",
                 (exp - int(time.time())) // 3600,
                 ((exp - int(time.time())) % 3600) // 60)
    save_token(token)
    log.info("完成。可以 python main.py 启动代理。")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        log.info("已取消。")
        sys.exit(130)
