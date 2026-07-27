"""One-command Telegram wiring for local runs.

Prerequisite (manual, cannot be scripted): create a bot with @BotFather in the
Telegram app, then put its token in .env as TELEGRAM_BOT_TOKEN, and send any
message (e.g. /start) to your new bot from your own account.

Then run:  python scripts/setup_telegram.py

It reads the token from .env, calls getUpdates to discover the chat id that
messaged the bot, writes TELEGRAM_CHAT_ID back into .env, and sends a test
message so you can confirm delivery end to end. Safe to re-run.
"""
from __future__ import annotations

import sys
from pathlib import Path

import httpx

ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
API = "https://api.telegram.org/bot{token}/{method}"


def _read_env(path: Path) -> list[str]:
    if not path.exists():
        sys.exit(f"No .env at {path}. Copy .env.example to .env first.")
    return path.read_text(encoding="utf-8").splitlines()


def _env_value(lines: list[str], key: str) -> str:
    for line in lines:
        s = line.strip()
        if s.startswith(f"{key}=") and not s.startswith("#"):
            return s.split("=", 1)[1].strip()
    return ""


def _set_env_value(lines: list[str], key: str, value: str) -> list[str]:
    out, replaced = [], False
    for line in lines:
        if line.strip().startswith(f"{key}=") and not line.strip().startswith("#"):
            out.append(f"{key}={value}")
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f"{key}={value}")
    return out


def _chat_id_from_updates(updates: list[dict]) -> tuple[str | None, str | None]:
    """Return (chat_id, human_label) from the most recent update that carries a
    chat. Handles plain messages, channel posts, and callback queries."""
    for upd in reversed(updates):
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            chat = (upd.get(key) or {}).get("chat")
            if chat:
                return str(chat["id"]), chat.get("title") or chat.get("username") or chat.get(
                    "first_name") or str(chat["id"])
        cb = upd.get("callback_query")
        if cb and cb.get("message", {}).get("chat"):
            chat = cb["message"]["chat"]
            return str(chat["id"]), chat.get("username") or str(chat["id"])
    return None, None


def main() -> int:
    lines = _read_env(ENV_PATH)
    token = _env_value(lines, "TELEGRAM_BOT_TOKEN")
    if not token:
        print("TELEGRAM_BOT_TOKEN is empty in .env.\n"
              "  1. In Telegram, message @BotFather, send /newbot, follow the prompts.\n"
              "  2. Paste the token into the TELEGRAM_BOT_TOKEN= line in .env.\n"
              "  3. Send /start to your new bot, then re-run this script.")
        return 1

    r = httpx.get(API.format(token=token, method="getUpdates"), timeout=30)
    if r.status_code == 401:
        print("Telegram rejected the token (401). Double-check TELEGRAM_BOT_TOKEN in .env.")
        return 1
    r.raise_for_status()
    updates = r.json().get("result", [])
    chat_id, label = _chat_id_from_updates(updates)
    if not chat_id:
        print("No chat found yet. Open your bot in Telegram and send it any message "
              "(e.g. /start), then re-run this script.")
        return 1

    lines = _set_env_value(lines, "TELEGRAM_CHAT_ID", chat_id)
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"Wrote TELEGRAM_CHAT_ID={chat_id} ({label}) to .env")

    send = httpx.post(API.format(token=token, method="sendMessage"),
                      json={"chat_id": chat_id,
                            "text": "SmartCapital is wired up. Approval prompts will arrive here."},
                      timeout=30)
    if send.status_code == 200 and send.json().get("ok"):
        print("Test message sent - check your Telegram.")
        return 0
    print(f"Chat id saved, but the test send failed: {send.status_code} {send.text[:200]}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
