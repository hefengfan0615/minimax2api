"""auto_login.py — 自动登录 agent.minimaxi.com 并把 JWT 写回 config.json。

使用方法：
    python auto_login.py                # 必要时登录并更新 config.json
    python auto_login.py --check        # 只检查当前 token 是否过期
    python auto_login.py --force        # 强制重新登录
    python auto_login.py --phone 138... --password xxx  # 临时换号

依赖：pip install httpx
说明：本脚本是基于已掌握信息（MiniMax-Free-API / Chat2API 通用模式）写的
试探版。如果登录失败，脚本会打印最近一次的请求/响应细节，请在
DevTools → Network 中输入账号密码登录，把真实端点/Headers/Payload 抓出来，
对照日志里打印的内容进行修正（见文件末尾的“可调参数”区）。
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import re
import sys
import time
from pathlib import Path
from typing import Any, Optional

try:
    import httpx
except ImportError:
    print("缺少 httpx，请先运行: pip install httpx", file=sys.stderr)
    sys.exit(1)


# ╔══════════════════════════════════════════════════════════════════╗
# ║ 可调参数 — 如果登录失败，先改这里再跑                              ║
# ╚══════════════════════════════════════════════════════════════════╝

LOGIN_BASE = "https://agent.minimaxi.com"     # 网站主域
PHONE = "19065353709"
PASSWORD = "baobao615"
COUNTRY_CODE = "86"

# 候选登录端点（按顺序尝试，直到拿到 JWT 为止）
LOGIN_ENDPOINTS = [
    "/api/user/login",
    "/api/auth/login",
    "/v1/api/user/login",
    "/matrix/api/v1/login",
    "/api/v1/login",
]

# 候选请求体 — 同一个端点会依次试这些 body
# 字段含 md5: 后缀的会自动用 md5(原值) 替换
PASSWORD_VARIANTS = [
    {"phone": PHONE, "country_code": COUNTRY_CODE, "password": PASSWORD},
    {"phone": PHONE, "country_code": COUNTRY_CODE, "password_md5": "md5:" + PASSWORD},
    {"phone": PHONE, "password": PASSWORD},
    {"phone": PHONE, "password_md5": "md5:" + PASSWORD},
]

# 通用 Headers（User-Agent 模仿 Chrome on macOS，避免被基础反爬拦）
COMMON_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Origin": LOGIN_BASE,
    "Referer": LOGIN_BASE + "/",
}

# Token 在响应 JSON 里可能的字段路径（按优先级）
TOKEN_PATHS = [
    ("data", "token"),
    ("data", "access_token"),
    ("data", "_token"),
    ("data", "data", "token"),
    ("token",),
    ("access_token",),
    ("_token",),
]

# 低于这个秒数视为“即将过期”，会触发重新登录
EXPIRY_REFRESH_THRESHOLD = 6 * 3600  # 6 小时

# ════════════════════════════════════════════════════════════════════

CONFIG_PATH = Path(__file__).parent / "config.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("auto_login")


# ── 小工具 ─────────────────────────────────────────────────────────

def md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()


def decode_jwt_exp(token: str) -> Optional[int]:
    """从 JWT 里读 exp 字段（秒），解析失败返回 None。"""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.b64decode(b64).decode("utf-8"))
        return int(payload.get("exp")) if "exp" in payload else None
    except Exception:
        return None


def looks_like_jwt(s: Any) -> bool:
    return isinstance(s, str) and s.count(".") == 2 and len(s) > 50


def extract_token_from_json(data: Any) -> Optional[str]:
    """按 TOKEN_PATHS 取 token；取不到就递归扫整棵树。"""
    if isinstance(data, dict):
        for path in TOKEN_PATHS:
            cur: Any = data
            ok = True
            for k in path:
                if isinstance(cur, dict) and k in cur:
                    cur = cur[k]
                else:
                    ok = False
                    break
            if ok and looks_like_jwt(cur):
                return cur

    def _scan(obj: Any) -> Optional[str]:
        if looks_like_jwt(obj):
            return obj
        if isinstance(obj, dict):
            for v in obj.values():
                r = _scan(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = _scan(v)
                if r:
                    return r
        return None

    return _scan(data)


def extract_token_from_response(resp: httpx.Response) -> Optional[str]:
    """先看 Set-Cookie，再看 JSON body。"""
    for name, value in resp.cookies.items():
        if "token" in name.lower() and looks_like_jwt(value):
            log.info("从 Set-Cookie 提取到 token (cookie=%s)", name)
            return value
    try:
        return extract_token_from_json(resp.json())
    except Exception:
        return None


# ── 读取 CSRF / 预热 Cookie ────────────────────────────────────────

CSRF_PATTERNS = [
    re.compile(r'<meta\s+name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'name=["\']csrf-token["\']\s+content=["\']([^"\']+)["\']', re.I),
    re.compile(r'name=["\']csrf_token["\']\s+value=["\']([^"\']+)["\']', re.I),
    re.compile(r'window\.__CSRF__\s*=\s*["\']([^"\']+)["\']'),
    re.compile(r'window\._csrf\s*=\s*["\']([^"\']+)["\']'),
]


def warm_up(client: httpx.Client) -> str:
    """访问首页，把基础 Cookie 收齐；顺便抓 csrf token。"""
    log.info("[1/3] 预热：访问 %s/", LOGIN_BASE)
    resp = client.get(LOGIN_BASE + "/", timeout=15.0)
    resp.raise_for_status()
    csrf = ""
    for pat in CSRF_PATTERNS:
        m = pat.search(resp.text)
        if m:
            csrf = m.group(1)
            log.info("  抓到 csrf: %s...", csrf[:16])
            break
    if not csrf:
        log.info("  页面里没找到 csrf 标记")
    log.info("  已收集 cookie: %s", list(client.cookies.keys()))
    return csrf


# ── 登录主流程 ──────────────────────────────────────────────────────

def _materialize(payload: dict) -> dict:
    """处理 'md5:xxx' 标记 → 真实 md5 值。"""
    out = {}
    for k, v in payload.items():
        if isinstance(v, str) and v.startswith("md5:"):
            out[k] = md5(v[4:])
        else:
            out[k] = v
    return out


def try_login(client: httpx.Client, csrf: str) -> Optional[str]:
    log.info("[2/3] 开始探测登录端点，共 %d 个端点 × %d 种 body",
             len(LOGIN_ENDPOINTS), len(PASSWORD_VARIANTS))

    headers = dict(COMMON_HEADERS)
    if csrf:
        headers["x-csrf-token"] = csrf

    for endpoint in LOGIN_ENDPOINTS:
        url = LOGIN_BASE + endpoint
        for body in PASSWORD_VARIANTS:
            real_body = _materialize(body)
            body_log = {k: (v[:8] + "...") if k.startswith("password") and isinstance(v, str) and len(v) > 12 else v
                        for k, v in real_body.items()}
            log.info("  → POST %s  body=%s", endpoint, body_log)
            try:
                resp = client.post(url, json=real_body, headers=headers, timeout=15.0)
            except httpx.HTTPError as e:
                log.warning("     网络异常: %s", e)
                continue

            log.info("     HTTP %d", resp.status_code)

            # 302/3xx 通常会带 Set-Cookie 凭证
            if 300 <= resp.status_code < 400:
                token = extract_token_from_response(resp)
                if token:
                    log.info("  ✓ 拿到 token（重定向 + Set-Cookie）")
                    return token

            if resp.status_code != 200:
                continue

            token = extract_token_from_response(resp)
            if token:
                log.info("  ✓ 拿到 token")
                return token

            # 看看响应 JSON 是不是错误码
            try:
                data = resp.json()
                code = data.get("code") or data.get("statusInfo", {}).get("code")
                msg = (data.get("msg")
                       or data.get("message")
                       or data.get("statusInfo", {}).get("message")
                       or "")
                log.info("     业务码=%s msg=%s", code, msg)
            except Exception:
                log.info("     非 JSON 响应: %.150s", resp.text)

    return None


# ── 写回 config.json ───────────────────────────────────────────────

def save_token_to_config(jwt: str) -> None:
    log.info("[3/3] 写回 %s", CONFIG_PATH.name)
    if not CONFIG_PATH.exists():
        log.error("找不到 %s，请先初始化项目（python main.py 启动过一次）", CONFIG_PATH)
        sys.exit(2)

    cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
    accounts = cfg.setdefault("accounts", [])

    # 找一个匹配 phone 的 web-token 账号，否则用第一个，否则新建
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
    target.setdefault("name", PHONE)
    target.setdefault("is_active", True)
    target["request_count"] = 0  # 重置计数，激活账号

    cfg["accounts"] = accounts
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    log.info("  ✓ 已更新账号: name=%s, base_url=%s",
             target.get("name"), target.get("base_url"))


# ── 检查现有 token ─────────────────────────────────────────────────

def get_current_token() -> Optional[str]:
    if not CONFIG_PATH.exists():
        return None
    cfg = json.loads(CONFIG_PATH.read_text("utf-8"))
    for a in cfg.get("accounts", []):
        if a.get("auth_mode") == "token" and a.get("base_url", "").startswith("https://agent.minimaxi.com"):
            return a.get("auth_token") or a.get("api_key")
    return None


def describe_token(token: Optional[str]) -> str:
    if not token:
        return "(无)"
    exp = decode_jwt_exp(token)
    if not exp:
        return f"{token[:24]}... (无法解析 exp)"
    remain = exp - int(time.time())
    if remain <= 0:
        return f"已过期 {-remain}s 前"
    return (f"{token[:24]}...  剩余 {remain // 3600}h{(remain % 3600) // 60}m  "
            f"(exp={time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(exp))})")


# ── 入口 ───────────────────────────────────────────────────────────

def main() -> int:
    global PHONE, PASSWORD  # 允许 argparse 覆盖

    p = argparse.ArgumentParser(description="自动登录 agent.minimaxi.com")
    p.add_argument("--check", action="store_true", help="只检查当前 token 是否过期")
    p.add_argument("--force", action="store_true", help="强制重新登录")
    p.add_argument("--phone", default=PHONE)
    p.add_argument("--password", default=PASSWORD)
    args = p.parse_args()

    PHONE, PASSWORD = args.phone, args.password

    current = get_current_token()
    log.info("当前 token: %s", describe_token(current))

    if args.check:
        exp = decode_jwt_exp(current)
        if exp and exp - int(time.time()) < EXPIRY_REFRESH_THRESHOLD:
            log.warning("即将过期，建议重新登录")
            return 1
        return 0

    exp = decode_jwt_exp(current) if current else None
    if current and exp and exp - int(time.time()) > EXPIRY_REFRESH_THRESHOLD and not args.force:
        log.info("Token 仍在有效期内（剩余 %ds），无需登录。--force 可强制刷新。",
                 exp - int(time.time()))
        return 0

    with httpx.Client(
        headers={k: v for k, v in COMMON_HEADERS.items() if k != "Content-Type"},
        follow_redirects=True,
        timeout=15.0,
    ) as client:
        try:
            csrf = warm_up(client)
        except Exception as e:
            log.error("预热失败: %s", e)
            return 3

        token = try_login(client, csrf)
        if not token:
            log.error("=" * 60)
            log.error("所有端点都未拿到 token。可能原因：")
            log.error("  1. 端点不在 LOGIN_ENDPOINTS 列表里 → 在脚本顶部加新端点")
            log.error("  2. 密码需要别的处理（RSA / 加盐 / 签名）→ 调整 PASSWORD_VARIANTS")
            log.error("  3. 需要图形验证码 / 短信验证码 → 当前脚本不支持，请手动登录一次")
            log.error("  4. 被风控拦截 → 换 IP / 降速 / 换 UA")
            log.error("操作建议：打开 DevTools → Network，输入账号密码登录，")
            log.error("把核心 POST 请求的 URL / Headers / Payload 抓出来贴给我。")
            log.error("=" * 60)
            return 4

        exp = decode_jwt_exp(token)
        if exp:
            log.info("登录成功！Token 过期时间: %s (剩余 %ds)",
                     time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(exp)),
                     exp - int(time.time()))
        else:
            log.info("登录成功！Token: %s...", token[:32])

        save_token_to_config(token)
        log.info("完成。可以启动代理: python main.py")
        return 0


if __name__ == "__main__":
    sys.exit(main())
