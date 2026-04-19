import os
import re
import json
import random
import asyncio
import requests
import threading
import discord
from flask import Flask
from discord.ext import tasks
from datetime import datetime, timezone, time, timedelta


WEBHOOK_URL        = os.environ.get("DISCORD_GIVEAWAY_WEBHOOK", "").strip()
LOGS_WEBHOOK_URL   = os.environ.get("DISCORD_LOGS_WEBHOOK", "").strip()
BOT_TOKEN          = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
DISCORD_GUILD_ID   = os.environ.get("DISCORD_GUILD_ID", "").strip()
DONATIONS_ROLE_ID  = os.environ.get("DONATIONS_ROLE_ID", "").strip()
GH_PAT             = os.environ.get("GH_PAT", "").strip()

DEGEN_GUILD_ID  = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID         = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE            = "https://api-v1.degenidle.com/api"
CLIENT_ID       = "c9563b2ef30348f182e122030ef28ad7"
MEMBERS_FILE    = "members.json"
GUILD_LEADER    = "Bloss"
OWNER_ID        = "237324092569681921"
MEMBERS_ROLE_ID = "1487294472478785536"
ADMIN_ROLE_ID   = "1487296175756410961"
OFFICER_ROLE_ID = "1487294633150251089"

DONATIONS_URL = f"{BASE}/guilds/{DEGEN_GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL = f"{BASE}/guilds/{DEGEN_GUILD_ID}/resources?characterId={CHAR_ID}"
GUILD_API     = f"{BASE}/guilds/character/{CHAR_ID}"

GIVEAWAY_TIME = time(hour=0, minute=0, tzinfo=timezone.utc)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Client(intents=intents)

last_run_week = None

# ── Flask keep-alive ────────────────────────────────────────────────────────
flask_app = Flask(__name__)

@flask_app.route("/")
def health():
    return "OK", 200

def run_flask():
    port = int(os.environ.get("PORT", "10000"))
    flask_app.run(host="0.0.0.0", port=port)

threading.Thread(target=run_flask, daemon=True).start()
# ────────────────────────────────────────────────────────────────────────────

def get_week_ending():
    now = datetime.now(timezone.utc)
    days_since_sunday = (now.weekday() + 1) % 7
    last_sunday = now - timedelta(days=days_since_sunday)
    return last_sunday.strftime("%b %d %Y")

def load_members():
    if not os.path.exists(MEMBERS_FILE):
        return {}
    with open(MEMBERS_FILE, "r") as f:
        return json.load(f)

def save_members(data):
    with open(MEMBERS_FILE, "w") as f:
        json.dump(data, f, indent=2)

def has_admin_role(member):
    if member is None or not hasattr(member, "roles"):
        return False
    return any(str(r.id) == ADMIN_ROLE_ID for r in member.roles)

def has_officer_role(member):
    if member is None or not hasattr(member, "roles"):
        return False
    return any(str(r.id) in (OFFICER_ROLE_ID, ADMIN_ROLE_ID) for r in member.roles)

def has_members_role(member):
    if member is None or not hasattr(member, "roles"):
        return False
    return any(str(r.id) == MEMBERS_ROLE_ID for r in member.roles)

def get_access_token():
    refresh_token = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
    if not refresh_token:
        raise RuntimeError("Missing DEGEN_REFRESH_TOKEN")
    r = requests.post(
        "https://auth.degenidle.com/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        },
        timeout=20
    )
    r.raise_for_status()
    data = r.json()
    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh_token:
        os.environ["DEGEN_REFRESH_TOKEN"] = new_refresh
        print(f"[TOKEN] Rotated — saving new token ending ...{new_refresh[-4:]}")
        _save_token_to_render(new_refresh)
        _save_token_to_github(new_refresh)
        _post_token_log(new_refresh, expires_in=data.get("expires_in", 86400))
    return data["access_token"]

