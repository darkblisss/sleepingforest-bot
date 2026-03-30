import os
import json
import requests
from datetime import datetime, timezone, timedelta

DONATIONS_URL = "https://api-v1.degenidle.com/api/guilds/d08f77ef-fc13-4781-adce-0fcf88f9f77b/donations/daily?day=today&characterId=ee938e63-72e6-4b8e-82bf-672ca6e0a568"
DISCORD_WEBHOOK_URL = os.environ.get("DONATIONS_WEBHOOK_URL", "").strip()
FULL_DONATION_COUNT = 20
MEMBERS_FILE = "members.json"

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
    return r.json()["access_token"]

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

    fresh_token = refresh_access_token()
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
