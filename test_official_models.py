"""test_official_models.py — 试官网所有 model ID，看哪个能过（账号余额 0 的话全部 50110）。

来源: /workspace/config.py DEFAULT_MODELS / MODEL_MAP
  MiniMax-M2.7        MiniMax-M2.7-highspeed
  MiniMax-M2.5        MiniMax-M2.5-highspeed
  MiniMax-M2.1        MiniMax-M2.1-highspeed
  MiniMax-M2
"""

import asyncio
import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("test_models")

sys.path.insert(0, str(Path(__file__).parent))
from minimax_adapter import web_agent_chat_stream, parse_token  # noqa

OFFICIAL_MODELS = [
    "MiniMax-M2.7",
    "MiniMax-M2.7-highspeed",
    "MiniMax-M2.5",
    "MiniMax-M2.5-highspeed",
    "MiniMax-M2.1",
    "MiniMax-M2.1-highspeed",
    "MiniMax-M2",
]


async def try_model(model, jwt, real_uid):
    log.info("─" * 60)
    log.info("试模型: %s", model)
    try:
        chunks = 0
        full = ""
        async for piece in web_agent_chat_stream(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            jwt_token=jwt,
            real_user_id=real_uid,
        ):
            chunks += 1
            full += piece
        log.info("  ✓ 流式返回 (%d chunks): %.200s", chunks, full[:200])
        if '"50110"' in full or "Not enough credits" in full or "积分不足" in full:
            return "no_credits"
        if '"50103"' in full or "model not found" in full or "50104" in full:
            return "model_not_found"
        return "ok"
    except Exception as e:
        msg = str(e)
        if "积分不足" in msg or "50110" in msg:
            return "no_credits"
        if "50103" in msg or "50104" in msg:
            return "model_not_found"
        log.warning("  ✗ 异常: %s", msg[:200])
        return "error"


async def main():
    cfg = json.loads(Path(__file__).parent.joinpath("config.json").read_text())
    account = next((a for a in cfg.get("accounts", [])
                    if a.get("auth_mode") == "token"
                    and a.get("base_url", "").startswith("https://agent.minimaxi.com")), None)
    if not account:
        log.error("config.json 里没 web-token 账号")
        return 1

    jwt, real_uid = parse_token(account["auth_token"])
    log.info("账号: %s, userID=%s", account.get("name"), real_uid)
    log.info("将测试 %d 个官方模型", len(OFFICIAL_MODELS))
    print()

    results = {}
    for m in OFFICIAL_MODELS:
        r = await try_model(m, jwt, real_uid)
        results[m] = r

    print()
    log.info("=" * 60)
    log.info("测试结果:")
    for m, r in results.items():
        icon = {"ok": "✓", "no_credits": "💰", "model_not_found": "✗", "error": "?"}.get(r, "?")
        log.info(f"  {icon}  {m:30s} → {r}")

    if all(r == "no_credits" for r in results.values()):
        log.warning("所有模型都是 '积分不足' — 账号余额 0，需充值")
        log.warning("访问 https://agent.minimaxi.com/ 给白蓝账号充值后再跑")
        return 0
    if any(r == "ok" for r in results.values()):
        log.info("有模型能用！")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
