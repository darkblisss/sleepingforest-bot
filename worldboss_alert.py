import os
import re
import json
import time
import base64
import requests
from datetime import datetime, timezone
from nacl import encoding, public

SCHEDULE_URL = "https://api-v1.degenidle.com/api/worldboss/schedule?limit=5"
STATE_FILE = "worldboss_state.json"

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
ROLE_ID = os.environ.get("WORLD_BOSS_ROLE_ID", "").strip()
ERROR_WEBHOOK_URL = os.environ.get("ERROR_WEBHOOK_URL", "").strip()
GH_PAT = os.environ.get("GH_PAT", "").strip()
GH_REPO = "darkblisss/worldboss-bot"
RENDER_API_KEY = os.environ.get("RENDER_API_KEY", "").strip()
RENDER_SERVICE_ID = os.environ.get("RENDER_SERVICE_ID", "").strip()

SEND_AT_MINUTES_BEFORE_SPAWN = 5
LOOKAHEAD_MINUTES = 10

def send_error_alert(message):
    if ERROR_WEBHOOK_URL:
        try:
            requests.post(
                ERROR_WEBHOOK_URL,
                json={"content": f"⚠️ Worldboss Bot Error: {message}"},
                timeout=10,
            )
        except Exception:
            pass


def update_render_secret(new_refresh_token, token_changed=True):
    if not RENDER_API_KEY or not RENDER_SERVICE_ID or not new_refresh_token:
        return
    headers = {"Authorization": f"Bearer {RENDER_API_KEY}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        r = requests.get(f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars", headers=headers, timeout=10)
        r.raise_for_status()
        current = r.json()
        updated = []
        found = False
        for item in current:
            ev = item.get("envVar", {})
            if ev.get("key") == "DEGEN_REFRESH_TOKEN":
                updated.append({"key": "DEGEN_REFRESH_TOKEN", "value": new_refresh_token})
                found = True
            else:
                updated.append({"key": ev["key"], "value": ev.get("value", "")})
        if not found:
            updated.append({"key": "DEGEN_REFRESH_TOKEN", "value": new_refresh_token})
        put_r = requests.put(f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars", headers=headers, json=updated, timeout=10)
        put_r.raise_for_status()
        print("✅ Render env var synced")
    except Exception as e:
        send_error_alert(f"⚠️ Failed to sync DEGEN_REFRESH_TOKEN to Render: {e}")

def update_github_secret(new_refresh_token):
    if not GH_PAT or not new_refresh_token:
        send_error_alert("⛔ GH_PAT or new_refresh_token missing — secret NOT updated, chain will break in 24h")
        return
    repos = ["darkblisss/worldboss-bot", "darkblisss/donations-bot", "darkblisss/guild-activity-checker"]
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
            timeout=20,
        )
        r.raise_for_status()
    except Exception as e:
        send_error_alert(
            f"⛔ TOKEN EXPIRED — manually update DEGEN_REFRESH_TOKEN in GitHub Secrets for BOTH repos NOW. "
            f"You have ~24h before donations bot also fails. Error: {e}"
        )
        raise
    data = r.json()
    new_refresh = data.get("refresh_token")
    if new_refresh:
        print(f"[TOKEN] New refresh token ending in: ...{new_refresh[-4:]}")
        same = new_refresh == refresh_token
        print(f"[TOKEN] Token changed: {'NO — SAME TOKEN, rotation may be broken!' if same else 'YES — token rotated successfully'}")
        update_github_secret(new_refresh)
        update_render_secret(new_refresh, token_changed=not same)
    else:
        send_error_alert("⛔ DEGEN did not return a new refresh_token — rotation will break within 24h")
    return data["access_token"]

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
    scheduled_iso = normalize_dt(event["scheduled_time"]).astimezone(
        timezone.utc
    ).isoformat()
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
        "timestamp": scheduled_iso,
    }

def send_webhook(event):
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL")
    content = ""
    allowed_mentions = {"parse": []}
    boss_level = event["boss"]["level"]
    if ROLE_ID and boss_level <= 30:
        content = f"<@&{ROLE_ID}>"
        allowed_mentions = {"roles": [ROLE_ID]}
    payload = {
        "username": "SleepingForest Watch",
        "content": content,
        "embeds": [build_embed(event)],
        "allowed_mentions": allowed_mentions,
    }
    r = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=20)
    r.raise_for_status()

def main():
    if not DISCORD_WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_WEBHOOK_URL")
    try:
        fresh_token = refresh_access_token()
    except Exception as e:
        raise
    headers = {
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
        "authorization": f"Bearer {fresh_token}",
        "user-agent": "Mozilla/5.0",
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
