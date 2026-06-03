"""test_stream.py — 调现有 minimax_adapter.web_agent_chat_stream 看 AI 流响应。"""

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
log = logging.getLogger("test_stream")

sys.path.insert(0, str(Path(__file__).parent))
from minimax_adapter import web_agent_chat_stream, parse_token  # noqa


async def main():
    cfg = json.loads(Path(__file__).parent.joinpath("config.json").read_text())
    account = next((a for a in cfg.get("accounts", [])
                    if a.get("auth_mode") == "token"
                    and a.get("base_url", "").startswith("https://agent.minimaxi.com")), None)
    if not account:
        log.error("config.json 里没找到 web-token 账号")
        return 1

    raw_token = account["auth_token"]
    jwt, real_uid = parse_token(raw_token)
    log.info("账号: %s, userID=%s", account.get("name"), real_uid)

    log.info("发送 '你好'，等 AI 流响应…")
    log.info("=" * 50)
    chunks = 0
    full = ""
    try:
        async for piece in web_agent_chat_stream(
            model="MiniMax-Text-01",
            messages=[{"role": "user", "content": "你好, 请用 1 句话说 hi"}],
            jwt_token=jwt,
            real_user_id=real_uid,
        ):
            chunks += 1
            full += piece
            sys.stdout.write(piece)
            sys.stdout.flush()
        log.info("")
        log.info("=" * 50)
        log.info(f"✓ 完成, {chunks} chunks, 内容: {full[:100]!r}")
        return 0
    except Exception as e:
        log.error("✗ 调用失败: %s", e)
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
