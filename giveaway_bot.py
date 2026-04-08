import os
import json
import random
import asyncio
import requests
import discord
from discord.ext import tasks
from datetime import datetime, timezone, time, timedelta
from keep_alive import keep_alive

REFRESH_TOKEN  = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
WEBHOOK_URL    = os.environ.get("DISCORD_GIVEAWAY_WEBHOOK", "").strip()
BOT_TOKEN      = os.environ.get("DISCORD_BOT_TOKEN", "").strip()

GUILD_ID      = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID       = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE          = "https://api-v1.degenidle.com/api"
CLIENT_ID     = "c9563b2ef30348f182e122030ef28ad7"
MEMBERS_FILE  = "members.json"
GUILD_LEADER  = "Bloss"

DONATIONS_URL = f"{BASE}/guilds/{GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL = f"{BASE}/guilds/{GUILD_ID}/resources?characterId={CHAR_ID}"

GIVEAWAY_TIME = time(hour=6, minute=0, tzinfo=timezone.utc)  # Sunday 06:00 UTC

intents = discord.Intents.default()
bot = discord.Client(intents=intents)

# track which ISO week we last ran so it never fires twice
last_run_week = None

# ── Helpers ───────────────────────────────────────────────────────

def get_week_ending():
    now = datetime.now(timezone.utc)
    days_since_saturday = (now.weekday() - 5) % 7
    last_saturday = now - timedelta(days=days_since_saturday)
    return last_saturday.strftime("%b %d %Y")

def load_members():
    if not os.path.exists(MEMBERS_FILE):
        return {}
    with open(MEMBERS_FILE, "r") as f:
        return json.load(f)

def get_access_token():
    r = requests.post(
        "https://auth.degenidle.com/oauth2/token",
        data={
            "client_id": CLIENT_ID,
            "grant_type": "refresh_token",
            "refresh_token": REFRESH_TOKEN,
        },
        timeout=20
    )
    r.raise_for_status()
    return r.json()["access_token"]

def make_headers(access_token):
    return {
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
        "authorization": f"Bearer {access_token}",
        "user-agent": "Mozilla/5.0"
    }

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

def find_eligible(donations, daily_limit):
    leader_count = None
    for player in donations:
        if get_player_name(player) == GUILD_LEADER:
            leader_count = player.get("count", 0)
            break
    threshold = leader_count if leader_count is not None else daily_limit * 7
    eligible = []
    for player in donations:
        if player.get("count", 0) >= threshold:
            eligible.append({
                "name": get_player_name(player),
                "count": player.get("count", 0),
            })
    return sorted(eligible, key=lambda x: x["count"], reverse=True), threshold

def post_webhook(embed, content=""):
    payload = {"username": "SleepingForest", "embeds": [embed]}
    if content:
        payload["content"] = content
        payload["allowed_mentions"] = {"parse": ["users"]}
    requests.post(WEBHOOK_URL, json=payload, timeout=15).raise_for_status()

def run_giveaway_logic():
    discord_map = load_members()
    token = get_access_token()
    headers = make_headers(token)
    daily_limit = get_daily_limit(headers)
    donations = get_weekly_donations(headers)
    eligible, threshold = find_eligible(donations, daily_limit)
    week_ending = get_week_ending()
    footer_text = f"Week ending {week_ending} • Daily limit: {daily_limit}"

    print(f"[GIVEAWAY] Threshold: {threshold}, Eligible ({len(eligible)}): {[p['name'] for p in eligible]}")

    if not eligible:
        post_webhook({
            "title": "Weekly Donations Giveaway",
            "description": (
                "No winner this week.\n"
                "Nobody hit the donation cap every day this week.\n"
                "Better luck next week!"
            ),
            "color": 0x958AEA,
            "footer": {"text": footer_text},
        })
        print("[GIVEAWAY] No eligible players — posted.")
        return

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
        },
        content=mention if winner_discord_id else ""
    )
    print(f"[GIVEAWAY] Winner: {winner['name']} — posted.")

# ── Task loop — fires every Sunday at 06:00 UTC ───────────────────

@tasks.loop(time=GIVEAWAY_TIME)
async def weekly_giveaway():
    global last_run_week
    now = datetime.now(timezone.utc)

    # only run on Sunday (weekday 6)
    if now.weekday() != 6:
        return

    # prevent double-firing in same week if bot restarts
    week_key = now.strftime("%Y-W%W")
    if week_key == last_run_week:
        print(f"[GIVEAWAY] Already ran for week {week_key}, skipping.")
        return

    last_run_week = week_key
    print(f"[GIVEAWAY] Running for week {week_key}")

    # run blocking requests in a thread so bot doesn't freeze
    await asyncio.get_event_loop().run_in_executor(None, run_giveaway_logic)

# ── Bot events ────────────────────────────────────────────────────

@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user} — online")
    if not weekly_giveaway.is_running():
        weekly_giveaway.start()

# ── Start ─────────────────────────────────────────────────────────

keep_alive()
bot.run(BOT_TOKEN)
