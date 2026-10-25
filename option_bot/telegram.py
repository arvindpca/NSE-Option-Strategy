import os
import unicodedata

import requests


def _clean(v):
    v = "".join(c for c in (v or "") if unicodedata.category(c) != "Cf").strip().strip("\"'").strip()
    return v[3:] if v.lower().startswith("bot") and ":" in v else v


def send(text: str) -> bool:
    token, chat = _clean(os.getenv("TELEGRAM_BOT_TOKEN")), _clean(os.getenv("TELEGRAM_CHAT_ID"))
    if not token or not chat:
        print("[telegram] secrets nahi hain - message sirf log me:\n" + text, flush=True)
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                                "disable_web_page_preview": True}, timeout=20)
        if r.status_code != 200:
            print(f"[telegram] fail {r.status_code}: {r.text[:200]}", flush=True)
        return r.status_code == 200
    except Exception as e:  # noqa: BLE001
        print(f"[telegram] error: {e}", flush=True)
        return False
