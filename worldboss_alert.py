import os
import re
import json
import time
import requests
from datetime import datetime, timezone

SCHEDULE_URL = "https://api-v1.degenidle.com/api/worldboss/schedule?limit=5"
STATE_FILE = "worldboss_state.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
ROLE_ID = os.environ.get("WORLD_BOSS_ROLE_ID", "").strip()

SEND_AT_MINUTES_BEFORE_SPAWN = 5
LOOKAHEAD_MINUTES = 10

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

def load_state():
    if not os.path.exists(STATE_FILE):
        return {"sent": []}
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"sent": []}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def normalize_dt(dt_str):
    s = dt_str.replace(" ", "T")
    if re.search(r"[+-]\d{2}$", s):
        s += ":00"
    return datetime.fromisoformat(s)

def seconds_until_spawn(event):
    spawn_at = normalize_dt(event["scheduled_time"])
    now = datetime.now(timezone.utc)
    return (spawn_at - now).total_seconds()

def spawn_unix(event):
    return int(normalize_dt(event["scheduled_time"]).timestamp())

def build_embed(event):
    boss = event["boss"]
    unix_ts = spawn_unix(event)
    scheduled_iso = normalize_dt(event["scheduled_time"]).astimezone(timezone.utc).isoformat()

    return {
        "title": "Bossing Alert",
        "description": (
            f"**{boss['name']}**\n"
            f"Level {boss['level']}\n"
            f"Location: {boss['location']}\n\n"
            f"Spawns: <t:{unix_ts}:R>"
        ),
        "color": 0x72AEED,
        "image": {"url": boss["image_url"]},
        "footer": {"text": "SleepingForest • DegenIdle"},
        "timestamp": scheduled_iso
    }

def send_webhook(event):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL")

    content = ""
    allowed_mentions = {"parse": []}

    if ROLE_ID:
        content = f"<@&{ROLE_ID}>"
        allowed_mentions = {"roles": [ROLE_ID]}

    payload = {
        "username": "SleepingForest Watch",
        "content": content,
        "embeds": [build_embed(event)],
        "allowed_mentions": allowed_mentions
    }

    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()

def main():
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL")

    fresh_token = refresh_access_token()
    headers = {
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
        "authorization": f"Bearer {fresh_token}",
        "user-agent": "Mozilla/5.0"
    }

    state = load_state()
    sent = set(state.get("sent", []))

    r = requests.get(SCHEDULE_URL, headers=headers, timeout=20)
    r.raise_for_status()
    payload = r.json()

    if not payload.get("success"):
        return

    events = payload.get("data") or []
    if not events:
        return

    best_event = None
    best_spawn_seconds = None

    for event in events:
        spawn_seconds = seconds_until_spawn(event)
        if spawn_seconds <= 0:
            continue

        if spawn_seconds <= LOOKAHEAD_MINUTES * 60:
            if best_spawn_seconds is None or spawn_seconds < best_spawn_seconds:
                best_event = event
                best_spawn_seconds = spawn_seconds

    if not best_event:
        state["sent"] = list(sent)[-200:]
        save_state(state)
        return

    event_id = best_event["id"]
    spawn_key = f"{event_id}:spawn"

    if spawn_key in sent:
        state["sent"] = list(sent)[-200:]
        save_state(state)
        return

    target_send_seconds_before_spawn = SEND_AT_MINUTES_BEFORE_SPAWN * 60
    wait_seconds = best_spawn_seconds - target_send_seconds_before_spawn

    if wait_seconds > 0:
        time.sleep(wait_seconds)

    refreshed_spawn_seconds = seconds_until_spawn(best_event)

    if refreshed_spawn_seconds > 0:
        send_webhook(best_event)
        sent.add(spawn_key)

    state["sent"] = list(sent)[-200:]
    save_state(state)

if __name__ == "__main__":
    main()


