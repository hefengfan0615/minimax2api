"""
MiniMax All-in-One - 整合了所有官网MiniMax功能的单一Python文件

功能包括：
- MiniMax Web Agent API 适配器
- 配置管理（支持多账号）
- OpenAI兼容的API代理
- 完整的FastAPI应用
- 使用统计和账号管理
"""

import hashlib
import json
import logging
import os
import threading
import time
import uuid as uuid_mod
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator, Dict, List, Optional, Union, Any

import httpx
import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, ConfigDict, model_validator

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("minimax_all_in_one")

# ==================== 常量 ====================
AGENT_BASE_URL = "https://agent.minimaxi.com"
MINIMAX_MAIN_URL = "https://www.minimaxi.com"
MINIMAX_API_URL = "https://api.minimaxi.com"
FAKE_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Cache-Control": "no-cache",
    "Origin": "https://agent.minimaxi.com",
    "Pragma": "no-cache",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/142.0.0.0 Safari/537.36"
    ),
}
FAKE_USER_DATA = {
    "device_platform": "web",
    "biz_id": "3",
    "app_id": "3001",
    "version_code": "22201",
    "os_name": "Mac",
    "browser_name": "chrome",
    "device_memory": 8,
    "cpu_core_num": 11,
    "browser_language": "zh-CN",
    "browser_platform": "MacIntel",
    "screen_width": 1920,
    "screen_height": 1080,
    "lang": "zh",
    "timezone_offset": 28800,
    "sys_language": "zh",
    "client": "web",
}
DEVICE_INFO_TTL = 10800  # seconds

# ==================== 模型名称映射 ====================
MODEL_MAP: Dict[str, str] = {
    "gpt-4o": "MiniMax-M2.7",
    "gpt-4o-2024-08-06": "MiniMax-M2.7",
    "gpt-4o-mini": "MiniMax-M2.5-highspeed",
    "gpt-4-turbo": "MiniMax-M2.7",
    "gpt-4": "MiniMax-M2.7",
    "gpt-3.5-turbo": "MiniMax-M2.5-highspeed",
    "claude-sonnet-4-20250514": "MiniMax-M2.7",
    "claude-sonnet-4": "MiniMax-M2.7",
    "claude-3.5-sonnet": "MiniMax-M2.7",
    "claude-3-haiku": "MiniMax-M2.5-highspeed",
    "gemini-2.0-flash": "MiniMax-M2.7-highspeed",
    "gemini-1.5-pro": "MiniMax-M2.7",
    "MiniMax-M2.7": "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed": "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5": "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed": "MiniMax-M2.5-highspeed",
}

DEFAULT_MODELS = [
    "MiniMax-M2.7", "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5", "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1", "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
]

# ==================== 数据类定义 ====================
@dataclass
class Account:
    api_key: str = ""
    name: str = ""
    base_url: str = "https://api.minimax.io/v1"
    auth_mode: str = "api_key"
    auth_token: str = ""
    is_active: bool = True
    request_count: int = 0
    last_used: float = 0.0
    cooldown_until: float = 0.0

    @property
    def on_cooldown(self) -> bool:
        return self.cooldown_until > time.time()

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "name": self.name,
            "base_url": self.base_url,
            "auth_mode": self.auth_mode,
            "auth_token": self.auth_token,
            "is_active": self.is_active,
            "request_count": self.request_count,
        }

@dataclass
class UsageStats:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict:
        return {
            "requests": self.requests,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }

@dataclass
class Config:
    minimax_api_key: str = ""
    minimax_base_url: str = "https://api.minimax.io/v1"
    proxy_api_keys: List[str] = field(default_factory=lambda: ["sk-default"])
    default_model: str = "MiniMax-M2.7"
    available_models: List[str] = field(default_factory=lambda: DEFAULT_MODELS.copy())
    webui_password: str = "minimax"
    accounts: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "minimax_api_key": self.minimax_api_key,
            "minimax_base_url": self.minimax_base_url,
            "proxy_api_keys": self.proxy_api_keys,
            "default_model": self.default_model,
            "available_models": self.available_models,
            "webui_password": self.webui_password,
            "accounts": self.accounts,
        }

