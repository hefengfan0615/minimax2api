"""test_chat.py — 调现有 web_agent_chat（非流式），确认 AI 调用链路。"""

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
log = logging.getLogger("test_chat")

sys.path.insert(0, str(Path(__file__).parent))
from minimax_adapter import web_agent_chat, parse_token  # noqa


async def main():
    cfg = json.loads(Path(__file__).parent.joinpath("config.json").read_text())
    account = next((a for a in cfg.get("accounts", [])
                    if a.get("auth_mode") == "token"
                    and a.get("base_url", "").startswith("https://agent.minimaxi.com")), None)
    raw_token = account["auth_token"]
    jwt, real_uid = parse_token(raw_token)
    log.info("账号 userID=%s", real_uid)

    for model in ["MiniMax-Text-01", "abab6.5s-chat", "abab6.5-chat", "abab5.5-chat", "MiniMax-01"]:
        log.info("=" * 50)
        log.info("尝试模型: %s", model)
        try:
            resp = await web_agent_chat(
                model=model,
                messages=[{"role": "user", "content": "hi"}],
                jwt_token=jwt,
                real_user_id=real_uid,
            )
            log.info("✓ 成功! 响应: %s", json.dumps(resp, ensure_ascii=False)[:500])
            return 0
        except Exception as e:
            log.warning("✗ %s", e)
    log.error("所有模型都失败")
    return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
