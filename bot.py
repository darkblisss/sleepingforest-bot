import os
import re
import json
import time
import base64
import random
import asyncio
import requests
import threading
from datetime import datetime, timezone, time as dtime, timedelta
from nacl import encoding, public as nacl_public
import discord
from flask import Flask, request as flask_request
from discord.ext import tasks

# ── Constants ────────────────────────────────────────────────────────────────────────────
DEGEN_GUILD_ID  = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID         = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE            = "https://api-v1.degenidle.com/api"
CLIENT_ID       = "c9563b2ef30348f182e122030ef28ad7"
GUILD_LEADER    = "Bloss"
OWNER_ID        = "237324092569681921"
MEMBERS_FILE    = "members.json"
SNAPSHOT_FILE   = "snapshots.json"
WB_STATE_FILE   = "worldboss_state.json"
WB_SCHEDULE_URL = "https://api-v1.degenidle.com/api/worldboss/schedule?limit=5"
WB_SEND_MINUTES_BEFORE = 5
WB_LOOKAHEAD_MINUTES   = 20
MAX_DONATIONS_PER_DAY  = 25
SKILLS = [
    "mining", "woodcutting", "tracking", "fishing", "gathering",
    "herbalism", "forging", "leatherworking", "tailoring", "crafting",
    "cooking", "alchemy", "combat", "woodcrafting", "dungeoneering",
    "bloomtide", "bossing", "exorcism", "tinkering"
]
GH_REPOS = [
    "darkblisss/sleepingforest-bot",
]

MEMBERS_ROLE_ID  = "1487294472478785536"
ADMIN_ROLE_ID    = "1487296175756410961"
OFFICER_ROLE_ID  = "1487294633150251089"

# ── Env vars ───────────────────────────────────────────────────────────────────────────
BOT_TOKEN             = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID      = os.environ.get("DISCORD_GUILD_ID", "").strip()
DONATIONS_ROLE_ID     = os.environ.get("DONATIONS_ROLE_ID", "").strip()
GIVEAWAY_WEBHOOK_URL  = os.environ.get("DISCORD_GIVEAWAY_WEBHOOK", "").strip()
DONATIONS_WEBHOOK_URL = os.environ.get("DONATIONS_WEBHOOK_URL", "").strip()
LOGS_WEBHOOK_URL      = os.environ.get("DISCORD_LOGS_WEBHOOK", "").strip()
WB_WEBHOOK_URL        = os.environ.get("DISCORD_WB_WEBHOOK", "").strip()
ACTIVITY_WEBHOOK_URL  = os.environ.get("DISCORD_ACTIVITY_WEBHOOK", "").strip()
ERROR_WEBHOOK_URL     = os.environ.get("ERROR_WEBHOOK_URL", "").strip()
GH_PAT                = os.environ.get("GH_PAT", "").strip()
RENDER_API_KEY        = os.environ.get("RENDER_API_KEY", "").strip()
RENDER_SERVICE_ID     = os.environ.get("RENDER_SERVICE_ID", "").strip()
WB_ROLE_ID            = os.environ.get("WORLD_BOSS_ROLE_ID", "").strip()
RELOAD_TOKEN_SECRET   = os.environ.get("RELOAD_TOKEN_SECRET", "").strip()
RAID_WEBHOOK_URL      = os.environ.get("RAID_WEBHOOK_URL", "").strip()
RAID_ROLE_ID          = os.environ.get("RAID_ROLE_ID", "").strip()

DAILY_DONATIONS_URL  = f"{BASE}/guilds/{DEGEN_GUILD_ID}/donations/daily?day=today&characterId={CHAR_ID}"
WEEKLY_DONATIONS_URL = f"{BASE}/guilds/{DEGEN_GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL        = f"{BASE}/guilds/{DEGEN_GUILD_ID}/resources?characterId={CHAR_ID}"
GUILD_API            = f"{BASE}/guilds/character/{CHAR_ID}"
PROFILE_API          = f"{BASE}/characters/profile/{{name}}"
LEADERBOARD_API      = f"{BASE}/guilds/{DEGEN_GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"

# ── Fixed UTC schedule times ──────────────────────────────────────────────────────────────────────────
GIVEAWAY_TIME  = dtime(hour=0,  minute=0, tzinfo=timezone.utc)
DONATIONS_TIME = dtime(hour=18, minute=0, tzinfo=timezone.utc)
ACTIVITY_TIMES = [
    dtime(hour=0, minute=0, tzinfo=timezone.utc),
]

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Client(intents=intents)
last_run_week = None

# ── Flask keep-alive ──────────────────────────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "OK", 200

@flask_app.route("/reload-token", methods=["POST"])
def reload_token():
    secret = flask_request.headers.get("X-Reload-Secret", "")
    if not RELOAD_TOKEN_SECRET or secret != RELOAD_TOKEN_SECRET:
        return {"error": "unauthorized"}, 401
    data = flask_request.get_json(silent=True) or {}
    new_token = data.get("token", "").strip()
    if not new_token or len(new_token) < 20:
        return {"error": "invalid token"}, 400
    old_token = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
    if new_token == old_token:
        return {"status": "unchanged"}, 200
    os.environ["DEGEN_REFRESH_TOKEN"] = new_token
    expires_unix = int(time.time()) + 86400
    os.environ["TOKEN_EXPIRES_UNIX"] = str(expires_unix)
    _post_token_log(new_token, expires_in=86400)
    print(f"[RELOAD] Token hot-reloaded via webhook: ...{new_token[-4:]}")
    return {"status": "updated", "ending": new_token[-4:]}, 200

def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()

# ── Error alert ─────────────────────────────────────────────────────────────────────────────────
def send_error_alert(message):
    if ERROR_WEBHOOK_URL:
        try:
            requests.post(ERROR_WEBHOOK_URL, json={"content": f"SleepingForest Bot Error: {message}"}, timeout=10)
        except Exception:
            pass