# ==================== Pydantic 模型 ====================
class Message(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str
    content: Union[str, List[Dict[str, Any]], None] = None
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None

class ToolFunction(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    description: Optional[str] = None
    parameters: Optional[Dict[str, Any]] = None

class Tool(BaseModel):
    model_config = ConfigDict(extra="allow")
    type: str = "function"
    function: ToolFunction

class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: str = "MiniMax-M2.7"
    messages: List[Message]
    temperature: Optional[float] = 1.0
    top_p: Optional[float] = 0.95
    max_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[List[Tool]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    stop: Optional[Union[str, List[str]]] = None
    presence_penalty: Optional[float] = None
    frequency_penalty: Optional[float] = None
    reasoning_split: Optional[bool] = False
    extra_body: Optional[Dict[str, Any]] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def capture_extra_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            known = {
                "model", "messages", "temperature", "top_p", "max_tokens",
                "stream", "tools", "tool_choice", "stop", "presence_penalty",
                "frequency_penalty", "reasoning_split",
            }
            extra = {k: v for k, v in data.items() if k not in known}
            if extra:
                existing = data.get("extra_body") or {}
                data = {**data, "extra_body": {**existing, **extra}}
        return data

class ChoiceMessage(BaseModel):
    role: str = "assistant"
    content: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None

class Choice(BaseModel):
    index: int = 0
    message: Optional[ChoiceMessage] = None
    delta: Optional[Dict[str, Any]] = None
    finish_reason: Optional[str] = None

class Usage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[Choice]
    usage: Optional[Usage] = None

# ==================== 配置管理器 ====================
class UsageTracker:
    def __init__(self):
        self._lock = threading.RLock()
        self._by_key: Dict[str, UsageStats] = {}
        self._by_model: Dict[str, UsageStats] = {}

    def record(self, proxy_key: str, model: str, prompt: int = 0, completion: int = 0):
        with self._lock:
            if proxy_key not in self._by_key:
                self._by_key[proxy_key] = UsageStats()
            if model not in self._by_model:
                self._by_model[model] = UsageStats()
            self._by_key[proxy_key].requests += 1
            self._by_key[proxy_key].prompt_tokens += prompt
            self._by_key[proxy_key].completion_tokens += completion
            self._by_model[model].requests += 1
            self._by_model[model].prompt_tokens += prompt
            self._by_model[model].completion_tokens += completion

    def get_stats(self) -> dict:
        with self._lock:
            total_req = sum(s.requests for s in self._by_key.values())
            total_tok = sum(s.total_tokens for s in self._by_key.values())
            return {
                "total_requests": total_req,
                "total_tokens": total_tok,
                "by_key": {k: v.to_dict() for k, v in self._by_key.items()},
                "by_model": {k: v.to_dict() for k, v in self._by_model.items()},
            }

class ConfigManager:
    def __init__(self, config_file: str = "config.json"):
        self.config_file = Path(config_file)
        self.config = Config()
        self._lock = threading.RLock()
        self._load()
        self._apply_env()

    def _load(self):
        if not self.config_file.exists():
            self._save()
            return
        try:
            data = json.loads(self.config_file.read_text("utf-8"))
            self.config = Config(
                minimax_api_key=data.get("minimax_api_key", ""),
                minimax_base_url=data.get("minimax_base_url", "https://api.minimax.io/v1"),
                proxy_api_keys=data.get("proxy_api_keys", ["sk-default"]),
                default_model=data.get("default_model", "MiniMax-M2.7"),
                available_models=data.get("available_models", DEFAULT_MODELS.copy()),
                webui_password=data.get("webui_password", "minimax"),
                accounts=data.get("accounts", []),
            )
        except Exception as exc:
            logger.warning(f"[Config] load error: {exc}")
            self.config = Config()
            self._save()

    def _save(self):
        with self._lock:
            self.config_file.write_text(
                json.dumps(self.config.to_dict(), indent=2, ensure_ascii=False), "utf-8"
            )

    def _apply_env(self):
        with self._lock:
            if v := os.environ.get("MINIMAX_API_KEY", "").strip():
                self.config.minimax_api_key = v
            if v := os.environ.get("MINIMAX_BASE_URL", "").strip():
                self.config.minimax_base_url = v
            if v := os.environ.get("DEFAULT_MODEL", "").strip():
                self.config.default_model = v
            if v := os.environ.get("PROXY_API_KEYS", "").strip():
                self.config.proxy_api_keys = [k.strip() for k in v.split(",") if k.strip()]

    def validate_proxy_key(self, key: str) -> bool:
        with self._lock:
            return key in self.config.proxy_api_keys

    def get_minimax_key(self) -> str:
        with self._lock:
            return self.config.minimax_api_key

    def get_base_url(self) -> str:
        with self._lock:
            return self.config.minimax_base_url

    def get_config(self) -> dict:
        with self._lock:
            d = self.config.to_dict()
            key = d.get("minimax_api_key", "")
            d["minimax_api_key_masked"] = (
                key[:8] + "..." + key[-4:] if len(key) > 12 else ("***" if key else "")
            )
            return d

    def update_config(self, new_cfg: dict):
        with self._lock:
            self.config = Config(
                minimax_api_key=new_cfg.get("minimax_api_key", self.config.minimax_api_key),
                minimax_base_url=new_cfg.get("minimax_base_url", self.config.minimax_base_url),
                proxy_api_keys=new_cfg.get("proxy_api_keys", self.config.proxy_api_keys),
                default_model=new_cfg.get("default_model", self.config.default_model),
                available_models=new_cfg.get("available_models", self.config.available_models),
                webui_password=new_cfg.get("webui_password", self.config.webui_password),
                accounts=new_cfg.get("accounts", self.config.accounts),
            )
            self._save()

    def get_accounts(self) -> List[Account]:
        with self._lock:
            if self.config.accounts:
                return [Account(**a) for a in self.config.accounts]
            if self.config.minimax_api_key:
                return [Account(
                    api_key=self.config.minimax_api_key,
                    name="default",
                    base_url=self.config.minimax_base_url,
                )]
            return []

    def save_accounts(self, accounts: List[Account]):
        with self._lock:
            self.config.accounts = [a.to_dict() for a in accounts]
            self._save()

# ==================== MiniMax官网账号密码登录 ====================
async def login_with_password(mobile: str, password: str) -> dict:
    """
    使用手机号和密码登录MiniMax官网获取JWT token
    
    Args:
        mobile: 手机号
        password: 密码
        
    Returns:
        包含 token 和用户信息的字典
    """
    # 尝试多个可能的登录端点
    possible_endpoints = [
        "https://api.minimaxi.com/api/v1/auth/password/login",
        "https://www.minimaxi.com/api/v1/auth/password/login",
        "https://agent.minimaxi.com/api/v1/auth/password/login",
        "https://api.minimax.com/api/v1/auth/password/login",
    ]
    
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        # 首先获取页面，可能需要获取一些必要的参数
        await client.get(MINIMAX_MAIN_URL, headers=FAKE_HEADERS)
        await client.get("https://agent.minimaxi.com", headers=FAKE_HEADERS)
        
        last_error = None
        
        # 尝试每个可能的登录端点
        for login_url in possible_endpoints:
            try:
                logger.info(f"尝试登录端点: {login_url}")
                
                login_payload = {
                    "mobile": mobile,
                    "password": password
                }
                
                # 确定Origin和Referer
                origin = MINIMAX_MAIN_URL
                if "agent.minimaxi.com" in login_url:
                    origin = "https://agent.minimaxi.com"
                
                login_headers = {
                    **FAKE_HEADERS,
                    "Content-Type": "application/json",
                    "Origin": origin,
                    "Referer": f"{origin}/"
                }
                
                resp = await client.post(
                    login_url,
                    json=login_payload,
                    headers=login_headers,
                    timeout=30.0
                )
                
                if resp.status_code == 200:
                    data = resp.json()
                    logger.info(f"端点 {login_url} 响应: {json.dumps(data, ensure_ascii=False)[:200]}")
                    
                    # 检查响应状态
                    status_ok = False
                    if data.get("statusCode") == 0 or data.get("code") == 0:
                        status_ok = True
                    elif "success" in data and data.get("success"):
                        status_ok = True
                    elif "token" in str(data):
                        status_ok = True
                    
                    if status_ok:
                        # 解析登录响应，获取token
                        result_data = data.get("data", data)
                        token = result_data.get("token", "")
                        
                        if not token:
                            # 尝试其他可能的字段名
                            token = (result_data.get("jwt", "") or result_data.get("accessToken", "") or result_data.get("authToken", ""))
                        
                        if token:
                            user_info = result_data.get("user", result_data)
                            user_id = str(user_info.get("id", "") or user_info.get("userId", ""))
                            
                            if not user_id:
                                # 尝试从token解析
                                user_id = _parse_jwt_user_id(token)
                            
                            logger.info(f"登录成功: 手机号 {mobile}, 用户ID {user_id}")
                            
                            return {
                                "success": True,
                                "token": token,
                                "user_id": user_id,
                                "user_info": user_info,
                                "raw_data": data
                            }
                else:
                    logger.warning(f"登录端点 {login_url} 状态码: {resp.status_code}")
                    last_error = f"HTTP {resp.status_code}"
                    
            except Exception as e:
                logger.warning(f"登录端点 {login_url} 异常: {str(e)}")
                last_error = str(e)
        
        # 所有端点都失败了
        error_msg = last_error or "所有登录端点都失败"
        logger.error(f"登录失败: {error_msg}")
        raise RuntimeError(f"登录失败: {error_msg}")

# ==================== MiniMax Web Agent 适配器 ====================
_device_cache: dict = {}
_device_cache_ts: dict = {}

def _md5(s: str) -> str:
    return hashlib.md5(s.encode("utf-8")).hexdigest()

def _unix() -> int:
    return int(time.time())

def _unix_ms() -> str:
    return str(int(time.time() * 1000))

def _uuid() -> str:
    return str(uuid_mod.uuid4())

def _parse_jwt_user_id(jwt_token: str) -> str:
    try:
        parts = jwt_token.split(".")
        if len(parts) != 3:
            return ""
        payload_b64 = parts[1]
        padding = 4 - (len(payload_b64) % 4)
        if padding != 4:
            payload_b64 += "=" * padding
        import base64
        decoded = base64.b64decode(payload_b64).decode("utf-8")
        payload = json.loads(decoded)
        return payload.get("user", {}).get("id", "")
    except Exception:
        return ""

def _build_query_string(jwt_token: str, real_user_id: str,
                       device_id: str = "", uuid_val: str = "",
                       use_random_uuid: bool = False) -> str:
    ts = _unix_ms()
    data = dict(FAKE_USER_DATA)
    if use_random_uuid:
        data["uuid"] = uuid_val or _uuid()
    else:
        data["uuid"] = real_user_id
    data["user_id"] = real_user_id
    data["unix"] = ts
    data["token"] = jwt_token
    if device_id:
        data["device_id"] = device_id
    return "&".join(f"{k}={v}" for k, v in data.items() if v is not None)

def _compute_yy(full_uri_with_qs: str, data_json: str) -> str:
    from urllib.parse import quote
    unix_ms_val = _unix_ms()
    encoded = quote(full_uri_with_qs, safe="")
    return _md5(f"{encoded}_{data_json}{_md5(unix_ms_val)}ooui")

def _compute_signature(timestamp: int, jwt_token: str, data_json: str) -> str:
    return _md5(f"{timestamp}{jwt_token}{data_json}")

def _build_signed_headers(
    jwt_token: str, real_user_id: str, request_body: dict,
    path: str, device_id: str = "", uuid_val: str = "",
) -> dict:
    ts = _unix()
    data_json = json.dumps(request_body, separators=(",", ":"), ensure_ascii=False)
    qs = _build_query_string(jwt_token, real_user_id, device_id, uuid_val)
    full_uri = f"{path}?{qs}" if qs else path
    yy = _compute_yy(full_uri, data_json)
    sig = _compute_signature(ts, jwt_token, data_json)
    return {
        **FAKE_HEADERS,
        "Content-Type": "application/json",
        "Referer": "https://agent.minimaxi.com/",
        "token": jwt_token,
        "x-timestamp": str(ts),
        "x-signature": sig,
        "yy": yy,
    }

def parse_token(raw_token: str) -> tuple:
    if "+" in raw_token:
        parts = raw_token.split("+", 1)
        return parts[1].strip(), parts[0].strip()
    uid = _parse_jwt_user_id(raw_token)
    return raw_token.strip(), uid

async def register_device(
    jwt_token: str, real_user_id: str,
    client: httpx.AsyncClient,
) -> dict:
    cache_key = f"{jwt_token}:{real_user_id}"
    now = _unix()
    if cache_key in _device_cache and (now - _device_cache_ts.get(cache_key, 0)) < DEVICE_INFO_TTL:
        return _device_cache[cache_key]

    random_uuid = _uuid()
    body = {"uuid": random_uuid}
    ts = _unix()
    data_json = json.dumps(body, separators=(",", ":"))
    qs = _build_query_string(jwt_token, real_user_id, uuid_val=random_uuid, use_random_uuid=True)
    path = f"/v1/api/user/device/register?{qs}"
    yy = _compute_yy(path, data_json)
    sig = _compute_signature(ts, jwt_token, data_json)

    headers = {
        **FAKE_HEADERS,
        "Content-Type": "application/json",
        "Referer": "https://agent.minimaxi.com/",
        "token": jwt_token,
        "x-timestamp": str(ts),
        "x-signature": sig,
        "yy": yy,
    }

    resp = await client.post(
        f"{AGENT_BASE_URL}{path}",
        json=body,
        headers=headers,
        timeout=15.0,
    )

    if resp.status_code != 200:
        logger.warning("Device registration failed: HTTP %d — %.200s",
                      resp.status_code, resp.text[:200])
        raise RuntimeError(f"MiniMax device registration failed: HTTP {resp.status_code}")

    data = resp.json()
    status = data.get("statusInfo", {})
    if status.get("code") != 0:
        raise RuntimeError(f"MiniMax device registration rejected: {status.get('message', 'unknown')}")

    result = {
        "deviceId": data.get("data", {}).get("deviceIDStr", ""),
        "realUserID": data.get("data", {}).get("realUserID", real_user_id),
        "uuid": random_uuid,
    }
    _device_cache[cache_key] = result
    _device_cache_ts[cache_key] = now
    logger.info("Device registered: deviceId=%.16s realUserID=%s",
                result["deviceId"], result["realUserID"])
    return result

def _build_msg_payload(messages: list, chat_id: str = "") -> dict:
    system_text = ""
    other_msgs = []

    for m in messages:
        content = ""
        if isinstance(m.get("content"), str):
            content = m["content"]
        elif isinstance(m.get("content"), list):
            texts = [p.get("text", "") for p in m["content"] if p.get("type") == "text"]
            content = "\n".join(texts)
        if not content:
            continue

        if m.get("role") == "system":
            system_text = content
        else:
            other_msgs.append(f"{m['role']}:{content}")

    text_parts = []
    if system_text:
        text_parts.append(f"system:{system_text}")
    text_parts.extend(other_msgs)

    if len(other_msgs) >= 2:
        text_parts.append("assistant:\n")

    text = "\n".join(text_parts)
    if not text:
        text = "hi"

    payload = {
        "msg_type": 1,
        "text": text,
        "chat_type": 1,
        "attachments": [],
        "selected_mcp_tools": [],
        "backend_config": {},
        "sub_agent_ids": [],
    }
    if chat_id:
        payload["chat_id"] = chat_id
    return payload

async def _send_message(
    jwt_token: str, real_user_id: str, device_info: dict,
    payload: dict, client: httpx.AsyncClient,
) -> dict:
    path = "/matrix/api/v1/chat/send_msg"
    qs = _build_query_string(jwt_token, real_user_id,
                           device_info.get("deviceId", ""),
                           device_info.get("uuid", ""))
    url = f"{AGENT_BASE_URL}{path}?{qs}"
    headers = _build_signed_headers(
        jwt_token, real_user_id, payload, path,
        device_id=device_info.get("deviceId", ""),
        uuid_val=device_info.get("uuid", ""),
    )
    resp = await client.post(
        url,
        json=payload,
        headers=headers,
        timeout=15.0,
    )
    if resp.status_code != 200:
        detail = resp.text[:300]
        logger.warning("Send message failed: HTTP %d — %.200s",
                      resp.status_code, detail)
        raise RuntimeError(f"MiniMax send message failed: HTTP {resp.status_code}: {detail}")

    data = resp.json()
    br = data.get("base_resp", {})
    if br.get("status_code") != 0:
        msg = br.get("status_msg", "unknown")
        raise RuntimeError(f"MiniMax send message rejected: {msg}")

    return {
        "chat_id": data.get("chat_id", ""),
        "msg_id": data.get("msg_id", ""),
    }

async def _poll_response(
    chat_id: str,
    jwt_token: str, real_user_id: str, device_info: dict,
    client: httpx.AsyncClient,
    max_polls: int = 240,
    poll_interval: float = 0.5,
) -> dict:
    import asyncio
    body = {"chat_id": chat_id}
    path = "/matrix/api/v1/chat/get_chat_detail"

    for i in range(max_polls):
        await asyncio.sleep(poll_interval)

        qs = _build_query_string(jwt_token, real_user_id,
                               device_info.get("deviceId", ""),
                               device_info.get("uuid", ""))
        url = f"{AGENT_BASE_URL}{path}?{qs}"
        headers = _build_signed_headers(
            jwt_token, real_user_id, body, path,
            device_id=device_info.get("deviceId", ""),
            uuid_val=device_info.get("uuid", ""),
        )
        resp = await client.post(
            url,
            json=body,
            headers=headers,
            timeout=15.0,
        )
        if resp.status_code != 200:
            if i == 0:
                logger.warning("Poll HTTP %d: %.100s", resp.status_code, resp.text[:100])
            continue

        data = resp.json()
        br = data.get("base_resp", {})
        if br.get("status_code") != 0:
            if i == 0:
                logger.warning("Poll base_resp: %s", br)
            continue

        messages = data.get("messages", [])
        ai_msgs = [m for m in messages if m.get("msg_type") == 2]
        if not ai_msgs:
            continue

        ai_msg = ai_msgs[-1]
        if ai_msg.get("msg_content"):
            logger.info("AI response after %d polls", i + 1)
            return ai_msg

    raise RuntimeError("No AI response after polling timeout")

def _to_openai_response(ai_msg: dict, model: str, chat_id: str) -> dict:
    content = ai_msg.get("msg_content", "")
    thinking = ai_msg.get("extra_info", {}).get("thinking_content", "")

    choice = {
        "index": 0,
        "message": {"role": "assistant", "content": content},
        "finish_reason": "stop",
    }
    if thinking:
        choice["message"]["reasoning_content"] = thinking

    return {
        "id": f"chatcmpl-{chat_id}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [choice],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }

async def web_agent_chat(
    model: str, messages: list,
    jwt_token: str, real_user_id: str,
) -> dict:
    async with httpx.AsyncClient(timeout=30.0) as client:
        device_info = await register_device(jwt_token, real_user_id, client)
        payload = _build_msg_payload(messages)
        result = await _send_message(
            jwt_token, real_user_id, device_info, payload, client,
        )
        chat_id = result["chat_id"]
        ai_msg = await _poll_response(
            chat_id, jwt_token, real_user_id, device_info, client,
        )

    return _to_openai_response(ai_msg, model, chat_id)

async def web_agent_chat_stream(
    model: str, messages: list,
    jwt_token: str, real_user_id: str,
) -> AsyncGenerator[str, None]:
    import asyncio

    chat_id = ""
    device_info = None
    ai_msg = None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            device_info = await register_device(jwt_token, real_user_id, client)
            payload = _build_msg_payload(messages)
            result = await _send_message(
                jwt_token, real_user_id, device_info, payload, client,
            )
            chat_id = result["chat_id"]

            last_content = ""
            last_thinking = ""
            body = {"chat_id": chat_id}
            path = "/matrix/api/v1/chat/get_chat_detail"
            max_polls = 180
            sent_role = False

            for poll_i in range(max_polls):
                await asyncio.sleep(0.5)

                qs = _build_query_string(jwt_token, real_user_id,
                                       device_info.get("deviceId", ""),
                                       device_info.get("uuid", ""))
                poll_url = f"{AGENT_BASE_URL}{path}?{qs}"
                headers = _build_signed_headers(
                    jwt_token, real_user_id, body, path,
                    device_id=device_info.get("deviceId", ""),
                    uuid_val=device_info.get("uuid", ""),
                )
                resp = await client.post(
                    poll_url,
                    json=body,
                    headers=headers,
                    timeout=15.0,
                )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                br = data.get("base_resp", {})
                if br.get("status_code") != 0:
                    continue

                chat_data = data.get("chat", {})
                chat_status = chat_data.get("chat_status", 0)
                messages_list = data.get("messages", [])
                ai_msgs = [m for m in messages_list if m.get("msg_type") == 2]

                if not ai_msgs:
                    continue

                current = ai_msgs[-1]
                current_content = current.get("msg_content", "") or ""
                current_thinking = (
                    current.get("extra_info", {}).get("thinking_content", "") or ""
                )

                if current_thinking and len(current_thinking) > len(last_thinking):
                    delta = current_thinking[len(last_thinking):]
                    if delta.strip():
                        if not sent_role:
                            yield _sse_chunk(chat_id, model, {"role": "assistant"})
                            sent_role = True
                        yield _sse_chunk(chat_id, model,
                                        {"reasoning_content": delta})
                    last_thinking = current_thinking

                if current_content and len(current_content) > len(last_content):
                    delta = current_content[len(last_content):]
                    if delta:
                        if not sent_role:
                            yield _sse_chunk(chat_id, model, {"role": "assistant"})
                            sent_role = True
                        yield _sse_chunk(chat_id, model, {"content": delta})
                    last_content = current_content

                if chat_status == 2 and current_content:
                    yield _sse_chunk(chat_id, model, {},
                                    finish_reason="stop")
                    ai_msg = current
                    break

    except Exception as e:
        logger.error("Streaming error: %s", e)
        yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"

    yield "data: [DONE]\n\n"

def _sse_chunk(chat_id: str, model: str, delta: dict,
              finish_reason: Optional[str] = None) -> str:
    chunk = {
        "id": f"chatcmpl-{chat_id}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "delta": delta,
            "finish_reason": finish_reason,
        }],
    }
    return f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"

async def test_adapter(jwt_token: str, real_user_id: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            device_info = await register_device(jwt_token, real_user_id, client)
            payload = _build_msg_payload([{"role": "user", "content": "Hi"}])
            result = await _send_message(
                jwt_token, real_user_id, device_info, payload, client,
            )
            ai_msg = await _poll_response(
                result["chat_id"], jwt_token, real_user_id, device_info, client,
                max_polls=120, poll_interval=0.5,
            )
        return {
            "success": True,
            "response": (ai_msg.get("msg_content", "") or "")[:200],
            "chat_id": result["chat_id"],
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

# ==================== 代理功能 ====================
def resolve_model(client_model: str) -> str:
    return MODEL_MAP.get(client_model, client_model)

def _is_web_agent(acct: Account) -> bool:
    return acct.auth_mode == "token" and "agent.minimaxi.com" in (acct.base_url or "")

def _get_web_agent_creds(acct: Account) -> tuple:
    raw = (acct.auth_token or acct.api_key or "").strip()
    return parse_token(raw)

def _pick_account() -> Optional[Account]:
    accounts = config_manager.get_accounts()
    if not accounts:
        return None

    now = time.time()
    active = [a for a in accounts if a.is_active and not a.on_cooldown]
    if not active:
        active = sorted(accounts, key=lambda a: a.cooldown_until)
        if active:
            logger.warning("All accounts on cooldown; using soonest-recovering")
            return active[0]
        return None

    active.sort(key=lambda a: a.last_used)
    return active[0]

def _mark_used(acct: Account):
    acct.last_used = time.time()
    acct.request_count += 1

def _mark_failed(acct: Account, status: int):
    backoff = min(60 * (2 ** min(acct.request_count, 5)), 600)
    acct.cooldown_until = time.time() + backoff
    acct.is_active = False
    logger.warning("Account '%s' cooldown %ds after HTTP %d", acct.name, backoff, status)

def _build_headers(acct: Account) -> dict:
    token = acct.auth_token if acct.auth_mode == "token" and acct.auth_token else acct.api_key
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

def _build_payload(model: str, messages: list, params: dict) -> dict:
    resolved = resolve_model(model)
    payload: dict = {"model": resolved, "messages": messages}

    for field in ("temperature", "top_p", "max_tokens", "stream",
                  "tools", "tool_choice", "stop", "presence_penalty",
                  "frequency_penalty"):
        if field in params and params[field] is not None:
            if field == "temperature":
                payload["temperature"] = max(0.01, min(1.0, float(params[field])))
            else:
                payload[field] = params[field]

    if params.get("reasoning_split"):
        payload["reasoning_split"] = True

    extra = params.get("extra_body") or {}
    if extra:
        payload.update(extra)

    return payload

async def _execute_request(
    acct: Account, payload: dict, stream: bool = False
) -> httpx.Response:
    url = f"{acct.base_url.rstrip('/')}/chat/completions"
    headers = _build_headers(acct)
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)

    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)

    if resp.status_code != 200:
        detail = resp.text[:500]
        logger.warning("MiniMax error: HTTP %d — %.200s", resp.status_code, detail)
        _mark_failed(acct, resp.status_code)
        raise HTTPException(
            status_code=resp.status_code,
            detail={"error": {"message": f"MiniMax 上游错误: {detail}", "type": "upstream_error"}},
        )

    _mark_used(acct)
    return resp

async def proxy_chat(model: str, messages: list, params: dict, proxy_key: str) -> dict:
    acct = _pick_account()
    if not acct:
        raise HTTPException(status_code=503, detail={
            "error": {"message": "没有可用的账号（已冷却）", "type": "unavailable"},
        })

    if _is_web_agent(acct):
        jwt_token, real_user_id = _get_web_agent_creds(acct)
        try:
            result = await web_agent_chat(model, messages, jwt_token, real_user_id)
            _mark_used(acct)
            usage_tracker.record(proxy_key, resolve_model(model), 0, 0)
            return result
        except Exception as e:
            _mark_failed(acct, 503)
            raise HTTPException(
                status_code=503,
                detail={"error": {"message": str(e), "type": "upstream_error"}},
            )

    payload = _build_payload(model, messages, params)
    payload["stream"] = False
    resp = await _execute_request(acct, payload)
    data = resp.json()

    usage = data.get("usage", {})
    usage_tracker.record(proxy_key, payload["model"],
                         usage.get("prompt_tokens", 0),
                         usage.get("completion_tokens", 0))
    return data

async def proxy_chat_stream(
    model: str, messages: list, params: dict, proxy_key: str
) -> AsyncGenerator[str, None]:
    acct = _pick_account()
    if not acct:
        yield f"data: {json.dumps({'error': {'message': '没有可用的账号（已冷却）', 'type': 'unavailable'}})}\n\n"
        yield "data: [DONE]\n\n"
        return

    if _is_web_agent(acct):
        jwt_token, real_user_id = _get_web_agent_creds(acct)
        try:
            async for chunk in web_agent_chat_stream(model, messages, jwt_token, real_user_id):
                yield chunk
            _mark_used(acct)
            usage_tracker.record(proxy_key, resolve_model(model), 0, 0)
        except Exception as e:
            _mark_failed(acct, 503)
            yield f"data: {json.dumps({'error': {'message': str(e), 'type': 'upstream_error'}})}\n\n"
            yield "data: [DONE]\n\n"
        return

    payload = _build_payload(model, messages, params)
    payload["stream"] = True
    url = f"{acct.base_url.rstrip('/')}/chat/completions"
    headers = _build_headers(acct)
    timeout = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", url, headers=headers, json=payload) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    _mark_failed(acct, resp.status_code)
                    yield f"data: {json.dumps({'error': {'message': body.decode()[:500], 'type': 'upstream_error'}})}\n\n"
                    yield "data: [DONE]\n\n"
                    return

                _mark_used(acct)
                prompt_tok = 0
                completion_tok = 0

                async for line in resp.aiter_lines():
                    if not line:
                        continue
                    if line.startswith("data: "):
                        data = line[6:]
                        if data.strip() == "[DONE]":
                            break
                        yield f"data: {data}\n\n"
                        try:
                            chunk = json.loads(data)
                            u = chunk.get("usage", {}) or {}
                            if u.get("total_tokens", 0) > 0:
                                prompt_tok = u.get("prompt_tokens", 0)
                                completion_tok = u.get("completion_tokens", 0)
                        except json.JSONDecodeError:
                            pass

                usage_tracker.record(proxy_key, payload["model"], prompt_tok, completion_tok)
                yield "data: [DONE]\n\n"

    except httpx.ConnectError as e:
        yield f"data: {json.dumps({'error': {'message': f'连接失败: {e}', 'type': 'upstream_connection_error'}})}\n\n"
        yield "data: [DONE]\n\n"
    except httpx.TimeoutException:
        yield f"data: {json.dumps({'error': {'message': '请求超时', 'type': 'upstream_timeout'}})}\n\n"
        yield "data: [DONE]\n\n"

async def test_connection() -> dict:
    acct = _pick_account()
    if not acct:
        return {"success": False, "error": "未配置账号"}
    return await _test_account(acct)

async def test_account_by_index(idx: int) -> dict:
    accounts = config_manager.get_accounts()
    if idx < 0 or idx >= len(accounts):
        return {"success": False, "error": f"账号索引 {idx} 超出范围"}
    return await _test_account(accounts[idx])

async def _test_account(acct: Account) -> dict:
    if _is_web_agent(acct):
        jwt_token, real_user_id = _get_web_agent_creds(acct)
        return await test_adapter(jwt_token, real_user_id)

    payload = {
        "model": config_manager.config.default_model,
        "messages": [{"role": "user", "content": "Hi"}],
        "max_tokens": 16,
    }

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{acct.base_url.rstrip('/')}/chat/completions",
                headers=_build_headers(acct), json=payload,
            )
        if resp.status_code == 200:
            data = resp.json()
            return {
                "success": True,
                "model": data.get("model", payload["model"]),
                "response": (data.get("choices", [{}])[0]
                             .get("message", {})
                             .get("content", "")[:200]),
            }
        return {"success": False, "error": f"HTTP {resp.status_code}: {resp.text[:300]}"}
    except Exception as e:
        return {"success": False, "error": str(e)}

def get_accounts_status() -> list[dict]:
    accounts = config_manager.get_accounts()
    result = []
    for a in accounts:
        result.append({
            "name": a.name,
            "is_active": a.is_active,
            "on_cooldown": a.on_cooldown,
            "request_count": a.request_count,
            "last_used": a.last_used,
            "auth_mode": a.auth_mode,
            "api_key_preview": (
                (a.api_key[:8] + "…" + a.api_key[-4:])
                if a.api_key and len(a.api_key) > 12
                else (a.api_key[:12] if a.api_key else "")
            ),
        })
    return result

async def fetch_models() -> dict:
    return {
        "object": "list",
        "data": [
            {"id": m, "object": "model", "created": 1681940951, "owned_by": "minimax"}
            for m in config_manager.config.available_models
        ],
    }

# ==================== 认证功能 ====================
def extract_api_key(
    authorization: str = Header(None),
    x_api_key: str = Header(None, alias="x-api-key"),
) -> str:
    key = None
    if authorization:
        key = authorization.replace("Bearer ", "").strip()
    elif x_api_key:
        key = x_api_key.strip()

    if not key:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "缺少 API 密钥。请通过 'Authorization: Bearer <key>' 或 'X-API-Key: <key>' 提供。",
                    "type": "authentication_error",
                }
            },
        )

    if not config_manager.validate_proxy_key(key):
        raise HTTPException(
            status_code=401,
            detail={
                "error": {
                    "message": "无效的 API 密钥。请在设置页面中配置正确的代理密钥。",
                    "type": "authentication_error",
                }
            },
        )

    return key