def _post_token_log(new_token, expires_in=86400):
    log_webhook = os.environ.get("DISCORD_LOGS_WEBHOOK", "").strip()
    if not log_webhook:
        return
    import time as _time
    expires_unix = int(_time.time()) + expires_in
    requests.post(log_webhook, json={
        "username": "SleepingForest Log",
        "embeds": [{
            "title": "🔑 Token Refreshed",
            "description": (
                f"The DegenIdle refresh token has been rotated successfully.\n"
                f"**New token ending:** `...{new_token[-4:]}`\n\n"
                f"Expires approximately: <t:{expires_unix}:R> (<t:{expires_unix}:f>)"
            ),
            "color": 0x01696f,
            "footer": {"text": "SleepingForest • Token Rotation"}
        }]
    }, timeout=10)

def _save_token_to_render(new_token):
    render_api_key = os.environ.get("RENDER_API_KEY", "").strip()
    render_service_id = os.environ.get("RENDER_SERVICE_ID", "").strip()
    if not render_api_key or not render_service_id:
        return
    headers = {"Authorization": f"Bearer {render_api_key}", "Accept": "application/json", "Content-Type": "application/json"}
    try:
        r = requests.get(f"https://api.render.com/v1/services/{render_service_id}/env-vars", headers=headers, timeout=10)
        r.raise_for_status()
        updated = []
        found = False
        for item in r.json():
            ev = item.get("envVar", {})
            if ev.get("key") == "DEGEN_REFRESH_TOKEN":
                updated.append({"key": "DEGEN_REFRESH_TOKEN", "value": new_token})
                found = True
            else:
                updated.append({"key": ev["key"], "value": ev.get("value", "")})
        if not found:
            updated.append({"key": "DEGEN_REFRESH_TOKEN", "value": new_token})
        requests.put(f"https://api.render.com/v1/services/{render_service_id}/env-vars", headers=headers, json=updated, timeout=10).raise_for_status()
        print("[TOKEN] Render env var updated")
    except Exception as e:
        print(f"[TOKEN] Render update failed: {e}")

def _save_token_to_github(new_token):
    gh_pat = os.environ.get("GH_PAT", "").strip()
    if not gh_pat:
        return
    import base64
    from nacl import encoding, public as nacl_public
    repos = ["darkblisss/worldboss-bot", "darkblisss/donations-bot", "darkblisss/guild-activity-checker"]
    for repo in repos:
        try:
            r = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers={"Authorization": f"Bearer {gh_pat}"}, timeout=10)
            r.raise_for_status()
            key_data = r.json()
            pub_key = nacl_public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder)
            encrypted = base64.b64encode(nacl_public.SealedBox(pub_key).encrypt(new_token.encode())).decode()
            requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/DEGEN_REFRESH_TOKEN", headers={"Authorization": f"Bearer {gh_pat}"}, json={"encrypted_value": encrypted, "key_id": key_data["key_id"]}, timeout=10)
            print(f"[TOKEN] GitHub secret updated: {repo}")
        except Exception as e:
            print(f"[TOKEN] GitHub update failed for {repo}: {e}")

def make_headers(access_token):
    return {
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
        "authorization": f"Bearer {access_token}",
        "user-agent": "Mozilla/5.0"
    }

