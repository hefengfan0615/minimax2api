"""auto_login.py — 自动续期 agent.minimaxi.com 的 JWT 并写回 config.json。

═══════════════════════════════════════════════════════════════════════
【真相 (curl 验证过)】
═══════════════════════════════════════════════════════════════════════
1. 登录端点 POST /v1/api/user/login/phone 服务端只实现两种 loginType:
   • "" (PHONE)       → SMS 验证码登录
   • "5" (THIRDPART)  → 微信/GitHub/Google 登录
   • "20" (PASSWORD)  → 客户端有枚举，服务端返回 1200019「服务端开小差了」
                       **密码登录在当前版本服务端没实现**
2. 但有现成 JWT 时,  POST /v1/api/user/renewal 100% 返回新 JWT, 续期 30 天。
   旧的 22 天 → 新的 39 天, 是 rolling expiration。
3. 用户给的 19065353709 跟现有 JWT 的账号手机号 13613849743(白蓝, 微信绑定) 对不上,
   所以即使用 baobao615 试密码, 也走不通 — 服务端都不接 PASSWORD loginType。

═══════════════════════════════════════════════════════════════════════
【解决方案】
═══════════════════════════════════════════════════════════════════════
- 默认行为: 用 config.json 里现有 JWT 调 /v1/api/user/renewal 续期, 写回 config.json。
  零交互, 可放进 cron 每天跑一次, 理论上 token 永远不过期。
- 现有 JWT 失效 / 不存在: 引导用户在浏览器登录, 从 localStorage._token 粘进来。
- 不接腾讯滑块 CAPTCHA, 也不发短信 — 因为前者太复杂, 后者需要真手机。

用法:
    python auto_login.py            # 续期
    python auto_login.py --check    # 只看 token 状态
    python auto_login.py --set      # 手动设新 token
    python auto_login.py --set 19065353709 baobao615  # 跑 password 登录(基本会失败)
    python auto_login.py --force    # 即使没到期也续

依赖: pip install httpx brotli
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
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

RENEWAL_PATH = "/v1/api/user/renewal"
INFO_PATH = "/v1/api/user/info"
LOGIN_PATH = "/v1/api/user/login/phone"
SMS_SEND_PATH = "/v1/api/user/login/sms/send"
CONFIG_PATH = Path(__file__).parent / "config.json"

# 用户口述的账号 — 跑 password 登录会失败, 但保留给将来万一服务端支持
USER_PHONE = "19065353709"
USER_PASSWORD = "baobao615"
COUNTRY_CODE = "+86"

# 续期阈值: token 剩余不到这个秒数就续
RENEW_THRESHOLD = 6 * 3600  # 6 小时


# ── 签名 / 设备指纹 ────────────────────────────────────────────────

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
    return _md5(f"{quote(full_uri, safe='')}_{body}{_md5(_unix_ms())}ooui")


def _compute_xsig(timestamp: int, jwt: str, body: str) -> str:
    return _md5(f"{timestamp}{jwt}{body}")


def _decode(content: bytes, encoding: str) -> dict:
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


def _jwt_payload(token: str) -> dict:
    try:
        b64 = token.split(".")[1] + "=" * (-len(token.split(".")[1]) % 4)
        return json.loads(base64.b64decode(b64).decode("utf-8"))
    except Exception:
        return {}


def _jwt_exp(token: str) -> Optional[int]:
    return _jwt_payload(token).get("exp")


def _jwt_user_id(token: str) -> str:
    return str(_jwt_payload(token).get("user", {}).get("id", "0"))


# ── 核心: 续期 ─────────────────────────────────────────────────────

def renew_token(old_jwt: str) -> dict:
    """POST /v1/api/user/renewal → 新 JWT。返回 {"data":{...}, "statusInfo":{...}}"""
    real_uid = _jwt_user_id(old_jwt)
    qs = _build_qs(_uuid(), _uuid(), old_jwt, real_uid)
    body = ""
    uri = f"{RENEWAL_PATH}?{qs}"
    yy = _compute_yy(uri, body)
    sig = _compute_xsig(_unix(), old_jwt, body)

    headers = {
        **FAKE_HEADERS,
        "Content-Type": "application/json",
        "Referer": "https://agent.minimaxi.com/",
        "token": old_jwt,
        "x-timestamp": str(_unix()),
        "x-signature": sig,
        "yy": yy,
    }
    r = httpx.post(AGENT_BASE_URL + uri, content=None, headers=headers, timeout=15.0)
    d = _decode(r.content, r.headers.get("content-encoding", ""))
    return {"_status": r.status_code, **d}


# ── 反查账号信息 (验证 token 还活着) ───────────────────────────────

def fetch_account(jwt: str) -> dict:
    real_uid = _jwt_user_id(jwt)
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
    d = _decode(r.content, r.headers.get("content-encoding", ""))
    ui = (d.get("data") or {}).get("userInfo") or {}
    return {
        "name": ui.get("name"),
        "phone": ui.get("phone"),
        "userID": ui.get("userID"),
        "status": d.get("statusInfo", {}).get("code"),
    }


# ── 已知不通的 password 登录 (保留) ─────────────────────────────────

def try_password_login(phone: str, password: str) -> dict:
    """尝试 password 登录。**服务端不实现, 永远返回 1200019**。"""
    body_dict = {
        "phone": phone,
        "code": password,
        "countryCode": COUNTRY_CODE,
        "loginType": "20",  # 客户端 PASSWORD 枚举, 服务端 1200019
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
    return {"_status": r.status_code, **_decode(r.content, r.headers.get("content-encoding", ""))}


# ── config.json 读写 ───────────────────────────────────────────────

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
        log.error("找不到 %s，请先跑 python main.py 初始化", CONFIG_PATH)
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
        target = {"name": USER_PHONE, "base_url": "https://agent.minimaxi.com/v1",
                  "auth_mode": "token", "is_active": True, "request_count": 0}
        accounts.append(target)

    target["api_key"] = jwt
    target["auth_token"] = jwt
    target["auth_mode"] = "token"
    target["base_url"] = "https://agent.minimaxi.com/v1"
    target.setdefault("is_active", True)
    target["request_count"] = 0

    CONFIG_PATH.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
    log.info("✓ 已写回 %s", CONFIG_PATH.name)


def describe_token(token: Optional[str]) -> str:
    if not token:
        return "(无)"
    exp = _jwt_exp(token)
    if not exp:
        return f"{token[:24]}... (无法解析 exp)"
    remain = exp - int(time.time())
    if remain <= 0:
        return f"已过期 ({-remain}s 前)"
    return (f"{token[:24]}...  剩 {remain // 86400}d{remain % 86400 // 3600}h"
            f" (exp={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))})")


# ── 入口 ───────────────────────────────────────────────────────────

def main() -> int:
    p = argparse.ArgumentParser(description="自动续期 agent.minimaxi.com JWT")
    p.add_argument("--check", action="store_true", help="只检查 token 状态")
    p.add_argument("--force", action="store_true", help="即使没到期也续")
    p.add_argument("--set", nargs="*", metavar="TOKEN", help="手动设新 token (从 localStorage._token 复制)")
    p.add_argument("--password", action="store_true", help="用 19065353709/baobao615 试密码登录 (基本会失败)")
    args = p.parse_args()

    # 检查模式
    if args.check:
        cur = get_current_token()
        print(f"当前 token: {describe_token(cur)}")
        return 0

    # 手动设 token
    if args.set is not None:
        if not args.set:
            log.info("请粘贴 JWT (Ctrl+V 粘贴, 回车确认):")
            token = input().strip().strip('"').strip("'")
        else:
            token = args.set[0]
        if not token or token.count(".") != 2:
            log.error("看起来不像 JWT (需要两个点)")
            return 1
        save_token(token)
        log.info("新 token: %s", describe_token(token))
        return 0

    # 试密码登录 (基本失败, 给个明确的反馈)
    if args.password:
        log.info("试密码登录: %s / ***%s", USER_PHONE, USER_PASSWORD[-2:])
        d = try_password_login(USER_PHONE, USER_PASSWORD)
        info = d.get("statusInfo", {})
        log.info("HTTP %d  code=%s  msg=%s",
                 d.get("_status"), info.get("code"), info.get("message"))
        log.info("(预期会失败: 服务端不实现 loginType=20 PASSWORD)")
        return 1

    # 主流程: 续期
    cur = get_current_token()
    log.info("当前 token: %s", describe_token(cur))

    if not cur:
        log.warning("config.json 里没 token, 无法续期。")
        log.info("请用浏览器打开 https://agent.minimaxi.com/ 微信扫码登录后, 在")
        log.info("控制台跑:  copy(localStorage.getItem('_token'))")
        log.info("然后:  python auto_login.py --set <粘贴的 token>")
        return 1

    exp = _jwt_exp(cur)
    remain = exp - int(time.time()) if exp else None
    if remain is not None and remain > RENEW_THRESHOLD and not args.force:
        log.info("Token 还剩 %d 天 %d 小时, 不需要续 (--force 可强续)",
                 remain // 86400, (remain % 86400) // 3600)
        return 0

    log.info("调 /v1/api/user/renewal 续期…")
    d = renew_token(cur)
    info = d.get("statusInfo", {})
    new_token = (d.get("data") or {}).get("token")

    if info.get("code") != 0 or not new_token:
        log.error("续期失败: HTTP %s  code=%s  msg=%s",
                  d.get("_status"), info.get("code"), info.get("message"))
        log.info("可能是 token 已失效, 需重新: python auto_login.py --set <新 token>")
        return 1

    # 验证新 token
    acc = fetch_account(new_token)
    if acc.get("status") == 0:
        log.info("✓ 续期成功, 账号: %s (phone=%s, userID=%s)",
                 acc.get("name"), acc.get("phone"), acc.get("userID"))
    else:
        log.warning("续期返回 200 但反查账号失败, 仍写回")
        log.info("  acc = %s", json.dumps(acc, ensure_ascii=False))

    save_token(new_token)
    log.info("新 token: %s", describe_token(new_token))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        log.info("已取消。")
        sys.exit(130)