# ==================== 全局实例 ====================
config_manager = ConfigManager()
usage_tracker = UsageTracker()

# ==================== FastAPI 应用 ====================
_env = Path(__file__).parent / ".env"
if _env.exists():
    for line in _env.read_text("utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip("\"'")
        if k and not os.environ.get(k):
            os.environ[k] = v

STATIC = Path(__file__).parent / "static"
ADMIN_STATIC = Path(__file__).parent / "admin_static"

@asynccontextmanager
async def lifespan(_app: FastAPI):
    port = int(os.environ.get("PORT", "8000"))
    logger.info("=" * 58)
    logger.info("  MiniMax All-in-One — OpenAI-compatible proxy")
    logger.info("  API   : http://localhost:%d/v1/chat/completions", port)
    logger.info("  Admin : http://localhost:%d/admin/", port)
    logger.info("  Docs  : http://localhost:%d/docs", port)
    logger.info("=" * 58)
    yield

app = FastAPI(title="MiniMax All-in-One", version="1.1.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])

if STATIC.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")
if ADMIN_STATIC.exists():
    app.mount("/admin/static", StaticFiles(directory=str(ADMIN_STATIC)), name="admin_static")

@app.get("/")
async def index():
    idx = STATIC / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text("utf-8"))
    return JSONResponse({"error": "WebUI not found"})