def get_guild_member_names(headers):
    try:
        r = requests.get(GUILD_API, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        return [m["character_name"] for m in (data.get("members") or []) if m.get("character_name")]
    except Exception:
        return []

def get_daily_limit(headers):
    r = requests.get(RESOURCES_URL, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, list):
        for item in data:
            if "daily_limit" in item:
                return item["daily_limit"]
    if isinstance(data, dict):
        return data.get("daily_limit", 20)
    return 20

def get_weekly_donations(headers):
    r = requests.get(DONATIONS_URL, headers=headers, timeout=15)
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
    role_ids = {m["user"]["id"] for m in members if DONATIONS_ROLE_ID in m["roles"]}
    print(f"[ROLE] {len(role_ids)} members have the donations role")
    return role_ids

def find_eligible(donations, daily_limit, discord_map, role_ids, debug=False):
    logs = []
    def log(msg):
        print(msg)
        logs.append(msg)

    if role_ids is None:
        log("[CRITICAL] Role list failed to load — aborting")
        return [], 0, logs

    leader_count = None
    for player in donations:
        if get_player_name(player) == GUILD_LEADER:
            leader_count = player.get("count", 0)
            break
    threshold = leader_count if leader_count is not None else daily_limit * 7
    log(f"[GIVEAWAY] Threshold ({GUILD_LEADER}): {threshold}")

    eligible = []
    for player in donations:
        name = get_player_name(player)
        count = player.get("count", 0)
        is_leader = (name == GUILD_LEADER)
        if not is_leader and count < threshold:
            log(f"[GIVEAWAY] {name}: {count} — below threshold, excluded")
            continue
        discord_id = discord_map.get(name, "")
        if not discord_id:
            log(f"[GIVEAWAY] {name}: not in members.json, excluded")
            continue
        if discord_id not in role_ids:
            log(f"[GIVEAWAY] {name}: missing donations role, excluded")
            continue
        log(f"[GIVEAWAY] {name}: {count} — eligible")
        eligible.append({"name": name, "count": count})

    return sorted(eligible, key=lambda x: x["count"], reverse=True), threshold, logs

def post_webhook(embed, content="", mention_id=None):
    payload = {"username": "SleepingForest Giveaway", "embeds": [embed]}
    if content:
        payload["content"] = content
        payload["allowed_mentions"] = {"parse": [], "users": [mention_id]} if mention_id else {"parse": []}
    requests.post(WEBHOOK_URL, json=payload, timeout=15).raise_for_status()

def run_giveaway_logic(debug=False):
    discord_map = load_members()
    token = get_access_token()
    headers = make_headers(token)
    daily_limit = get_daily_limit(headers)
    donations = get_weekly_donations(headers)
    role_ids = get_role_member_ids()
    eligible, threshold, logs = find_eligible(donations, daily_limit, discord_map, role_ids, debug=debug)

    if threshold == 0:
        return "aborted: role check failed", logs

    week_ending = get_week_ending()
    footer_text = f"Week ending {week_ending} • Daily limit: {daily_limit}"
    print(f"[GIVEAWAY] Final eligible ({len(eligible)}): {[p['name'] for p in eligible]}")
    logs.append(f"[GIVEAWAY] Final eligible ({len(eligible)}): {[p['name'] for p in eligible]}")

    if not eligible:
        post_webhook({
            "title": "Weekly Donations Giveaway",
            "description": "No winner this week.\nNobody hit the donation cap every day this week.\nBetter luck next week!",
            "color": 0x958AEA,
            "footer": {"text": footer_text},
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        return "posted: no eligible players", logs

    winner = random.choice(eligible)
    count = len(eligible)
    player_word = "player" if count == 1 else "players"
    winner_discord_id = discord_map.get(winner["name"], "")
    mention = f"<@{winner_discord_id}>" if winner_discord_id else winner["name"]

    post_webhook(
        embed={
            "title": "Weekly Donations Giveaway",
            "description": (
                f"Congratulations {mention}, you won this week's giveaway!\n"
                f"{count} {player_word} hit the donation cap this week, well done all.\n"
                f"Keep it up and see you next week!"
            ),
            "color": 0x958AEA,
            "footer": {"text": footer_text},
            "timestamp": datetime.now(timezone.utc).isoformat()
        },
        content=mention if winner_discord_id else "", mention_id=winner_discord_id
    )
    return f"posted: winner is {winner['name']}", logs

def trigger_activity_check():
    if not GH_PAT:
        return "error: GH_PAT not set in Render env vars"
    r = requests.post(
        "https://api.github.com/repos/darkblisss/guild-activity-checker/actions/workflows/activity-check.yml/dispatches",
        headers={
            "Authorization": f"Bearer {GH_PAT}",
            "Accept": "application/vnd.github+json"
        },
        json={"ref": "main"},
        timeout=15
    )
    if r.status_code == 204:
        return "Activity check triggered — results in Discord shortly"
    return f"Failed: {r.status_code} {r.text}"

def push_members_to_github():
    if not GH_PAT:
        print("[WARN] GH_PAT not set — skipping members.json push to GitHub")
        return
    try:
        members_data = open(MEMBERS_FILE, "r").read()
        encoded = __import__("base64").b64encode(members_data.encode()).decode()
        headers = {
            "Authorization": f"Bearer {GH_PAT}",
            "Accept": "application/vnd.github+json"
        }
        r = requests.get(
            "https://api.github.com/repos/darkblisss/donations-bot/contents/members.json",
            headers=headers, timeout=10
        )
        sha = r.json().get("sha", "") if r.status_code == 200 else ""
        payload = {
            "message": "chore: auto-update members.json [skip ci]",
            "content": encoded
        }
        if sha:
            payload["sha"] = sha
        put_r = requests.put(
            "https://api.github.com/repos/darkblisss/donations-bot/contents/members.json",
            headers=headers, json=payload, timeout=10
        )
        if put_r.status_code in (200, 201):
            print("[GitHub] members.json pushed successfully")
        else:
            print(f"[GitHub] Push failed: {put_r.status_code} {put_r.text}")
    except Exception as e:
        print(f"[GitHub] Push error: {e}")

def do_link(discord_id, ingame_name_input):
    try:
        token = get_access_token()
        headers = make_headers(token)
        guild_names = get_guild_member_names(headers)
    except Exception as e:
        return f"Could not fetch guild members: {e}"

    matched_name = next((n for n in guild_names if n.lower() == ingame_name_input.lower()), None)
    if not matched_name:
        return f"No guild member found matching **{ingame_name_input}**. Check the spelling and try again."

    members = load_members()
    members = {k: v for k, v in members.items() if v != discord_id}
    members[matched_name] = discord_id
    save_members(members)
    push_members_to_github()
    return f"You are all set! I have successfully linked **{matched_name}** to your Discord. Welcome to the guild, it is great to have you with us!"

def do_self_unlink(discord_id):
    members = load_members()
    matched_key = next((k for k, v in members.items() if v == discord_id), None)
    if not matched_key:
        return "You are not currently linked to any in-game name."
    del members[matched_key]
    save_members(members)
    push_members_to_github()
    return f"All done. I have unlinked **{matched_key}** from your account as requested. We wish you the best on your journey!"

def do_force_unlink(ingame_name_input):
    members = load_members()
    matched_key = next((k for k in members if k.lower() == ingame_name_input.lower()), None)
    if not matched_key:
        return f"No entry found for **{ingame_name_input}** in members.json."
    del members[matched_key]
    save_members(members)
    push_members_to_github()
    return f"The member list has been updated. I have officially unlinked **{matched_key}** and cleared them from our records."

def do_members_list():
    members = load_members()
    if not members:
        return "members.json is empty."
    lines = [f"**{name}** → <@{did}>" for name, did in sorted(members.items())]
    return f"**Linked members ({len(lines)}):**\n" + "\n".join(lines)

@tasks.loop(time=GIVEAWAY_TIME)
async def weekly_giveaway():
    global last_run_week
    now = datetime.now(timezone.utc)
    if now.weekday() != 0:
        return
    week_key = now.strftime("%Y-W%W")
    if week_key == last_run_week:
        print(f"[GIVEAWAY] Already ran for week {week_key}, skipping.")
        return
    last_run_week = week_key
    print(f"[GIVEAWAY] Running for week {week_key} at {now}")
    await asyncio.get_running_loop().run_in_executor(None, run_giveaway_logic)

LOG_WEBHOOK_URL = "https://discord.com/api/webhooks/1492516908149248251/ASkbVKGlyrWTLcu3rm7K9CTkuDsyGdnVY4bFN59fUAgCxIN1ehaqlkR3ETlbv_A1ypQJ"

async def send_log(msg):
    if not LOG_WEBHOOK_URL:
        return
    try:
        requests.post(LOG_WEBHOOK_URL, json={"content": msg, "username": "SleepingForest Log"}, timeout=10)
    except Exception:
        pass

def fetch_last_boss_raid():
    token = get_access_token()
    h = {
        "Authorization": "Bearer " + token,
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
    }
    hist = requests.get(
        BASE + "/guild-worldboss/" + DEGEN_GUILD_ID + "/history?limit=1&offset=0",
        headers=h, timeout=15
    ).json()
    raids = hist.get("data", [])
    if not raids:
        return None, None, {}
    raid = raids[0]
    lb = requests.get(
        BASE + "/guild-worldboss/leaderboard/" + raid["id"] + "?characterId=" + CHAR_ID,
        headers=h, timeout=15
    ).json()
    try:
        guild_data = requests.get(GUILD_API, headers=h, timeout=15).json()
        members = {
            m["character_id"]: m["character_name"]
            for m in (guild_data.get("members") or [])
            if m.get("character_id") and m.get("character_name")
        }
    except Exception:
        members = {}
    return raid, lb, members

def fetch_previous_boss_raid():
    token = get_access_token()
    h = {
        "Authorization": "Bearer " + token,
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
    }
    hist = requests.get(
        BASE + "/guild-worldboss/" + DEGEN_GUILD_ID + "/history?limit=2&offset=0",
        headers=h, timeout=15
    ).json()
    raids = hist.get("data", [])
    if len(raids) < 2:
        return None, None, {}
    raid = raids[1]
    lb = requests.get(
        BASE + "/guild-worldboss/leaderboard/" + raid["id"] + "?characterId=" + CHAR_ID,
        headers=h, timeout=15
    ).json()
    try:
        guild_data = requests.get(GUILD_API, headers=h, timeout=15).json()
        members = {
            m["character_id"]: m["character_name"]
            for m in (guild_data.get("members") or [])
            if m.get("character_id") and m.get("character_name")
        }
    except Exception:
        members = {}
    return raid, lb, members

def build_boss_embed(raid, lb, members=None):
    boss = raid["boss"]
    entries = lb.get("data", [])
    total = float(raid["total_damage_dealt"]) if raid["total_damage_dealt"] else 0.0
    hp_pct = (total / boss["max_hp"] * 100) if boss["max_hp"] else 0.0
    hp_remaining_pct = 100.0 - hp_pct
    defeated = raid["boss_defeated"]

    def fmt(n):
        if n is None:
            return "N/A"
        n = float(n)
        if n >= 1_000_000:
            return str(round(n / 1_000_000, 2)) + "M"
        if n >= 1_000:
            return str(round(n / 1_000, 1)) + "K"
        return str(round(n))

    secs = None
    if raid["spawn_time"] and raid["end_time"]:
        s = datetime.fromisoformat(raid["spawn_time"].replace(" ", "T").split("+")[0] + "+00:00")
        e = datetime.fromisoformat(raid["end_time"].replace(" ", "T").split("+")[0] + "+00:00")
        secs = max(1, (e - s).total_seconds())

    if secs:
        mins, sc = divmod(int(secs), 60)
        dur = str(mins) + "m " + str(sc) + "s" if mins else str(sc) + "s"
    else:
        dur = "N/A"

    dt = datetime.fromisoformat(raid["scheduled_time"].replace(" ", "T").split("+")[0] + "+00:00")
    date_str = dt.strftime("%d %b %Y")

    init_id = raid.get("initiator_character_id", "")
    init_name = "Unknown"
    if members and init_id in members:
        init_name = members[init_id]
    else:
        for e in entries:
            if e.get("character_id") == init_id:
                init_name = e["character_name"]
                break

    outcome      = "BOSS DEFEATED" if defeated else "Boss Survived"
    outcome_icon = "\u2705" if defeated else "\u274c"
    color        = 0x2ECC71 if defeated else 0xB43232

    def bar(pct, w=14):
        f = int(pct / 100 * w)
        return chr(9608) * f + chr(9617) * (w - f)

    hp_bar = "`[" + bar(hp_remaining_pct) + "]` **" + str(round(hp_remaining_pct, 1)) + "% HP remaining**"
    hp_val = hp_bar + chr(10) + fmt(boss["max_hp"] - total) + " HP left"

    if not entries:
        part_val = "Nobody joined this raid."
    else:
        lines = []
        for i, e in enumerate(entries):
            dps    = e["damage_dealt"] / secs if secs else 0
            status = "\u2705" if e["survived"] else "\U0001f480"
            rank   = ["#1", "#2", "#3"][i] if i < 3 else ("#" + str(i + 1))
            lines.append(
                rank + " **" + e["character_name"] + "**"
                + "  \u00b7  " + fmt(e["damage_dealt"]) + " DMG"
                + "  \u00b7  " + fmt(dps) + "/s"
                + "  \u00b7  " + str(round(e["percentage"], 1)) + "%"
                + "  " + status
            )
        part_val = chr(10).join(lines)

    return {
        "title": boss["name"] + " Lv." + str(boss["level"]) + " \u2500 Raid Report",
        "description": outcome_icon + " **" + outcome + "**",
        "color": color,
        "fields": [
            {"name": "\U0001f4c5 Date",         "value": date_str,              "inline": True},
            {"name": "\u23f1 Duration",          "value": dur,                   "inline": True},
            {"name": "\u2694\ufe0f Initiated by", "value": "**" + init_name + "**", "inline": True},
            {"name": "Boss HP",                   "value": hp_val,                "inline": False},
            {"name": "Participants (" + str(len(entries)) + ")", "value": part_val, "inline": False},
        ],
        "footer": {"text": "SleepingForest  \u00b7  " + boss["name"] + " Lv." + str(boss["level"])},
    }

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    content = message.content.strip()
    author_id = str(message.author.id)
    is_owner = author_id == OWNER_ID

    if content.lower().startswith("!link "):
        args = content[6:].strip()
        _lparts = args.rsplit(None, 1)
        is_dm = message.guild is None
        guild_obj = bot.get_guild(int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None
        acting_member = guild_obj.get_member(int(author_id)) if (is_dm and guild_obj) else (message.author if not is_dm else None)
        is_admin = acting_member and has_admin_role(acting_member)
        if (len(_lparts) == 2 and _lparts[1].isdigit()
                and len(_lparts[1]) >= 15 and (is_owner or is_admin)):
            _ingame = _lparts[0].strip()
            _tid    = _lparts[1]
            await asyncio.get_running_loop().run_in_executor(None, do_link, _tid, _ingame)
            await message.channel.send(f"Done! **{_ingame}** has been linked to <@{_tid}>.")
            await send_log(f"Force-linked by ID: **{_ingame}** \u2192 <@{_tid}> (by <@{author_id}>)")
            return
        target_id = author_id
        ingame_name = args
        if args.startswith("<@") and (is_owner or is_admin):
            m = re.match(r"<@!?(\d+)>\s+(.+)", args)
            if m:
                target_id = m.group(1)
                ingame_name = m.group(2).strip()
            else:
                try:
                    await message.author.send("Usage: `!link @user IngameName`")
                except Exception:
                    pass
                return
        else:
            if not is_owner:
                if not acting_member or not has_members_role(acting_member):
                    if is_dm:
                        try:
                            await message.author.send("You need the Members role to link your account.")
                        except Exception:
                            pass
                    return
        if not ingame_name:
            try:
                await message.author.send("Usage: `!link YourIngameName`")
            except Exception:
                pass
            return
        try:
            await asyncio.get_running_loop().run_in_executor(None, do_link, target_id, ingame_name)
            if target_id == author_id:
                confirm = f"You are all set! I have successfully linked **{ingame_name}** to your Discord. Glad to have you in the guild!"
            else:
                confirm = f"Done! **{ingame_name}** has been linked to <@{target_id}>."
            try:
                await message.author.send(confirm)
            except Exception:
                if not is_dm:
                    await message.channel.send(f"<@{target_id}> {confirm}")
            await send_log(f"Linked: <@{target_id}> -> **{ingame_name}**")
        except Exception as e:
            try:
                await message.author.send(f"Something went wrong: {e}")
            except Exception:
                pass
        return

    if content.lower() == "!unlink" or content.lower().startswith("!unlink "):
        is_dm = message.guild is None
        guild_obj = bot.get_guild(int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None
        acting_member = guild_obj.get_member(int(author_id)) if (is_dm and guild_obj) else (message.author if not is_dm else None)
        is_admin = acting_member and has_admin_role(acting_member)
        parts = content.split(None, 1)
        force_name = parts[1].strip() if len(parts) > 1 else None
        if force_name and (is_owner or is_admin):
            try:
                await asyncio.get_running_loop().run_in_executor(None, do_force_unlink, force_name)
                confirm = f"The member list has been updated. I have officially unlinked **{force_name}** and cleared them from our records."
                try:
                    await message.author.send(confirm)
                except Exception:
                    if not is_dm:
                        await message.channel.send(confirm)
                await send_log(f"Force-unlinked: **{force_name}**")
            except Exception as e:
                try:
                    await message.author.send(f"Something went wrong: {e}")
                except Exception:
                    pass
        elif not force_name:
            if not is_owner and (not acting_member or not has_members_role(acting_member)):
                return
            members_data = load_members()
            charname = next((k for k, v in members_data.items() if v == author_id), "your character")
            try:
                await asyncio.get_running_loop().run_in_executor(None, do_self_unlink, author_id)
                confirm = f"All done. I have unlinked **{charname}** from your account as requested. We wish you the best on your journey!"
                try:
                    await message.author.send(confirm)
                except Exception:
                    if not is_dm:
                        await message.channel.send(f"{message.author.mention} {confirm}")
                await send_log(f"Unlinked: <@{author_id}> -> **{charname}**")
            except Exception as e:
                try:
                    await message.author.send(f"Something went wrong: {e}")
                except Exception:
                    pass
        return

    if not is_owner and not (message.guild and has_admin_role(message.guild.get_member(message.author.id) or message.author)):
        return

    if content.lower() == "!members":
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, do_members_list)
            try:
                await message.author.send(result)
            except Exception:
                await message.channel.send(result)
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    elif content == "!testgiveaway":
        try:
            result, logs = await asyncio.get_running_loop().run_in_executor(None, run_giveaway_logic)
            await message.channel.send(f"Test complete: {result}")
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    elif content.lower().startswith("!settoken "):
        new_token = content[10:].strip()
        if len(new_token) < 20:
            try:
                await message.author.send("Invalid token — too short. Check you copied the full value.")
            except Exception:
                await message.channel.send(f"{message.author.mention} Invalid token.")
            return
        os.environ["DEGEN_REFRESH_TOKEN"] = new_token
        lines = ["Token update results:", "IN-MEMORY: updated immediately (active now)"]
        import base64
        from nacl import encoding, public as nacl_public
        import time as _time
        gh_pat = os.environ.get("GH_PAT", "").strip()
        repos = [
            "darkblisss/worldboss-bot",
            "darkblisss/donations-bot",
            "darkblisss/guild-activity-checker",
        ]
        if gh_pat:
            for repo in repos:
                success = False
                for attempt in range(3):
                    try:
                        r = requests.get(
                            f"https://api.github.com/repos/{repo}/actions/secrets/public-key",
                            headers={"Authorization": f"Bearer {gh_pat}"},
                            timeout=10,
                        )
                        r.raise_for_status()
                        key_data = r.json()
                        pub_key = nacl_public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder)
                        box = nacl_public.SealedBox(pub_key)
                        encrypted = base64.b64encode(box.encrypt(new_token.encode())).decode()
                        put_r = requests.put(
                            f"https://api.github.com/repos/{repo}/actions/secrets/DEGEN_REFRESH_TOKEN",
                            headers={"Authorization": f"Bearer {gh_pat}"},
                            json={"encrypted_value": encrypted, "key_id": key_data["key_id"]},
                            timeout=10,
                        )
                        if put_r.status_code in (201, 204):
                            lines.append(f"GITHUB {repo.split('/')[1]}: secret updated")
                            success = True
                            break
                        else:
                            _time.sleep(2)
                    except Exception as e:
                        _time.sleep(2)
                if not success:
                    lines.append(f"GITHUB {repo.split('/')[1]}: FAILED after 3 attempts")
        else:
            lines.append("GITHUB: GH_PAT not set — secrets not updated")
        msg = "\n".join(lines)
        try:
            await message.author.send(msg)
        except Exception:
            await message.channel.send(f"{message.author.mention}\n{msg}")
        return

    elif content == "!activitycheck":
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, trigger_activity_check)
            await message.channel.send(result)
        except Exception as e:
            await message.channel.send(f"Error: {e}")

    elif content.lower() == "!botcommands":
        guild_obj4 = bot.get_guild(int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None
        acting4 = guild_obj4.get_member(int(author_id)) if guild_obj4 else None
        _is_officer = has_officer_role(acting4)
        _is_admin = has_admin_role(acting4)
        embed = discord.Embed(
            title="SleepingForest Bot Commands",
            description="Commands available for your current roles.",
            color=0x958AEA
        )
        embed.add_field(
            name="🌸 Members",
            value=(
                "`!link YourIngameName`\n"
                "Link your Discord to your in-game character\n\n"
                "`!unlink`\n"
                "Unlink your own account"
            ),
            inline=False
        )
        if _is_officer or _is_admin or is_owner:
            embed.add_field(
                name="🌿 Officers",
                value=(
                    "`!bossstats`\n"
                    "Post the latest boss raid report to logs\n\n"
                    "`!previousboss`\n"
                    "Post the previous boss raid report to logs"
                ),
                inline=False
            )
        if _is_admin or is_owner:
            embed.add_field(
                name="✨ Admins",
                value=(
                    "`!link @user IngameName`\n"
                    "Link another user by mention\n\n"
                    "`!link CharName DiscordID`\n"
                    "Link a user by raw Discord ID\n\n"
                    "`!unlink CharName`\n"
                    "Force-unlink any member\n\n"
                    "`!members`\n"
                    "List all linked members\n\n"
                    "`!activitycheck`\n"
                    "Trigger the activity check workflow"
                ),
                inline=False
            )
        if is_owner:
            embed.add_field(
                name="👑 Owner",
                value=(
                    "`!testgiveaway`\n"
                    "Test the giveaway and see full debug logs\n\n"
                    "`!settoken`\n"
                    "Update the DegenIdle API token"
                ),
                inline=False
            )
        embed.set_footer(text="Role-aware command list")
        await message.channel.send(embed=embed)
        return

    elif content.lower() == "!bossstats":
        guild_obj2 = bot.get_guild(int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None
        acting2 = guild_obj2.get_member(int(author_id)) if guild_obj2 else None
        if not is_owner and not has_officer_role(acting2):
            await message.channel.send("You need the Officer role to use this command.")
            return
        try:
            loop = asyncio.get_running_loop()
            raid, lb, members = await loop.run_in_executor(None, fetch_last_boss_raid)
            if not raid:
                await message.channel.send("No raid history found.")
                return
            embed_dict = build_boss_embed(raid, lb, members)
            if not LOGS_WEBHOOK_URL:
                await message.channel.send("DISCORD_LOGS_WEBHOOK is not set.")
                return
            r = requests.post(LOGS_WEBHOOK_URL, json={"embeds": [embed_dict]}, timeout=15)
            r.raise_for_status()
            await message.channel.send("Boss stats posted!")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return

    elif content.lower() == "!previousboss":
        guild_obj3 = bot.get_guild(int(DISCORD_GUILD_ID)) if DISCORD_GUILD_ID else None
        acting3 = guild_obj3.get_member(int(author_id)) if guild_obj3 else None
        if not is_owner and not has_officer_role(acting3):
            await message.channel.send("You need the Officer role to use this command.")
            return
        try:
            loop = asyncio.get_running_loop()
            raid, lb, members = await loop.run_in_executor(None, fetch_previous_boss_raid)
            if not raid:
                await message.channel.send("No previous boss raid found.")
                return
            embed_dict = build_boss_embed(raid, lb, members)
            if not LOGS_WEBHOOK_URL:
                await message.channel.send("DISCORD_LOGS_WEBHOOK is not set.")
                return
            r = requests.post(LOGS_WEBHOOK_URL, json={"embeds": [embed_dict]}, timeout=15)
            r.raise_for_status()
            await message.channel.send("Previous boss stats posted!")
        except Exception as e:
            await message.channel.send(f"Error: `{e}`")
        return

@bot.event
async def on_member_update(before, after):
    before_roles = {str(r.id) for r in before.roles}
    after_roles  = {str(r.id) for r in after.roles}
    if MEMBERS_ROLE_ID not in before_roles and MEMBERS_ROLE_ID in after_roles:
        try:
            await after.send(
                "Welcome to **SleepingForest**! It is great to have you with us.\n"
                "We need you to link your in-game character so we know who you are and can ping you for guild events.\n\n"
                "Just reply to this message with:\n"
                "`!link YourIngameName`"
            )
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
    print(f"[BOT] Logged in as {bot.user} — online")
    if not weekly_giveaway.is_running():
        weekly_giveaway.start()

bot.run(BOT_TOKEN)
