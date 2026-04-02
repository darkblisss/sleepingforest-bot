import os
import json
import time
import base64
import requests
from datetime import datetime, timezone, timedelta
from nacl import encoding, public

DONATIONS_URL = "https://api-v1.degenidle.com/api/guilds/d08f77ef-fc13-4781-adce-0fcf88f9f77b/donations/daily?day=today&characterId=ee938e63-72e6-4b8e-82bf-672ca6e0a568"
DISCORD_WEBHOOK_URL = os.environ.get("DONATIONS_WEBHOOK_URL", "").strip()
ERROR_WEBHOOK_URL = os.environ.get("ERROR_WEBHOOK_URL", "").strip()
FULL_DONATION_COUNT = 20
MEMBERS_FILE = "members.json"
GH_PAT = os.environ.get("GH_PAT", "").strip()
GH_REPO = "darkblisss/donations-bot"

def send_error_alert(message):
    if ERROR_WEBHOOK_URL:
        try:
            requests.post(ERROR_WEBHOOK_URL, json={"content": f"⚠️ Donations Bot Error: {message}"}, timeout=10)
        except Exception:
            pass

def update_github_secret(new_refresh_token):
    if not GH_PAT or not new_refresh_token:
        send_error_alert("⛔ GH_PAT or new_refresh_token missing — secret NOT updated, chain will break in 24h")
        return
    repos = ["darkblisss/worldboss-bot", "darkblisss/donations-bot"]
    for repo in repos:
        success = False
        for attempt in range(3):
            try:
                r = requests.get(
                    f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
                    headers={"Authorization": f"Bearer {GH_PAT}"},
                    timeout=10,
                )
                r.raise_for_status()
                key_data = r.json()
                public_key = public.PublicKey(
                    key_data["key"].encode(), encoding.Base64Encoder
                )
                box = public.SealedBox(public_key)
                encrypted = base64.b64encode(
                    box.encrypt(new_refresh_token.encode())
                ).decode()
                put_r = requests.put(
                    f"https://api.github.com/repos/{repo}/actions/secrets/DEGEN_REFRESH_TOKEN",
                    headers={"Authorization": f"Bearer {GH_PAT}"},
                    json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
                    timeout=10,
                )
                if put_r.status_code in (201, 204):
                    success = True
                    break
                else:
                    time.sleep(2)
            except Exception:
                time.sleep(2)
        if not success:
            send_error_alert(f"⛔ Failed to save new DEGEN_REFRESH_TOKEN to {repo} after 3 attempts — chain will break in 24h")

def load_members():
    if not os.path.exists(MEMBERS_FILE):
        return {}
    try:
        with open(MEMBERS_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}

def refresh_access_token():
    refresh_token = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise RuntimeError("Missing DEGEN_REFRESH_TOKEN")

    print(f"[TOKEN] Using refresh token ending in: ...{refresh_token[-4:]}")

    try:
        r = requests.post(
            "https://auth.degenidle.com/oauth2/token",
            data={
                "client_id": "c9563b2ef30348f182e122030ef28ad7",
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
            },
            timeout=20
        )
        r.raise_for_status()
    except Exception as e:
        send_error_alert(
            f"⛔ TOKEN EXPIRED — manually update DEGEN_REFRESH_TOKEN in GitHub Secrets for BOTH repos NOW. "
            f"You have ~24h before worldboss bot also fails. Error: {e}"
        )
        raise
    data = r.json()
    new_refresh = data.get("refresh_token")
    if new_refresh:
        print(f"[TOKEN] New refresh token ending in: ...{new_refresh[-4:]}")
        same = new_refresh == refresh_token
        print(f"[TOKEN] Token changed: {'NO — SAME TOKEN, rotation may be broken!' if same else 'YES — token rotated successfully'}")
        update_github_secret(new_refresh)
    else:
        send_error_alert("⛔ DEGEN did not return a new refresh_token — rotation will break within 24h")
    return data["access_token"]

def hours_until_reset():
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    hours = round((midnight - now).total_seconds() / 3600)
    return hours

def build_embed(not_donated, used, cap, discord_map):
    percent = round((used / cap) * 100) if cap > 0 else 0
    count = len(not_donated)
    member_word = "member" if count == 1 else "members"
    hours = hours_until_reset()
    lines = []
    lines.append(f"Total donations today: {used}/{cap} ({percent}%)")
    lines.append(f"{count} {member_word} not fully donated:")
    lines.append("")
    for m in not_donated:
        lines.append(f"• {m['character_name']}")
    lines.append("")
    lines.append(f"About {hours} hours until reset, please get your donations in.")
    lines.append("Need resources? Reach out for help!")
    return {
        "title": "Daily Donation Reminder",
        "description": "\n".join(lines),
        "color": 0x958AEA,
        "footer": {"text": "SleepingForest • DegenIdle"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

def main():
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DONATIONS_WEBHOOK_URL")
    discord_map = load_members()
    try:
        fresh_token = refresh_access_token()
    except Exception as e:
        raise
    headers = {
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
        "authorization": f"Bearer {fresh_token}",
        "user-agent": "Mozilla/5.0"
    }
    r = requests.get(DONATIONS_URL, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("success"):
        return
    members = data.get("byMember", [])
    not_donated = [m for m in members if m["count"] < FULL_DONATION_COUNT]
    if not not_donated:
        return
    used = data.get("used", 0)
    cap = data.get("cap", 0)
    mentions = []
    for m in not_donated:
        discord_id = discord_map.get(m["character_name"], "")
        if discord_id:
            mentions.append(f"<@{discord_id}>")
    content = " ".join(mentions) if mentions else ""
    payload = {
        "username": "SleepingForest Watch",
        "content": content,
        "embeds": [build_embed(not_donated, used, cap, discord_map)],
        "allowed_mentions": {"parse": ["users"]}
    }
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()

if __name__ == "__main__":
    main()