@app.get("/admin")
@app.get("/admin/")
async def admin_index():
    idx = ADMIN_STATIC / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text("utf-8"))
    return HTMLResponse("<h1>Admin UI not found</h1>", status_code=404)

@app.post("/v1/chat/completions")
async def chat_completions(
    request: ChatCompletionRequest,
    authorization: str = Header(None),
    x_api_key: str = Header(None, alias="x-api-key"),
):
    proxy_key = extract_api_key(authorization, x_api_key)
    params = request.model_dump()
    messages = [m.model_dump(exclude_none=True) for m in request.messages]

    if request.stream:
        return StreamingResponse(
            proxy_chat_stream(request.model, messages, params, proxy_key),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    result = await proxy_chat(request.model, messages, params, proxy_key)
    return JSONResponse(result)

@app.get("/v1/models")
async def list_models(
    authorization: str = Header(None),
    x_api_key: str = Header(None, alias="x-api-key"),
):
    extract_api_key(authorization, x_api_key)
    return JSONResponse(await fetch_models())

class LoginRequest(BaseModel):
    password: str

class MiniMaxLoginRequest(BaseModel):
    mobile: str
    password: str

@app.post("/api/auth/login")
async def webui_login(req: LoginRequest):
    if req.password == config_manager.config.webui_password:
        return {"success": True}
    return JSONResponse(
        {"success": False, "error": "密码错误"},
        status_code=401,
    )

@app.get("/api/config")
async def get_config():
    return JSONResponse(config_manager.get_config())

@app.post("/api/config")
async def update_config(req: Request):
    try:
        data = await req.json()
        config_manager.update_config(data)
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"status": "error", "error": str(e)}, status_code=400)