# ── Token helpers ──────────────────────────────────────────────────────────────────────────────
def _post_token_log(new_token, expires_in=86400):
    expires_unix = int(time.time()) + expires_in
    os.environ["TOKEN_EXPIRES_UNIX"] = str(expires_unix)
    _save_env_to_render("TOKEN_EXPIRES_UNIX", str(expires_unix))
    if not LOGS_WEBHOOK_URL:
        return
    try:
        requests.post(LOGS_WEBHOOK_URL, json={
            "username": "SleepingForest Log",
            "embeds": [{
                "title": "Token Refreshed",
                "description": (
                    f"The DegenIdle refresh token has been rotated successfully.\n"
                    f"**New token ending:** `...{new_token[-4:]}`\n\n"
                    f"Expires approximately: <t:{expires_unix}:R> (<t:{expires_unix}:f>)"
                ),
                "color": 0x01696f,
                "footer": {"text": "SleepingForest \u2022 Token Rotation"}
            }]
        }, timeout=10)
    except Exception:
        pass

def _save_env_to_render(key, value):
    if not RENDER_API_KEY or not RENDER_SERVICE_ID:
        return
    headers = {"Authorization": f"Bearer {RENDER_API_KEY}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        r = requests.get(f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars", headers=headers, timeout=10)
        r.raise_for_status()
        updated = []
        found = False
        for item in r.json():
            ev = item.get("envVar", {})
            if ev.get("key") == key:
                updated.append({"key": key, "value": value})
                found = True
            else:
                updated.append({"key": ev["key"], "value": ev.get("value", "")})
        if not found:
            updated.append({"key": key, "value": value})
        requests.put(f"https://api.render.com/v1/services/{RENDER_SERVICE_ID}/env-vars", headers=headers, json=updated, timeout=10).raise_for_status()
        print(f"[Render] {key} updated")
    except Exception as e:
        print(f"[Render] Failed to update {key}: {e}")

def _save_token_to_github(new_token):
    if not GH_PAT:
        return
    for repo in GH_REPOS:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers={"Authorization": f"Bearer {GH_PAT}"}, timeout=10)
            r.raise_for_status()
            key_data = r.json()
            pub_key = nacl_public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder)
            encrypted = base64.b64encode(nacl_public.SealedBox(pub_key).encrypt(new_token.encode())).decode()
            requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/DEGEN_REFRESH_TOKEN", headers={"Authorization": f"Bearer {GH_PAT}"}, json={"encrypted_value": encrypted, "key_id": key_data["key_id"]}, timeout=10)
            print(f"[TOKEN] GitHub secret updated: {repo}")
        except Exception as e:
            print(f"[TOKEN] GitHub update failed for {repo}: {e}")

def get_access_token():
    refresh_token = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise RuntimeError("Missing DEGEN_REFRESH_TOKEN")
    print(f"[TOKEN] Using refresh token ending in: ...{refresh_token[-4:]}")
    try:
        r = requests.post(
            "https://auth.degenidle.com/oauth2/token",
            data={"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": refresh_token},
            timeout=20
        )
        r.raise_for_status()
    except Exception as e:
        send_error_alert(f"TOKEN EXPIRED - run !settoken with a fresh token. Error: {e}")
        raise
    data = r.json()
    new_refresh = data.get("refresh_token")
    if new_refresh:
        print(f"[TOKEN] New refresh token ending in: ...{new_refresh[-4:]}")
        changed = new_refresh != refresh_token
        print(f"[TOKEN] Token changed: {'YES - rotated successfully' if changed else 'NO - same token'}")
        if changed:
            os.environ["DEGEN_REFRESH_TOKEN"] = new_refresh
            _save_env_to_render("DEGEN_REFRESH_TOKEN", new_refresh)
            _save_token_to_github(new_refresh)
            _post_token_log(new_refresh, expires_in=data.get("expires_in", 86400))
    else:
        send_error_alert("No new refresh_token returned - rotation will break within 24h")
    return data["access_token"]

def check_token_expiry():
    stored_refresh = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
    if not stored_refresh:
        return None, None, None, "No DEGEN_REFRESH_TOKEN set."
    try:
        r = requests.post(
            "https://auth.degenidle.com/oauth2/token",
            data={"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": stored_refresh},
            timeout=20
        )
        r.raise_for_status()
    except Exception as e:
        return None, None, None, f"Token request failed: {e}"
    data = r.json()
    expires_in = data.get("expires_in", 0)
    expires_unix = int(time.time()) + expires_in
    returned_refresh = data.get("refresh_token", "")
    rotated = bool(returned_refresh and returned_refresh != stored_refresh)
    new_ending = f"...{returned_refresh[-4:]}" if returned_refresh else "(none returned)"
    stored_ending = f"...{stored_refresh[-4:]}"
    return expires_unix, rotated, (stored_ending, new_ending), None

def make_headers(access_token):
    return {
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
        "authorization": f"Bearer {access_token}",
        "user-agent": "Mozilla/5.0"
    }

# ── Members helpers ────────────────────────────────────────────────────────────────────────────────
def load_members():
    if not os.path.exists(MEMBERS_FILE):
        return {}
    with open(MEMBERS_FILE, "r") as f:
        return json.load(f)

