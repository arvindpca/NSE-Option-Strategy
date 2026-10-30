"""Angel One login ko din me ek hi baar karne ke liye session cache.

Pehle har run (har 15 min) naya login hota tha -> din me 25+ login sirf ek bot se.
Ab: pehla run login karta hai aur session (token) ko ENCRYPTED file me save karta hai;
baaki runs wahi session use karte hain (GitHub cache se file wapas aati hai).

Safety:
- File encrypted hai. Key = ANGEL_API_KEY + ANGEL_TOTP_SECRET (GitHub secrets), isliye
  bina secrets ke koi file padh nahi sakta.
- Session sirf aaj (IST) ka hi use hota hai, aur use se pehle Angel se check hota hai
  (getProfile). Kharab/expire ho to naya login.
- Kuch bhi galat ho -> chupchaap normal login (bot kabhi nahi rukega).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_PATH = Path(os.getenv("ANGEL_SESSION_CACHE", ".angel_session/session.bin"))


def _env(name: str) -> str:
    raw = os.getenv(name, "") or ""
    return "".join(ch for ch in raw.strip() if unicodedata.category(ch) != "Cf")


def _key() -> bytes | None:
    api, totp = _env("ANGEL_API_KEY"), _env("ANGEL_TOTP_SECRET")
    if not api or not totp:
        return None
    return hashlib.sha256(f"angel-session-cache|{api}|{totp}".encode()).digest()


def _stream(key: bytes, nonce: bytes, n: int) -> bytes:
    out, i = b"", 0
    while len(out) < n:
        out += hashlib.sha256(key + nonce + i.to_bytes(8, "big")).digest()
        i += 1
    return out[:n]


def _encrypt(key: bytes, data: bytes) -> bytes:
    nonce = os.urandom(16)
    ct = bytes(a ^ b for a, b in zip(data, _stream(key, nonce, len(data))))
    tag = hmac.new(key, nonce + ct, hashlib.sha256).digest()
    return base64.b64encode(nonce + tag + ct)


def _decrypt(key: bytes, blob: bytes) -> bytes | None:
    raw = base64.b64decode(blob)
    nonce, tag, ct = raw[:16], raw[16:48], raw[48:]
    if not hmac.compare_digest(tag, hmac.new(key, nonce + ct, hashlib.sha256).digest()):
        return None
    return bytes(a ^ b for a, b in zip(ct, _stream(key, nonce, len(ct))))


def _today() -> str:
    return datetime.now(IST).strftime("%Y-%m-%d")


def save(api) -> None:
    """Login ke baad call karo: session ko encrypted file me rakh do."""
    try:
        key = _key()
        if key is None or not getattr(api, "access_token", None):
            return
        payload = json.dumps({
            "day": _today(),
            "access_token": api.access_token,
            "refresh_token": api.refresh_token,
            "feed_token": api.feed_token,
            "user_id": getattr(api, "userId", None),
        }).encode()
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        CACHE_PATH.write_bytes(_encrypt(key, payload))
        print("[angel-cache] session saved (aaj ke baaki runs dobara login nahi karenge)", flush=True)
    except Exception as exc:  # cache kabhi bot ko nahi todega
        print(f"[angel-cache] save skipped: {type(exc).__name__}", flush=True)


def load():
    """Aaj ka valid session ho to SmartConnect lautao, warna None."""
    try:
        key = _key()
        if key is None or not CACHE_PATH.exists():
            return None
        data = _decrypt(key, CACHE_PATH.read_bytes())
        if not data:
            print("[angel-cache] cache file match nahi hui -> naya login", flush=True)
            return None
        s = json.loads(data)
        if s.get("day") != _today():
            print("[angel-cache] purana din ka session -> naya login", flush=True)
            return None
        from SmartApi import SmartConnect
        api = SmartConnect(api_key=_env("ANGEL_API_KEY"), access_token=s["access_token"],
                           refresh_token=s["refresh_token"], feed_token=s["feed_token"],
                           userId=s.get("user_id"))
        check = api.getProfile(s["refresh_token"])
        if not (isinstance(check, dict) and check.get("status")):
            print("[angel-cache] session expire ho gaya -> naya login", flush=True)
            return None
        print("[angel-cache] aaj ka session reuse kiya (login nahi kiya)", flush=True)
        return api
    except Exception as exc:
        print(f"[angel-cache] cache use nahi hua ({type(exc).__name__}) -> naya login", flush=True)
        return None


def session_data(api) -> dict:
    """generateSession jaisa dict (kuch purana code jwtToken isi se padhta hai)."""
    return {"status": True, "data": {"jwtToken": "Bearer " + str(api.access_token),
                                     "refreshToken": api.refresh_token,
                                     "feedToken": api.feed_token,
                                     "clientcode": getattr(api, "userId", None)}}