@app.post("/api/test")
async def test_api():
    return JSONResponse(await test_connection())

@app.post("/api/test-account/{idx:int}")
async def test_account_route(idx: int):
    return JSONResponse(await test_account_by_index(idx))

@app.get("/api/accounts/status")
async def accounts_status():
    return JSONResponse(get_accounts_status())

@app.get("/api/models")
async def api_models():
    return JSONResponse(await fetch_models())

@app.get("/api/usage")
async def get_usage():
    return JSONResponse(usage_tracker.get_stats())

@app.post("/api/minimax/login")
async def minimax_auto_login(req: MiniMaxLoginRequest):
    """
    使用MiniMax账号密码自动登录并配置为web agent账号
    """
    try:
        # 使用账号密码登录
        login_result = await login_with_password(req.mobile, req.password)
        
        if not login_result.get("success"):
            return JSONResponse({
                "success": False,
                "error": login_result.get("error", "登录失败")
            }, status_code=401)
        
        # 获取token和用户ID
        token = login_result.get("token", "")
        user_id = login_result.get("user_id", "")
        user_info = login_result.get("user_info", {})
        
        # 构建账号名称
        account_name = user_info.get("name", "") or req.mobile
        if not account_name:
            account_name = token[:8] if len(token) >= 8 else "minimax-account"
        
        # 获取当前配置
        current_config = config_manager.get_config()
        existing_accounts = current_config.get("accounts", [])
        
        # 创建新账号配置
        new_account = {
            "api_key": token,
            "name": account_name,
            "base_url": "https://agent.minimaxi.com/v1",
            "auth_mode": "token",
            "auth_token": token,
            "is_active": True,
            "request_count": 0
        }
        
        # 检查是否已存在相同账号（通过token前缀或手机号匹配）
        updated_accounts = []
        account_added = False
        
        for acc in existing_accounts:
            # 如果已存在相同的token，更新它
            if acc.get("auth_token", "").startswith(token[:20]) or acc.get("api_key", "").startswith(token[:20]):
                updated_accounts.append(new_account)
                account_added = True
            else:
                updated_accounts.append(acc)
        
        if not account_added:
            updated_accounts.append(new_account)
        
        # 更新配置
        new_config = {
            "accounts": updated_accounts,
            # 保持其他配置不变
            "minimax_api_key": current_config.get("minimax_api_key", ""),
            "minimax_base_url": current_config.get("minimax_base_url", ""),
            "proxy_api_keys": current_config.get("proxy_api_keys", []),
            "default_model": current_config.get("default_model", "MiniMax-M2.7"),
            "available_models": current_config.get("available_models", DEFAULT_MODELS.copy()),
            "webui_password": current_config.get("webui_password", "minimax")
        }
        
        config_manager.update_config(new_config)
        
        logger.info(f"MiniMax账号 {account_name} 已自动配置成功")
        
        return JSONResponse({
            "success": True,
            "message": "账号登录并配置成功",
            "account_name": account_name,
            "user_id": user_id,
            "user_info": user_info
        })
        
    except Exception as e:
        logger.error(f"MiniMax自动登录失败: {str(e)}")
        return JSONResponse({
            "success": False,
            "error": str(e)
        }, status_code=500)

@app.get("/health")
async def health():
    return {"status": "ok", "version": "1.1.0", "service": "minimax_all_in_one"}

@app.api_route("/{path:path}", methods=["GET"])
async def spa_fallback(path: str):
    if path.startswith(("api/", "v1/", "static/", "admin/", "health")):
        return JSONResponse({"error": "Not found"}, status_code=404)
    idx = STATIC / "index.html"
    if idx.exists():
        return HTMLResponse(idx.read_text("utf-8"))
    return JSONResponse({"error": "WebUI not found"}, status_code=404)

def main():
    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")

if __name__ == "__main__":
    main()