def save_members(data):
    with open(MEMBERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def push_members_to_github():
    if not GH_PAT:
        return
    try:
        members_data = open(MEMBERS_FILE, "r").read()
        encoded = base64.b64encode(members_data.encode()).decode()
        headers = {"Authorization": f"Bearer {GH_PAT}", "Accept": "application/vnd.github+json"}
        r = requests.get("https://api.github.com/repos/darkblisss/sleepingforest-bot/contents/members.json", headers=headers, timeout=10)
        sha = r.json().get("sha", "") if r.status_code == 200 else ""
        payload = {"message": "chore: auto-update members.json [skip ci]", "content": encoded}
        if sha:
            payload["sha"] = sha
        requests.put("https://api.github.com/repos/darkblisss/sleepingforest-bot/contents/members.json", headers=headers, json=payload, timeout=10)
        print("[GitHub] members.json pushed")
    except Exception as e:
        print(f"[GitHub] Push error: {e}")

def push_snapshots_to_github(snapshots):
    if not GH_PAT:
        return
    try:
        encoded = base64.b64encode(json.dumps(snapshots, indent=2).encode()).decode()
        headers = {"Authorization": f"Bearer {GH_PAT}", "Accept": "application/vnd.github+json"}
        r = requests.get("https://api.github.com/repos/darkblisss/sleepingforest-bot/contents/snapshots.json", headers=headers, timeout=10)
        sha = r.json().get("sha", "") if r.status_code == 200 else ""
        payload = {"message": "chore: auto-update snapshots.json [skip ci]", "content": encoded}
        if sha:
            payload["sha"] = sha
        requests.put("https://api.github.com/repos/darkblisss/sleepingforest-bot/contents/snapshots.json", headers=headers, json=payload, timeout=10)
        print("[GitHub] snapshots.json pushed")
    except Exception as e:
        print(f"[GitHub] snapshots push error: {e}")

def has_admin_role(member):
    if not member or not hasattr(member, "roles"):
        return False
    return any(str(r.id) == ADMIN_ROLE_ID for r in member.roles)

def has_officer_role(member):
    if not member or not hasattr(member, "roles"):
        return False
    return any(str(r.id) in (OFFICER_ROLE_ID, ADMIN_ROLE_ID) for r in member.roles)

def has_members_role(member):
    if not member or not hasattr(member, "roles"):
        return False
    return any(str(r.id) == MEMBERS_ROLE_ID for r in member.roles)

# ── Guild API helpers ─────────────────────────────────────────────────────────────────────────────
def get_guild_member_names(headers):
    try:
        r = requests.get(GUILD_API, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        return [m["character_name"] for m in (data.get("members") or []) if m.get("character_name")]
    except Exception:
        return []

def get_daily_limit(headers):
    try:
        r = requests.get(RESOURCES_URL, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "daily_limit" in data:
            return data["daily_limit"]
        if isinstance(data, list):
            for item in data:
                if "daily_limit" in item:
                    return item["daily_limit"]
    except Exception as e:
        print(f"[Resources] Failed to fetch daily_limit: {e}")
    return MAX_DONATIONS_PER_DAY

def get_weekly_donations(headers):
    r = requests.get(WEEKLY_DONATIONS_URL, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict):
        for key in ["byMember", "members", "data", "leaderboard", "results", "players"]:
            if key in data and isinstance(data[key], list):
                return data[key]
        return []
    return data if isinstance(data, list) else []

def get_player_name(player):
    for field in ["character_name", "name", "characterName", "username", "displayName"]:
        if player.get(field):
            return player[field]
    return "Unknown"

def get_role_member_ids():
    if not DISCORD_GUILD_ID or not DONATIONS_ROLE_ID or not BOT_TOKEN:
        return None
    headers = {"Authorization": f"Bot {BOT_TOKEN}"}
    members = []
    after = None
    while True:
        url = f"https://discord.com/api/v10/guilds/{DISCORD_GUILD_ID}/members?limit=1000"
        if after:
            url += f"&after={after}"
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        batch = r.json()
        if not batch:
            break
        members.extend(batch)
        if len(batch) < 1000:
            break
        after = batch[-1]["user"]["id"]
    return {m["user"]["id"] for m in members if DONATIONS_ROLE_ID in m["roles"]}

# ── Daily donations reminder ──────────────────────────────────────────────────────────────────
def hours_until_reset():
    now = datetime.now(timezone.utc)
    midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return round((midnight - now).total_seconds() / 3600)

def run_donations_reminder(webhook_override=None):
    print(f"\n-- Donations Reminder @ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} --")
    target_wh = webhook_override or DONATIONS_WEBHOOK_URL
    if not target_wh:
        print("[Donations] No webhook URL set.")
        return
    try:
        token = get_access_token()
        hdrs = make_headers(token)
        discord_map = load_members()
        per_person_cap = get_daily_limit(hdrs)
        print(f"[Donations] Per-person daily cap: {per_person_cap}")
        r = requests.get(DAILY_DONATIONS_URL, headers=hdrs, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        send_error_alert(f"Donations reminder failed: {e}")
        return

    if not data.get("success"):
        print("[Donations] API returned success=false")
        return

    members = data.get("byMember", [])
    guild_cap = data.get("cap", 0)
    used = data.get("used", 0)
    not_donated = [m for m in members if m.get("count", 0) < per_person_cap]

    if not not_donated:
        print("[Donations] Everyone has hit the cap today.")
        return

    percent = round((used / guild_cap) * 100) if guild_cap > 0 else 0
    hours = hours_until_reset()
    count = len(not_donated)
    member_word = "member" if count == 1 else "members"

    lines = [
        f"Total donations today: {used}/{guild_cap} ({percent}%)",
        f"{count} {member_word} not fully donated:",
        "",
    ]
    for m in not_donated:
        lines.append(f"\u2022 {m['character_name']}")
    lines.append("")
    lines.append(f"About {hours} hours until reset, please get your donations in.")
    lines.append("**Need resources? Reach out for help!**")

    mentions = []
    for m in not_donated:
        discord_id = discord_map.get(m["character_name"], "")
        if discord_id:
            mentions.append(f"<@{discord_id}>")

    payload = {
        "username": "SleepingForest Watch",
        "content": " ".join(mentions) if mentions else "",
        "embeds": [{
            "title": "Daily Donation Reminder",
            "description": "\n".join(lines),
            "color": 0x958AEA,
            "footer": {"text": "SleepingForest \u2022 DegenIdle"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }],
        "allowed_mentions": {"parse": ["users"]}
    }
    requests.post(target_wh, json=payload, timeout=15)
    print(f"[Donations] Reminder posted - {count} member(s) haven't hit cap ({per_person_cap}) today.")

@tasks.loop(time=DONATIONS_TIME)
async def donations_loop():
    await asyncio.get_running_loop().run_in_executor(None, run_donations_reminder)

# ── Giveaway ──────────────────────────────────────────────────────────────────────────────────────
def get_week_ending():
    now = datetime.now(timezone.utc)
    days_since_sunday = (now.weekday() + 1) % 7
    last_sunday = now - timedelta(days=days_since_sunday)
    return last_sunday.strftime("%b %d %Y")

def find_eligible(donations, daily_limit, discord_map, role_ids):
    if role_ids is None:
        return [], 0, ["[CRITICAL] Role list failed - aborting"]
    logs = []
    leader_count = next((p.get("count", 0) for p in donations if get_player_name(p) == GUILD_LEADER), None)
    threshold = leader_count if leader_count is not None else daily_limit * 7
    logs.append(f"[GIVEAWAY] Threshold ({GUILD_LEADER}): {threshold}")
    eligible = []
    for player in donations:
        name = get_player_name(player)
        count = player.get("count", 0)
        if name != GUILD_LEADER and count < threshold:
            logs.append(f"[GIVEAWAY] {name}: {count} - below threshold")
            continue
        discord_id = discord_map.get(name, "")
        if not discord_id:
            logs.append(f"[GIVEAWAY] {name}: not in members.json")
            continue
        if discord_id not in role_ids:
            logs.append(f"[GIVEAWAY] {name}: missing donations role")
            continue
        logs.append(f"[GIVEAWAY] {name}: {count} - eligible")
        eligible.append({"name": name, "count": count})
    return sorted(eligible, key=lambda x: x["count"], reverse=True), threshold, logs

def run_giveaway_logic(test_mode=False):
    discord_map = load_members()
    token = get_access_token()
    headers = make_headers(token)
    daily_limit = get_daily_limit(headers)
    donations = get_weekly_donations(headers)
    role_ids = get_role_member_ids()
    eligible, threshold, logs = find_eligible(donations, daily_limit, discord_map, role_ids)
    if threshold == 0:
        return "aborted: role check failed", logs
    week_ending = get_week_ending()
    footer_text = f"Week ending {week_ending} \u2022 Daily limit: {daily_limit}"
    target_webhook = LOGS_WEBHOOK_URL if test_mode else GIVEAWAY_WEBHOOK_URL
    if not eligible:
        requests.post(target_webhook, json={"username": "SleepingForest Giveaway", "embeds": [{
                "title": "Weekly Donations Giveaway",
                "description": "No winner this week.\nNobody hit the donation cap every day.\nBetter luck next week!",
                "color": 0x958AEA,
                "footer": {"text": footer_text},
                "timestamp": datetime.now(timezone.utc).isoformat()
            }]}, timeout=15).raise_for_status()
        return "posted: no eligible players", logs
    winner = random.choice(eligible)
    count = len(eligible)
    winner_discord_id = discord_map.get(winner["name"], "")
    mention = f"<@{winner_discord_id}>" if winner_discord_id else winner["name"]
    payload = {"username": "SleepingForest Giveaway", "embeds": [{
        "title": "Weekly Donations Giveaway",
        "description": (
            f"Congratulations {mention}, you won this week's giveaway!\n"
            f"{count} {'player' if count == 1 else 'players'} hit the donation cap this week.\nKeep it up!"
        ),
        "color": 0x958AEA,
        "footer": {"text": footer_text},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }]}
    if winner_discord_id and not test_mode:
        payload["content"] = mention
        payload["allowed_mentions"] = {"parse": [], "users": [winner_discord_id]}
    requests.post(target_webhook, json=payload, timeout=15).raise_for_status()
    return f"posted: winner is {winner['name']}", logs

@tasks.loop(time=GIVEAWAY_TIME)
async def weekly_giveaway():
    global last_run_week
    now = datetime.now(timezone.utc)
    if now.weekday() != 0:
        return
    week_key = now.strftime("%Y-W%W")
    if week_key == last_run_week:
        return
    last_run_week = week_key
    await asyncio.get_running_loop().run_in_executor(None, run_giveaway_logic)

# ── Member link/unlink ────────────────────────────────────────────────────────────────────────────
def do_link(discord_id, ingame_name_input):
    try:
        token = get_access_token()
        headers = make_headers(token)
        guild_names = get_guild_member_names(headers)
    except Exception as e:
        return f"Could not fetch guild members: {e}"
    matched_name = next((n for n in guild_names if n.lower() == ingame_name_input.lower()), None)
    if not matched_name:
        return f"No guild member found matching **{ingame_name_input}**."
    members = load_members()
    members = {k: v for k, v in members.items() if v != discord_id}
    members[matched_name] = discord_id
    save_members(members)
    push_members_to_github()
    return f"Linked **{matched_name}** to your Discord. Welcome!"

def do_self_unlink(discord_id):
    members = load_members()
    matched_key = next((k for k, v in members.items() if v == discord_id), None)
    if not matched_key:
        return "You are not currently linked to any in-game name."
    del members[matched_key]
    save_members(members)
    push_members_to_github()
    return f"Unlinked **{matched_key}** from your account."

def do_force_unlink(ingame_name_input):
    members = load_members()
    matched_key = next((k for k in members if k.lower() == ingame_name_input.lower()), None)
    if not matched_key:
        return f"No entry found for **{ingame_name_input}**."
    del members[matched_key]
    save_members(members)
    push_members_to_github()
    return f"Unlinked **{matched_key}** and cleared from records."

def do_members_list():
    members = load_members()
    if not members:
        return "members.json is empty."
    lines = [f"**{name}** -> <@{did}>" for name, did in sorted(members.items())]
    return f"**Linked members ({len(lines)}):**\n" + "\n".join(lines)

# ── Boss stats (raid boss, NOT world boss) ──────────────────────────────────────────────────────
def fetch_boss_raid(offset=0):
    token = get_access_token()
    h = make_headers(token)
    hist = requests.get(f"{BASE}/guild-worldboss/{DEGEN_GUILD_ID}/history?limit={offset+1}&offset=0", headers=h, timeout=15).json()
    raids = hist.get("data", [])
    if len(raids) <= offset:
        return None, None, {}
    raid = raids[offset]
    lb = requests.get(f"{BASE}/guild-worldboss/leaderboard/{raid['id']}?characterId={CHAR_ID}", headers=h, timeout=15).json()
    try:
        guild_data = requests.get(GUILD_API, headers=h, timeout=15).json()
        members = {m["character_id"]: m["character_name"] for m in (guild_data.get("members") or []) if m.get("character_id") and m.get("character_name")}
    except Exception:
        members = {}
    return raid, lb, members

def build_boss_embed(raid, lb, members=None):
    boss = raid["boss"]
    entries = lb.get("data", [])
    total = float(raid["total_damage_dealt"]) if raid["total_damage_dealt"] else 0.0
    hp_remaining_pct = max(0.0, 100.0 - (total / boss["max_hp"] * 100 if boss["max_hp"] else 0.0))
    defeated = raid["boss_defeated"]

    def fmt(n):
        if n is None: return "N/A"
        n = float(n)
        if n >= 1_000_000: return f"{round(n/1_000_000,2)}M"
        if n >= 1_000: return f"{round(n/1_000,1)}K"
        return str(round(n))

    secs = None
    if raid["spawn_time"] and raid["end_time"]:
        s = datetime.fromisoformat(raid["spawn_time"].replace(" ", "T").split("+")[0] + "+00:00")
        e = datetime.fromisoformat(raid["end_time"].replace(" ", "T").split("+")[0] + "+00:00")
        secs = max(1, (e - s).total_seconds())
    dur = (lambda m, sc: f"{m}m {sc}s" if m else f"{sc}s")(*divmod(int(secs), 60)) if secs else "N/A"
    dt = datetime.fromisoformat(raid["scheduled_time"].replace(" ", "T").split("+")[0] + "+00:00")
    date_str = dt.strftime("%d %b %Y")
    init_id = raid.get("initiator_character_id", "")
    init_name = (members or {}).get(init_id) or next((e["character_name"] for e in entries if e.get("character_id") == init_id), "Unknown")

    def bar(pct, w=14):
        f = int(pct / 100 * w)
        return chr(9608)*f + chr(9617)*(w-f)

    hp_val = f"`[{bar(hp_remaining_pct)}]` **{round(hp_remaining_pct,1)}% HP remaining**\n{fmt(boss['max_hp'] - total)} HP left"
    if not entries:
        part_val = "Nobody joined this raid."
    else:
        lines = []
        for i, e in enumerate(entries):
            dps = e["damage_dealt"] / secs if secs else 0
            rank = ["#1","#2","#3"][i] if i < 3 else f"#{i+1}"
            lines.append(f"{rank} **{e['character_name']}**  \u00b7  {fmt(e['damage_dealt'])} DMG  \u00b7  {fmt(dps)}/s  \u00b7  {round(e['percentage'],1)}%")
        part_val = "\n".join(lines)

    return {
        "title": f"{boss['name']} Lv.{boss['level']} - Raid Report",
        "description": f"{'DEFEATED' if defeated else 'SURVIVED'} **{'BOSS DEFEATED' if defeated else 'Boss Survived'}**",
        "color": 0x2ECC71 if defeated else 0xB43232,
        "fields": [
            {"name": "Date", "value": date_str, "inline": True},
            {"name": "Duration", "value": dur, "inline": True},
            {"name": "Initiated by", "value": f"**{init_name}**", "inline": True},
            {"name": "Boss HP", "value": hp_val, "inline": False},
            {"name": f"Participants ({len(entries)})", "value": part_val, "inline": False},
        ],
        "footer": {"text": f"SleepingForest  \u00b7  {boss['name']} Lv.{boss['level']}"},
    }

# ── Post raid boss stats to raid channel after spawn ──────────────────────────────────────────────
def _post_raid_boss_stats_after_spawn():
    """Called after a raid boss spawns. Fetches the latest raid and posts to the raid channel."""
    target_wh = RAID_WEBHOOK_URL or LOGS_WEBHOOK_URL
    if not target_wh:
        print("[RaidBoss] No RAID_WEBHOOK_URL or LOGS_WEBHOOK_URL set - skipping post-spawn stats")
        return
    try:
        raid, lb, members = fetch_boss_raid(0)
        if not raid:
            print("[RaidBoss] No raid history found for post-spawn stats")
            return
        content = ""
        allowed_mentions = {"parse": []}
        if RAID_ROLE_ID:
            content = f"<@&{RAID_ROLE_ID}>"
            allowed_mentions = {"roles": [RAID_ROLE_ID]}
        requests.post(
            target_wh,
            json={
                "username": "SleepingForest Raids",
                "content": content,
                "embeds": [build_boss_embed(raid, lb, members)],
                "allowed_mentions": allowed_mentions,
            },
            timeout=15,
        ).raise_for_status()
        print("[RaidBoss] Post-spawn stats posted to raid channel")
    except Exception as e:
        print(f"[RaidBoss] Failed to post post-spawn stats: {e}")
        send_error_alert(f"RaidBoss post-spawn stats failed: {e}")

# ── Activity checker ──────────────────────────────────────────────────────────────────────────────
def parse_joined_at(s):
    try:
        s = s.replace(" ", "T")
        if s.endswith("+00"): s += ":00"
        return datetime.fromisoformat(s)
    except Exception:
        return None

def format_avg_daily_donations(weekly_count, joined_at_str, daily_limit=None):
    cap = daily_limit or MAX_DONATIONS_PER_DAY
    try:
        joined = parse_joined_at(joined_at_str)
        if not joined: return "?"
        days = min((datetime.now(timezone.utc).date() - joined.date()).days + 1, 7)
        capped = min(weekly_count, days * cap)
        avg = capped / days
        return str(int(avg)) if avg == int(avg) else str(round(avg, 1))
    except Exception:
        return "?"

def get_character_skills(name, headers):
    try:
        r = requests.get(PROFILE_API.format(name=name), headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            skills = data["profile"]["skills"]
            return {skill: int(skills.get(skill, 0)) for skill in SKILLS}
    except Exception as e:
        print(f"[Activity] Could not fetch skills for {name}: {e}")
    return None

def load_snapshots():
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    return {}

def save_snapshots(snapshots):
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshots, f, indent=2)

def format_inactive_duration(iso_str):
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso_str)
        h, m = divmod(int(delta.total_seconds()), 3600)
        m = m // 60
        d = h // 24
        if h >= 240: return f"{d}d"
        if h >= 1: return f"{h}h {m}m"
        return f"{m}m"
    except Exception:
        return "unknown"

def time_since(iso_str):
    try:
        delta = datetime.now(timezone.utc) - datetime.fromisoformat(iso_str)
        h = int(delta.total_seconds() // 3600)
        m = int((delta.total_seconds() % 3600) // 60)
        return f"{h}h {m}m" if h > 0 else f"{m}m"
    except Exception:
        return "unknown"

def run_activity_check():
    print(f"\n-- Activity Check @ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} --")
    access_token = get_access_token()
    headers = make_headers(access_token)
    snapshots = load_snapshots()
    last_check_ts = snapshots.get("_last_run")
    daily_limit = get_daily_limit(headers)

    r = requests.get(GUILD_API, headers=headers, timeout=15)
    r.raise_for_status()
    members_raw = [{"name": m["character_name"], "joined_at": m.get("joined_at", "")} for m in (r.json().get("members") or []) if m.get("character_name")]

    donations_raw = requests.get(LEADERBOARD_API, headers=headers, timeout=15).json()
    donation_counts = {e["character_name"]: int(e.get("count") or 0) for e in (donations_raw.get("byMember") or []) if isinstance(e, dict) and e.get("character_name")}

    now_iso = datetime.now(timezone.utc).isoformat()
    new_snapshots = {"_last_run": now_iso}
    inactive = []
    is_first_run = not any(k != "_last_run" for k in snapshots)

    for member in members_raw:
        name = member["name"]
        skills = get_character_skills(name, headers)
        if skills is None:
            if name in snapshots: new_snapshots[name] = snapshots[name]
            continue
        prev = snapshots.get(name, {})
        prev_skills = prev.get("skills", {})
        prev_streak = prev.get("inactive_streak", 0)
        prev_last_active = prev.get("last_active_ts", now_iso)
        weekly_count = donation_counts.get(name, 0)
        avg_str = format_avg_daily_donations(weekly_count, member["joined_at"], daily_limit)
        try: avg_raw = float(avg_str)
        except Exception: avg_raw = 0.0
        if prev_skills:
            gained = any(skills.get(s, 0) > prev_skills.get(s, 0) for s in SKILLS)
            if gained:
                streak, last_active = 0, now_iso
            else:
                streak = prev_streak + 1
                last_active = prev_last_active
                inactive.append({"name": name, "streak": streak, "last_active_ts": last_active, "avg_donations": avg_str, "avg_donations_raw": avg_raw})
        else:
            streak, last_active = 0, now_iso
        new_snapshots[name] = {"skills": skills, "timestamp": now_iso, "inactive_streak": streak, "last_active_ts": last_active}

    save_snapshots(new_snapshots)
    push_snapshots_to_github(new_snapshots)

    if is_first_run:
        print("[Activity] First run - baselines saved.")
        return

    wh = ACTIVITY_WEBHOOK_URL or LOGS_WEBHOOK_URL
    if not wh:
        return

    since = time_since(last_check_ts) if last_check_ts else "first check"
    if inactive:
        inactive_sorted = sorted(inactive, key=lambda x: (x["last_active_ts"], x["avg_donations_raw"]))
        member_lines = [f"\u2022 {e['name']} ({format_inactive_duration(e['last_active_ts'])}) [{e['avg_donations']}]" for e in inactive_sorted]
        requests.post(wh, json={"username": "SleepingForest Warden", "embeds": [{"title": "Inactive Guild Members", "description": f"**{len(inactive)}** member(s) had no XP gains since last check **({since} ago)**.", "color": 0xC0392B, "fields": [{"name": "Members", "value": "\n".join(member_lines), "inline": False}], "footer": {"text": "SleepingForest"}, "timestamp": now_iso}]}, timeout=10)
    else:
        requests.post(wh, json={"username": "SleepingForest Warden", "embeds": [{"title": "All Members Active", "description": f"Every guild member gained XP since the last check **({since} ago)**.", "color": 0x27AE60, "footer": {"text": "SleepingForest"}, "timestamp": now_iso}]}, timeout=10)

@tasks.loop(time=ACTIVITY_TIMES)
async def activity_check_loop():
    await asyncio.get_running_loop().run_in_executor(None, run_activity_check)

# ── Worldboss alert loop (world boss only, separate from raid boss) ──────────────────────────────
def wb_load_state():
    if not os.path.exists(WB_STATE_FILE):
        return {"sent": []}
    try:
        with open(WB_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"sent": []}

def wb_save_state(state):
    with open(WB_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def wb_normalize_dt(dt_str):
    s = dt_str.replace(" ", "T")
    if re.search(r"[+-]\d{2}$", s): s += ":00"
    return datetime.fromisoformat(s)

def wb_seconds_until(event):
    return (wb_normalize_dt(event["scheduled_time"]) - datetime.now(timezone.utc)).total_seconds()

def wb_spawn_unix(event):
    return int(wb_normalize_dt(event["scheduled_time"]).timestamp())

def wb_build_embed(event):
    boss = event["boss"]
    unix_ts = wb_spawn_unix(event)
    return {
        "title": "Bossing Alert",
        "description": f"**{boss['name']}**\nLevel {boss['level']}\nLocation: {boss['location']}\n\nSpawns: <t:{unix_ts}:R>",
        "color": 0x72AEED,
        "image": {"url": boss["image_url"]},
        "footer": {"text": "SleepingForest \u2022 DegenIdle"},
        "timestamp": wb_normalize_dt(event["scheduled_time"]).astimezone(timezone.utc).isoformat(),
    }

def run_worldboss_check():
    if not WB_WEBHOOK_URL:
        print("[WB] No DISCORD_WB_WEBHOOK set - skipping")
        return
    try:
        access_token = get_access_token()
    except Exception as e:
        print(f"[WB] Token refresh failed: {e}")
        return
    headers = make_headers(access_token)
    state = wb_load_state()
    sent = set(state.get("sent", []))
    try:
        r = requests.get(WB_SCHEDULE_URL, headers=headers, timeout=20)
        r.raise_for_status()
        payload = r.json()
    except Exception as e:
        print(f"[WB] Schedule fetch failed: {e}")
        state["sent"] = list(sent)[-200:]
        wb_save_state(state)
        return
    if not payload.get("success"):
        state["sent"] = list(sent)[-200:]
        wb_save_state(state)
        return
    events = payload.get("data") or []
    best_event, best_secs = None, None
    for event in events:
        secs = wb_seconds_until(event)
        if secs <= 0: continue
        if secs <= WB_LOOKAHEAD_MINUTES * 60:
            if best_secs is None or secs < best_secs:
                best_event, best_secs = event, secs
    if not best_event:
        state["sent"] = list(sent)[-200:]
        wb_save_state(state)
        return
    spawn_key = f"{best_event['id']}:spawn"
    if spawn_key in sent:
        state["sent"] = list(sent)[-200:]
        wb_save_state(state)
        return
    wait_secs = best_secs - WB_SEND_MINUTES_BEFORE * 60
    if wait_secs > 0:
        time.sleep(wait_secs)
    if wb_seconds_until(best_event) > 0:
        content = ""
        allowed_mentions = {"parse": []}
        if WB_ROLE_ID and best_event["boss"]["level"] <= 50:
            content = f"<@&{WB_ROLE_ID}>"
            allowed_mentions = {"roles": [WB_ROLE_ID]}
        requests.post(WB_WEBHOOK_URL, json={"username": "SleepingForest Watch", "content": content, "embeds": [wb_build_embed(best_event)], "allowed_mentions": allowed_mentions}, timeout=20).raise_for_status()
        sent.add(spawn_key)
        print(f"[WB] Alert sent for {best_event['boss']['name']}")
    state["sent"] = list(sent)[-200:]
    wb_save_state(state)

@tasks.loop(minutes=15)
async def worldboss_loop():
    await asyncio.get_running_loop().run_in_executor(None, run_worldboss_check)

# ── Log helper ────────────────────────────────────────────────────────────────────────────────────
async def send_log(msg):
    if not LOGS_WEBHOOK_URL:
        return
    try:
        requests.post(LOGS_WEBHOOK_URL, json={"content": msg, "username": "SleepingForest Log"}, timeout=10)
    except Exception:
        pass

# ── Discord commands ──────────────────────────────────────────────────────────────────────────────
@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    content = message.content.strip()
    author_id = str(message.author.id)
    is_owner = author_id == OWNER_ID
    is_dm = message.guild is None
    guild_obj = bot.get_guild(int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None
    acting_member = guild_obj.get_member(int(author_id)) if guild_obj else None

    # !link
    if content.lower().startswith("!link "):
        args = content[6:].strip()
        _lparts = args.rsplit(None, 1)
        is_admin = acting_member and has_admin_role(acting_member)
        if len(_lparts) == 2 and _lparts[1].isdigit() and len(_lparts[1]) >= 15 and (is_owner or is_admin):
            await asyncio.get_running_loop().run_in_executor(None, do_link, _lparts[1], _lparts[0].strip())
            await message.channel.send(f"Done! **{_lparts[0].strip()}** linked to <@{_lparts[1]}>.")
            await send_log(f"Force-linked by ID: **{_lparts[0].strip()}** -> <@{_lparts[1]}> (by <@{author_id}>)")
            return
        target_id = author_id
        ingame_name = args
        if args.startswith("<@") and (is_owner or is_admin):
            m = re.match(r"<@!?(\d+)>\s+(.+)", args)
            if m:
                target_id, ingame_name = m.group(1), m.group(2).strip()
            else:
                try: await message.author.send("Usage: `!link @user IngameName`")
                except Exception: pass
                return
        else:
            if not is_owner and (not acting_member or not has_members_role(acting_member)):
                if is_dm:
                    try: await message.author.send("You need the Members role to link your account.")
                    except Exception: pass
                return
        if not ingame_name:
            try: await message.author.send("Usage: `!link YourIngameName`")
            except Exception: pass
            return
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, do_link, target_id, ingame_name)
            confirm = result if target_id != author_id else result
            try: await message.author.send(confirm)
            except Exception:
                if not is_dm: await message.channel.send(f"<@{target_id}> {confirm}")
            await send_log(f"Linked: <@{target_id}> -> **{ingame_name}**")
        except Exception as e:
            try: await message.author.send(f"Something went wrong: {e}")
            except Exception: pass
        return

    # !unlink
    if content.lower() == "!unlink" or content.lower().startswith("!unlink "):
        is_admin = acting_member and has_admin_role(acting_member)
        parts = content.split(None, 1)
        force_name = parts[1].strip() if len(parts) > 1 else None
        if force_name and (is_owner or is_admin):
            try:
                result = await asyncio.get_running_loop().run_in_executor(None, do_force_unlink, force_name)
                try: await message.author.send(result)
                except Exception:
                    if not is_dm: await message.channel.send(result)
                await send_log(f"Force-unlinked: **{force_name}**")
            except Exception as e:
                try: await message.author.send(f"Something went wrong: {e}")
                except Exception: pass
        elif not force_name:
            if not is_owner and (not acting_member or not has_members_role(acting_member)):
                return
            members_data = load_members()
            charname = next((k for k, v in members_data.items() if v == author_id), "your character")
            try:
                result = await asyncio.get_running_loop().run_in_executor(None, do_self_unlink, author_id)
                try: await message.author.send(result)
                except Exception:
                    if not is_dm: await message.channel.send(f"{message.author.mention} {result}")
                await send_log(f"Unlinked: <@{author_id}> -> **{charname}**")
            except Exception as e:
                try: await message.author.send(f"Something went wrong: {e}")
                except Exception: pass
        return

    # admin/owner gate for remaining commands
    is_admin = acting_member and has_admin_role(acting_member)
    is_officer = acting_member and has_officer_role(acting_member)

    if content.lower() == "!members":
        if not is_owner and not is_admin: return
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, do_members_list)
            try: await message.author.send(result)
            except Exception: await message.channel.send(result)
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    elif content == "!testgiveaway":
        if not is_owner and not is_admin: return
        try:
            result, logs = await asyncio.get_running_loop().run_in_executor(None, run_giveaway_logic, True)
            await message.channel.send(f"Test complete: {result}")
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    elif content == "!testdonations":
        if not is_owner and not is_admin: return
        try:
            await asyncio.get_running_loop().run_in_executor(None, run_donations_reminder, LOGS_WEBHOOK_URL)
            await message.channel.send("Donations reminder test fired - check logs channel.")
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    elif content.lower() == "!activitycheck":
        if not is_owner and not is_admin: return
        try:
            await asyncio.get_running_loop().run_in_executor(None, run_activity_check)
            await message.channel.send("Activity check complete - results posted.")
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    elif content.lower().startswith("!settoken "):
        if not is_owner and not is_admin: return
        new_token = content[10:].strip()
        if len(new_token) < 20:
            try: await message.author.send("Invalid token - too short.")
            except Exception: await message.channel.send(f"{message.author.mention} Invalid token.")
            return
        os.environ["DEGEN_REFRESH_TOKEN"] = new_token
        try:
            r_tok = requests.post("https://auth.degenidle.com/oauth2/token", data={"client_id": CLIENT_ID, "grant_type": "refresh_token", "refresh_token": new_token}, timeout=15)
            r_tok.raise_for_status()
            tok_data = r_tok.json()
            rotated = tok_data.get("refresh_token", new_token)
            if rotated != new_token:
                os.environ["DEGEN_REFRESH_TOKEN"] = rotated
            _post_token_log(rotated, expires_in=tok_data.get("expires_in", 86400))
            _save_env_to_render("DEGEN_REFRESH_TOKEN", rotated)
            _save_token_to_github(rotated)
        except Exception as err:
            print(f"[SETTOKEN] Error: {err}")
        try: await message.author.send("Token updated and propagated.")
        except Exception: await message.channel.send(f"{message.author.mention} Token updated.")

    elif content.lower() == "!tokenexpiry":
        if not is_owner and not is_admin: return
        expires_unix, rotated, endings, err = await asyncio.get_running_loop().run_in_executor(None, check_token_expiry)
        if err:
            await message.channel.send(f"Error: {err}")
            return
        stored_end, new_end = endings
        rotation_line = (
            f"Token was rotated during this check\n- Was: `{stored_end}` -> Now: `{new_end}`"
            if rotated else
            f"Refresh token unchanged (`{stored_end}`)"
        )
        await message.channel.send(
            f"**DegenIdle Token Expiry**\n"
            f"Access token expires: <t:{expires_unix}:R> (<t:{expires_unix}:f>)\n"
            f"{rotation_line}"
        )

    elif content.lower() == "!bossstats":
        if not is_owner and not is_officer:
            await message.channel.send("You need the Officer role to use this command.")
            return
        try:
            raid, lb, members = await asyncio.get_running_loop().run_in_executor(None, fetch_boss_raid, 0)
            if not raid:
                await message.channel.send("No raid history found.")
                return
            target_wh = RAID_WEBHOOK_URL or LOGS_WEBHOOK_URL
            if not target_wh:
                await message.channel.send("No RAID_WEBHOOK_URL or DISCORD_LOGS_WEBHOOK is set.")
                return
            requests.post(target_wh, json={"username": "SleepingForest Raids", "embeds": [build_boss_embed(raid, lb, members)]}, timeout=15).raise_for_status()
            await message.channel.send("Boss stats posted to raid channel!")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")

    elif content.lower() == "!previousboss":
        if not is_owner and not is_officer:
            await message.channel.send("You need the Officer role to use this command.")
            return
        try:
            raid, lb, members = await asyncio.get_running_loop().run_in_executor(None, fetch_boss_raid, 1)
            if not raid:
                await message.channel.send("No previous boss raid found.")
                return
            target_wh = RAID_WEBHOOK_URL or LOGS_WEBHOOK_URL
            if not target_wh:
                await message.channel.send("No RAID_WEBHOOK_URL or DISCORD_LOGS_WEBHOOK is set.")
                return
            requests.post(target_wh, json={"username": "SleepingForest Raids", "embeds": [build_boss_embed(raid, lb, members)]}, timeout=15).raise_for_status()
            await message.channel.send("Previous boss stats posted to raid channel!")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")

    elif content.lower() == "!botcommands":
        embed = discord.Embed(title="SleepingForest Bot Commands", description="Commands available for your current roles.", color=0x958AEA)
        embed.add_field(name="\U0001f338 Members", value="`!link YourIngameName`\nLink your Discord to your in-game character\n\n`!unlink`\nUnlink your own account", inline=False)
        if is_officer or is_admin or is_owner:
            embed.add_field(name="\U0001f33f Officers", value="`!bossstats`\nPost the latest raid boss report to raid channel\n\n`!previousboss`\nPost the previous raid boss report to raid channel", inline=False)
        if is_admin or is_owner:
            embed.add_field(name="\u2728 Admins", value="`!link @user IngameName`\nLink another user\n\n`!unlink CharName`\nForce-unlink any member\n\n`!members`\nList all linked members\n\n`!activitycheck`\nRun the activity check now\n\n`!testgiveaway`\nTest giveaway (posts to logs)\n\n`!testdonations`\nTest donations reminder (posts to logs)\n\n`!settoken`\nUpdate the DegenIdle API token\n\n`!tokenexpiry`\nCheck live token expiry and rotation status", inline=False)
        embed.set_footer(text="Role-aware command list")
        await message.channel.send(embed=embed)

@bot.event
async def on_member_update(before, after):
    before_roles = {str(r.id) for r in before.roles}
    after_roles = {str(r.id) for r in after.roles}
    if MEMBERS_ROLE_ID not in before_roles and MEMBERS_ROLE_ID in after_roles:
        try:
            await after.send("Welcome to **SleepingForest**! Link your in-game character so we know who you are.\n\nJust send:\n`!link YourIngameName`")
        except Exception:
            pass
    if MEMBERS_ROLE_ID in before_roles and MEMBERS_ROLE_ID not in after_roles:
        user_id = str(after.id)
        members_data = load_members()
        matched_key = next((k for k, v in members_data.items() if v == user_id), None)
        if matched_key:
            await asyncio.get_running_loop().run_in_executor(None, do_self_unlink, user_id)
            await send_log(f"Auto-unlinked (role removed): <@{user_id}> -> **{matched_key}**")

@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user} - online")
    expiry = os.environ.get("TOKEN_EXPIRES_UNIX", "")
    if expiry:
        print(f"[TOKEN] Loaded expiry from env: {expiry}")
    if not weekly_giveaway.is_running():
        weekly_giveaway.start()
    if not donations_loop.is_running():
        donations_loop.start()
    if not worldboss_loop.is_running():
        worldboss_loop.start()
    if not activity_check_loop.is_running():
        activity_check_loop.start()

bot.run(BOT_TOKEN)
