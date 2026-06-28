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

# -- Constants -------------------------------------------------------------------------------
DEGEN_GUILD_ID  = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID         = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE            = "https://api-v1.degenidle.com/api"
CLIENT_ID       = "c9563b2ef30348f182e122030ef28ad7"
GUILD_LEADER    = "Bloss"
OWNER_ID        = "237324092569681921"
MEMBERS_FILE    = "members.json"
SNAPSHOT_FILE   = "snapshots.json"
WB_STATE_FILE   = "worldboss_state.json"
RAID_STATE_FILE = "raid_state.json"
WB_SCHEDULE_URL = "https://api-v1.degenidle.com/api/worldboss/schedule?limit=5"
GUILD_RAID_STATUS_URL = f"https://api-v1.degenidle.com/api/guild-worldboss/{DEGEN_GUILD_ID}/status?characterId={CHAR_ID}"

# World bosses spawn at 06:00, 14:00, 22:00 UTC - alert 5 minutes before
WB_SEND_MINUTES_BEFORE  = 5
WB_LOOKAHEAD_MINUTES    = 20
WB_BOSS_LEVEL_MAX       = 50

# Raid boss is manually summoned - alert exactly 10 minutes before spawn
RAID_SEND_MINUTES_BEFORE = 10

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

# -- Env vars --------------------------------------------------------------------------------
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

# -- Fixed UTC schedule times ----------------------------------------------------------------
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

# Tracks raid IDs we have already scheduled an alert task for, so we never double-fire.
_raid_alert_scheduled = set()
_raid_report_scheduled = set()

# -- Flask keep-alive ------------------------------------------------------------------------
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

# -- Error alert -----------------------------------------------------------------------------
def send_error_alert(message):
    if ERROR_WEBHOOK_URL:
        try:
            requests.post(ERROR_WEBHOOK_URL, json={"content": f"SleepingForest Bot Error: {message}"}, timeout=10)
        except Exception:
            pass

# -- Token helpers ---------------------------------------------------------------------------
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
                "footer": {"text": "SleepingForest - Token Rotation"}
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

# -- Members helpers -------------------------------------------------------------------------
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

# -- Guild API helpers -----------------------------------------------------------------------
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

# -- Daily donations reminder ----------------------------------------------------------------
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
        lines.append(f"- {m['character_name']}")
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
            "footer": {"text": "SleepingForest - DegenIdle"},
            "timestamp": datetime.now(timezone.utc).isoformat()
        }],
        "allowed_mentions": {"parse": ["users"]}
    }
    requests.post(target_wh, json=payload, timeout=15)
    print(f"[Donations] Reminder posted - {count} member(s) haven't hit cap ({per_person_cap}) today.")

@tasks.loop(time=DONATIONS_TIME)
async def donations_loop():
    await asyncio.get_running_loop().run_in_executor(None, run_donations_reminder)

# -- Giveaway --------------------------------------------------------------------------------
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
    footer_text = f"Week ending {week_ending} - Daily limit: {daily_limit}"
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

# -- Member link/unlink ----------------------------------------------------------------------
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

# -- Boss stats (raid boss) ------------------------------------------------------------------
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
        if n is None:
            return "N/A"
        n = float(n)
        if n >= 1_000_000:
            return f"{round(n/1_000_000,2)}M"
        if n >= 1_000:
            return f"{round(n/1_000,1)}K"
        return str(round(n))

    secs = None
    if raid["spawn_time"] and raid["end_time"]:
        s = datetime.fromisoformat(raid["spawn_time"].replace(" ", "T").split("+")[0] + "+00:00")
        e = datetime.fromisoformat(raid["end_time"].replace(" ", "T").split("+")[0] + "+00:00")
        secs = max(1, (e - s).total_seconds())
    dur = (lambda m, sc: f"{m}m {sc}s" if m else f"{sc}s")(*divmod(int(secs), 60)) if secs else "N/A"
    dt = datetime.fromisoformat(raid["scheduled_time"].replace(" ", "T").split("+")[0] + "+00:00")
    date_str = dt.strftime("%d/%m/%Y")
    init_id = raid.get("initiator_character_id", "")
    init_name = (members or {}).get(init_id) or next((e["character_name"] for e in entries if e.get("character_id") == init_id), "Unknown")

    def bar(pct, width=20):
        filled = max(0, min(width, round(pct / 5)))
        empty = width - filled
        return "\u2588" * filled + "\u2591" * empty

    NB = "\u00A0"

    status_line = "**Boss Defeated**" if defeated else "**Boss Survived**"
    hp_left = max(0.0, boss["max_hp"] - total)
    hp_val = f"`[{bar(hp_remaining_pct)}]` **{round(hp_remaining_pct, 1)}% HP remaining**\n{fmt(hp_left)} HP left"

    if not entries:
        participants_block = "```\nNo participants\n```"
    else:
        header_raw = f"{'Rank':<4} {'Player':<13} {'Damage (%)':<13} {'DPS':>5}"
        sep_raw    = "-" * len(header_raw)
        header = header_raw.replace(" ", NB)
        sep    = sep_raw.replace("-", "\u2014").replace(" ", NB)
        rows = [header, sep]
        for i, e in enumerate(entries):
            dps_val = round(e["damage_dealt"] / secs) if secs else 0
            pct_val = round(e["percentage"], 1)
            name    = e["character_name"]
            dmg_pct = f"{fmt(e['damage_dealt'])} ({pct_val}%)"
            rank    = f"#{i+1}"
            row_raw = f"{rank:<4} {name:<13} {dmg_pct:<13} {dps_val:>5}"
            rows.append(row_raw.replace(" ", NB))
        participants_block = "```\n" + "\n".join(rows) + "\n```"

    footer_date = dt.strftime("%d/%m/%Y")

    description = (
        f"{status_line}\n\n"
        f"**Date:** {date_str}\n"
        f"**Duration:** {dur}\n"
        f"**Initiated by:** {init_name}\n\n"
        f"**Boss HP**\n"
        f"{hp_val}\n\n"
        f"**Participants ({len(entries)})**\n"
        f"{participants_block}"
    )

    return {
        "title": f"{boss['name']} Lv.{boss['level']} - Raid Report",
        "description": description,
        "color": 0xE74C3C if not defeated else 0x2ECC71,
        "footer": {"text": f"SleepingForest - Raids\u2022{footer_date}"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

# -- Activity checker ------------------------------------------------------------------------
def parse_joined_at(s):
    try:
        s = s.replace(" ", "T")
        if s.endswith("+00"):
            s += ":00"
        return datetime.fromisoformat(s)
    except Exception:
        return None

def format_avg_daily_donations(weekly_count, joined_at_str, daily_limit=None):
    cap = daily_limit or MAX_DONATIONS_PER_DAY
    try:
        joined = parse_joined_at(joined_at_str)
        if not joined:
            return "?"
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
        if h >= 240:
            return f"{d}d"
        if h >= 1:
            return f"{h}h {m}m"
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
            if name in snapshots:
                new_snapshots[name] = snapshots[name]
            continue
        prev = snapshots.get(name, {})
        prev_skills = prev.get("skills", {})
        prev_streak = prev.get("inactive_streak", 0)
        prev_last_active = prev.get("last_active_ts", now_iso)
        weekly_count = donation_counts.get(name, 0)
        avg_str = format_avg_daily_donations(weekly_count, member["joined_at"], daily_limit)
        try:
            avg_raw = float(avg_str)
        except Exception:
            avg_raw = 0.0
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
        member_lines = [f"- {e['name']} ({format_inactive_duration(e['last_active_ts'])}) [{e['avg_donations']}]" for e in inactive_sorted]
        requests.post(wh, json={"username": "SleepingForest Warden", "embeds": [{"title": "Inactive Guild Members", "description": f"**{len(inactive)}** member(s) had no XP gains since last check **({since} ago)**.", "color": 0xC0392B, "fields": [{"name": "Members", "value": "\n".join(member_lines), "inline": False}], "footer": {"text": "SleepingForest"}, "timestamp": now_iso}]}, timeout=10)
    else:
        requests.post(wh, json={"username": "SleepingForest Warden", "embeds": [{"title": "All Members Active", "description": f"Every guild member gained XP since the last check **({since} ago)**.", "color": 0x27AE60, "footer": {"text": "SleepingForest"}, "timestamp": now_iso}]}, timeout=10)

@tasks.loop(time=ACTIVITY_TIMES)
async def activity_check_loop():
    await asyncio.get_running_loop().run_in_executor(None, run_activity_check)

# -- World boss (global 06:00 / 14:00 / 22:00 UTC spawns ONLY) -------------------------------
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
    if re.search(r"[+-]\d{2}$", s):
        s += ":00"
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
        "description": f"**{boss['name']}**\nLevel {boss['level']}\n\nSpawns: <t:{unix_ts}:R>",
        "color": 0x72AEED,
        "image": {"url": boss["image_url"]},
        "footer": {"text": "SleepingForest - DegenIdle"},
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
        if secs <= 0:
            continue
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

    boss_level = best_event["boss"].get("level", 0)
    if wb_seconds_until(best_event) > 0:
        wb_content = ""
        wb_allowed = {"parse": []}
        if WB_ROLE_ID and boss_level <= WB_BOSS_LEVEL_MAX:
            wb_content = f"<@&{WB_ROLE_ID}>"
            wb_allowed = {"roles": [WB_ROLE_ID]}
        requests.post(
            WB_WEBHOOK_URL,
            json={
                "username": "SleepingForest Watch",
                "content": wb_content,
                "embeds": [wb_build_embed(best_event)],
                "allowed_mentions": wb_allowed,
            },
            timeout=20,
        ).raise_for_status()
        print(f"[WB] World boss alert sent for {best_event['boss']['name']} Lv.{boss_level}")
        sent.add(spawn_key)

    state["sent"] = list(sent)[-200:]
    wb_save_state(state)

@tasks.loop(minutes=15)
async def worldboss_loop():
    await asyncio.get_running_loop().run_in_executor(None, run_worldboss_check)

# -- Guild raid boss (manually initiated by guild members) -----------------------------------
def raid_load_state():
    if not os.path.exists(RAID_STATE_FILE):
        return {"alerted": [], "reported": []}
    try:
        with open(RAID_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"alerted": [], "reported": []}

def raid_save_state(state):
    with open(RAID_STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)

def raid_build_alert_embed(spawn, test_mode=False):
    boss = spawn["boss"]
    scheduled_time = spawn["scheduled_time"]
    s = scheduled_time.replace(" ", "T")
    if re.search(r"[+-]\d{2}$", s):
        s += ":00"
    unix_ts = int(datetime.fromisoformat(s).timestamp())
    title = "Raid Alert" + (" - TEST" if test_mode else "")
    description = (
        f"**{boss['name']} (Level {boss['level']})**\n"
        f"*Spawning in 10 minutes. Get ready!*\n\n"
        f"- **Cancel** active combat.\n"
        f"- **Heal** up fully.\n"
        f"- **Queue** for the raid."
    )
    if not test_mode:
        description += f"\n\nSpawns: <t:{unix_ts}:R>"
    return {
        "title": title,
        "description": description,
        "color": 0xE74C3C,
        "thumbnail": {"url": boss["image_url"]},
        "footer": {"text": "SleepingForest - Raids"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

def _post_raid_alert(spawn):
    """Post the raid alert embed to the raid webhook."""
    raid_wh = RAID_WEBHOOK_URL or LOGS_WEBHOOK_URL
    if not raid_wh:
        return
    raid_content = ""
    raid_allowed = {"parse": []}
    if RAID_ROLE_ID:
        raid_content = f"<@&{RAID_ROLE_ID}>"
        raid_allowed = {"roles": [RAID_ROLE_ID]}
    elif MEMBERS_ROLE_ID:
        raid_content = f"<@&{MEMBERS_ROLE_ID}>"
        raid_allowed = {"roles": [MEMBERS_ROLE_ID]}
    requests.post(
        raid_wh,
        json={
            "username": "SleepingForest Raids",
            "content": raid_content,
            "embeds": [raid_build_alert_embed(spawn)],
            "allowed_mentions": raid_allowed,
        },
        timeout=20,
    ).raise_for_status()
    print(f"[Raid] Alert sent for guild raid: {spawn['boss']['name']} Lv.{spawn['boss']['level']}")

def _post_raid_report(raid_id):
    """Fetch completed raid from history and post the report to the raid webhook."""
    raid_wh = RAID_WEBHOOK_URL or LOGS_WEBHOOK_URL
    if not raid_wh:
        return
    raid, lb, guild_members = fetch_boss_raid(0)
    if not raid or str(raid["id"]) != str(raid_id):
        print(f"[Raid] Report: history[0] id={raid['id'] if raid else 'none'} != expected {raid_id}, skipping")
        return
    if raid.get("boss_defeated") is None:
        print(f"[Raid] Report: boss_defeated is None for {raid_id}, raid not finished yet")
        return
    requests.post(
        raid_wh,
        json={
            "username": "SleepingForest Raids",
            "embeds": [build_boss_embed(raid, lb, guild_members)],
            "allowed_mentions": {"parse": []},
        },
        timeout=15,
    ).raise_for_status()
    print(f"[Raid] Auto-report posted for raid {raid_id}: {raid['boss']['name']}")

def _parse_spawn_dt(scheduled_str):
    s = scheduled_str.replace(" ", "T")
    if re.search(r"[+-]\d{2}$", s):
        s += ":00"
    return datetime.fromisoformat(s)

async def _raid_alert_task(spawn_id, spawn, alert_unix, state):
    """
    Sleep until exactly (spawn_time - 10 minutes), then post the alert.
    Uses asyncio.sleep so it is non-blocking and precise to the second.
    """
    now_unix = datetime.now(timezone.utc).timestamp()
    sleep_secs = alert_unix - now_unix
    if sleep_secs > 0:
        print(f"[Raid] Alert task sleeping {sleep_secs:.1f}s until exact 10-min mark for raid {spawn_id}")
        await asyncio.sleep(sleep_secs)

    # Re-check we haven't already alerted (e.g. bot restarted mid-sleep)
    state = raid_load_state()
    alerted = set(state.get("alerted", []))
    alert_key = f"{spawn_id}:alert"
    if alert_key in alerted:
        print(f"[Raid] Alert already sent for {spawn_id}, skipping")
        _raid_alert_scheduled.discard(spawn_id)
        return

    try:
        _post_raid_alert(spawn)
        alerted.add(alert_key)
        state["alerted"] = list(alerted)[-200:]
        raid_save_state(state)
    except Exception as e:
        print(f"[Raid] Alert post failed: {e}")
    finally:
        _raid_alert_scheduled.discard(spawn_id)

async def _raid_report_task(spawn_id, spawn_dt):
    """
    Sleep until the boss is due to have spawned, then poll every 30s until
    boss_defeated is set, then post the report.
    """
    # Wait until spawn time first
    now_unix = datetime.now(timezone.utc).timestamp()
    spawn_unix = spawn_dt.timestamp()
    wait_to_spawn = spawn_unix - now_unix
    if wait_to_spawn > 0:
        print(f"[Raid] Report task sleeping {wait_to_spawn:.1f}s until spawn for raid {spawn_id}")
        await asyncio.sleep(wait_to_spawn)

    # Poll every 30s until the raid is finished (max 20 minutes after spawn)
    deadline = spawn_dt.timestamp() + 20 * 60
    while datetime.now(timezone.utc).timestamp() < deadline:
        state = raid_load_state()
        reported = set(state.get("reported", []))
        report_key = f"{spawn_id}:report"
        if report_key in reported:
            _raid_report_scheduled.discard(spawn_id)
            return
        try:
            loop = asyncio.get_running_loop()
            raid, lb, guild_members = await loop.run_in_executor(None, fetch_boss_raid, 0)
            if raid and str(raid["id"]) == str(spawn_id) and raid.get("boss_defeated") is not None:
                raid_wh = RAID_WEBHOOK_URL or LOGS_WEBHOOK_URL
                if raid_wh:
                    requests.post(
                        raid_wh,
                        json={
                            "username": "SleepingForest Raids",
                            "embeds": [build_boss_embed(raid, lb, guild_members)],
                            "allowed_mentions": {"parse": []},
                        },
                        timeout=15,
                    ).raise_for_status()
                    print(f"[Raid] Auto-report posted for raid {spawn_id}")
                reported.add(report_key)
                state["reported"] = list(reported)[-200:]
                raid_save_state(state)
                _raid_report_scheduled.discard(spawn_id)
                return
        except Exception as e:
            print(f"[Raid] Report poll error: {e}")
        await asyncio.sleep(30)

    print(f"[Raid] Report task timed out for raid {spawn_id} - boss may still be alive")
    _raid_report_scheduled.discard(spawn_id)

def run_guild_raid_poll():
    """
    Lightweight poll: just fetch the active spawn and return it.
    All timing logic is handled by dedicated asyncio tasks.
    """
    if not RAID_WEBHOOK_URL and not LOGS_WEBHOOK_URL:
        return None
    try:
        access_token = get_access_token()
    except Exception as e:
        print(f"[Raid] Token refresh failed: {e}")
        return None
    headers = make_headers(access_token)
    try:
        r = requests.get(GUILD_RAID_STATUS_URL, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print(f"[Raid] Guild status fetch failed: {e}")
        return None
    if not data.get("success"):
        return None
    return data.get("data", {}).get("active_spawn")

@tasks.loop(minutes=2)
async def guild_raid_loop():
    """
    Every 2 minutes: check for a new active spawn.
    If a new spawn is detected, schedule a precise asyncio alert task and a report task.
    The actual posting is done by those tasks at the exact right moment - not here.
    """
    spawn = await asyncio.get_running_loop().run_in_executor(None, run_guild_raid_poll)
    if not spawn:
        return

    spawn_id = str(spawn["id"])
    scheduled_str = spawn.get("scheduled_time", "")

    try:
        spawn_dt = _parse_spawn_dt(scheduled_str)
    except Exception as e:
        print(f"[Raid] Could not parse scheduled_time: {e}")
        return

    alert_unix = spawn_dt.timestamp() - RAID_SEND_MINUTES_BEFORE * 60

    # Only schedule the alert task if we haven't already and it's not too late
    state = raid_load_state()
    alerted = set(state.get("alerted", []))
    reported = set(state.get("reported", []))
    alert_key = f"{spawn_id}:alert"
    report_key = f"{spawn_id}:report"

    now_unix = datetime.now(timezone.utc).timestamp()

    if alert_key not in alerted and spawn_id not in _raid_alert_scheduled:
        if alert_unix > now_unix:
            # Alert time hasn't passed yet - schedule a task to fire at the exact second
            _raid_alert_scheduled.add(spawn_id)
            asyncio.ensure_future(_raid_alert_task(spawn_id, spawn, alert_unix, state))
            print(f"[Raid] Scheduled exact alert task for raid {spawn_id} in {alert_unix - now_unix:.1f}s")
        elif now_unix < spawn_dt.timestamp():
            # We're past the 10-min mark but boss hasn't spawned yet - alert immediately
            print(f"[Raid] Missed 10-min window for raid {spawn_id}, alerting now")
            try:
                _post_raid_alert(spawn)
                alerted.add(alert_key)
                state["alerted"] = list(alerted)[-200:]
                raid_save_state(state)
            except Exception as e:
                print(f"[Raid] Late alert post failed: {e}")

    if report_key not in reported and spawn_id not in _raid_report_scheduled:
        _raid_report_scheduled.add(spawn_id)
        asyncio.ensure_future(_raid_report_task(spawn_id, spawn_dt))
        print(f"[Raid] Scheduled report task for raid {spawn_id}")

# -- Log helper ------------------------------------------------------------------------------
async def send_log(msg):
    if not LOGS_WEBHOOK_URL:
        return
    try:
        requests.post(LOGS_WEBHOOK_URL, json={"content": msg, "username": "SleepingForest Log"}, timeout=10)
    except Exception:
        pass

# -- Discord commands ------------------------------------------------------------------------
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
                try:
                    await message.author.send("Usage: `!link @user ingame_name` or `!link ingame_name`")
                except Exception:
                    pass
                return
        result = await asyncio.get_running_loop().run_in_executor(None, do_link, target_id, ingame_name)
        await message.channel.send(result)
        if target_id != author_id:
            await send_log(f"Admin link: **{ingame_name}** -> <@{target_id}> (by <@{author_id}>)")
        return

    if content.lower() == "!unlink":
        result = await asyncio.get_running_loop().run_in_executor(None, do_self_unlink, author_id)
        await message.channel.send(result)
        return

    if content.lower().startswith("!unlink "):
        if not (is_owner or (acting_member and has_officer_role(acting_member))):
            await message.channel.send("You don't have permission to unlink other members.")
            return
        target_name = content[8:].strip()
        result = await asyncio.get_running_loop().run_in_executor(None, do_force_unlink, target_name)
        await message.channel.send(result)
        await send_log(f"Force-unlink: **{target_name}** (by <@{author_id}>)")
        return

    if content.lower() == "!members":
        if not (is_owner or (acting_member and has_officer_role(acting_member))):
            await message.channel.send("You don't have permission to view the members list.")
            return
        result = await asyncio.get_running_loop().run_in_executor(None, do_members_list)
        await message.channel.send(result)
        return

    if content.lower().startswith("!bossstats"):
        parts = content.split()
        offset = 0
        if len(parts) > 1:
            try:
                offset = int(parts[1])
            except ValueError:
                pass
        try:
            raid, lb, members_map = await asyncio.get_running_loop().run_in_executor(None, fetch_boss_raid, offset)
            if not raid:
                await message.channel.send("No raid history found.")
                return
            embed = build_boss_embed(raid, lb, members_map)
            target_wh = LOGS_WEBHOOK_URL
            if target_wh:
                requests.post(
                    target_wh,
                    json={
                        "username": "SleepingForest Raids",
                        "embeds": [embed],
                        "allowed_mentions": {"parse": []},
                    },
                    timeout=15,
                )
            await message.channel.send("Boss stats posted to logs.")
        except Exception as e:
            await message.channel.send(f"Failed to fetch boss stats: {e}")
        return

    if content.lower() == "!testraid":
        if not (is_owner or (acting_member and has_officer_role(acting_member))):
            await message.channel.send("Officer+ only.")
            return
        target_wh = LOGS_WEBHOOK_URL
        if not target_wh:
            await message.channel.send("No logs webhook configured.")
            return
        try:
            token = get_access_token()
            h = make_headers(token)
            guild_data = requests.get(GUILD_API, headers=h, timeout=15).json()
            members_map = {m["character_id"]: m["character_name"] for m in (guild_data.get("members") or []) if m.get("character_id") and m.get("character_name")}
            hist = requests.get(f"{BASE}/guild-worldboss/{DEGEN_GUILD_ID}/history?limit=1&offset=0", headers=h, timeout=15).json()
            raids = hist.get("data", [])
            if not raids:
                await message.channel.send("No raid history found.")
                return
            fake_spawn = {
                "id": raids[0]["id"],
                "boss": raids[0]["boss"],
                "scheduled_time": raids[0]["scheduled_time"],
            }
            embed = raid_build_alert_embed(fake_spawn, test_mode=True)
            requests.post(
                target_wh,
                json={
                    "username": "SleepingForest Raids",
                    "embeds": [embed],
                    "allowed_mentions": {"parse": []},
                },
                timeout=15,
            ).raise_for_status()
            await message.channel.send("Test raid alert posted to logs.")
        except Exception as e:
            await message.channel.send(f"Test failed: {e}")
        return

    if content.lower() == "!testdonations":
        if not (is_owner or (acting_member and has_officer_role(acting_member))):
            await message.channel.send("Officer+ only.")
            return
        await asyncio.get_running_loop().run_in_executor(None, run_donations_reminder, LOGS_WEBHOOK_URL)
        await message.channel.send("Test donations reminder posted to logs.")
        return

    if content.lower() == "!testgiveaway":
        if not (is_owner or (acting_member and has_officer_role(acting_member))):
            await message.channel.send("Officer+ only.")
            return
        result, logs = await asyncio.get_running_loop().run_in_executor(None, run_giveaway_logic, True)
        await message.channel.send(f"Test giveaway result: {result}")
        log_chunk = "\n".join(logs[-20:])
        await send_log(f"**Test giveaway logs:**\n```\n{log_chunk}\n```")
        return

    if content.lower() == "!testactivity":
        if not (is_owner or (acting_member and has_officer_role(acting_member))):
            await message.channel.send("Officer+ only.")
            return
        await asyncio.get_running_loop().run_in_executor(None, run_activity_check)
        await message.channel.send("Activity check complete.")
        return

    if content.lower() == "!checktoken":
        if not (is_owner or (acting_member and has_admin_role(acting_member))):
            await message.channel.send("Admin only.")
            return
        expires_unix, rotated, endings, err = await asyncio.get_running_loop().run_in_executor(None, check_token_expiry)
        if err:
            await message.channel.send(f"Token check failed: {err}")
            return
        stored_ending, new_ending = endings
        status = "rotated" if rotated else "unchanged"
        await message.channel.send(
            f"Token check OK.\n"
            f"Stored: `{stored_ending}` | Returned: `{new_ending}` | Status: **{status}**\n"
            f"Expires: <t:{expires_unix}:R>"
        )
        return

    if content.lower().startswith("!settoken "):
        if not (is_owner or (acting_member and has_admin_role(acting_member))):
            await message.channel.send("Admin only.")
            return
        new_token = content[10:].strip()
        if not new_token or len(new_token) < 20:
            await message.channel.send("Invalid token.")
            return
        os.environ["DEGEN_REFRESH_TOKEN"] = new_token
        await asyncio.get_running_loop().run_in_executor(None, _save_env_to_render, "DEGEN_REFRESH_TOKEN", new_token)
        await asyncio.get_running_loop().run_in_executor(None, _save_token_to_github, new_token)
        await asyncio.get_running_loop().run_in_executor(None, _post_token_log, new_token, 86400)
        await message.channel.send(f"Token updated. New ending: `...{new_token[-4:]}`")
        try:
            await message.delete()
        except Exception:
            pass
        return

    if content.lower() == "!help":
        help_text = (
            "**SleepingForest Bot Commands**\n\n"
            "`!link <ingame_name>` - Link your Discord to your in-game character\n"
            "`!link @user <ingame_name>` - (Officer+) Link another member\n"
            "`!unlink` - Unlink your own account\n"
            "`!unlink <ingame_name>` - (Officer+) Force-unlink a member\n"
            "`!members` - (Officer+) List all linked members\n"
            "`!bossstats [offset]` - Post latest raid stats to logs channel\n"
            "`!checktoken` - (Admin) Check token status\n"
            "`!settoken <token>` - (Admin) Set a new refresh token\n"
            "`!testraid` - (Officer+) Test raid alert\n"
            "`!testdonations` - (Officer+) Test donations reminder\n"
            "`!testgiveaway` - (Officer+) Test giveaway\n"
            "`!testactivity` - (Officer+) Test activity check\n"
        )
        await message.channel.send(help_text)
        return

@bot.event
async def on_member_update(before, after):
    member_role_id = int(MEMBERS_ROLE_ID)
    had_role = any(role.id == member_role_id for role in before.roles)
    has_role = any(role.id == member_role_id for role in after.roles)

    if not had_role and has_role:
        try:
            welcome_msg = (
                "Welcome to **SleepingForest**! It is great to have you with us.\n"
                "We need you to link your in-game character so we know who you are and can ping you for guild events.\n\n"
                "Just reply to this message with:\n"
                "`!link YourIngameName`"
            )
            await after.send(welcome_msg)
            print(f"[Welcome] Sent welcome DM to {after.name}")
        except discord.Forbidden:
            print(f"[Welcome] Could not send DM to {after.name} (DMs disabled)")
        except Exception as e:
            print(f"[Welcome] Error sending DM to {after.name}: {e}")

@bot.event
async def on_ready():
    print(f"[Bot] Logged in as {bot.user} ({bot.user.id})")
    if not donations_loop.is_running():
        donations_loop.start()
    if not weekly_giveaway.is_running():
        weekly_giveaway.start()
    if not worldboss_loop.is_running():
        worldboss_loop.start()
    if not guild_raid_loop.is_running():
        guild_raid_loop.start()
    if not activity_check_loop.is_running():
        activity_check_loop.start()

bot.run(BOT_TOKEN)
