import os
import time
import random
import requests
from datetime import datetime, timezone

REFRESH_TOKEN   = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
WEBHOOK_URL     = os.environ.get("DISCORD_GIVEAWAY_WEBHOOK", "").strip()

GUILD_ID   = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID    = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE       = "https://api-v1.degenidle.com/api"
CLIENT_ID  = "c9563b2ef30348f182e122030ef28ad7"

DONATIONS_URL  = f"{BASE}/guilds/{GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL  = f"{BASE}/guilds/{GUILD_ID}/resources?characterId={CHAR_ID}"

def get_access_token():
    if not REFRESH_TOKEN:
        raise RuntimeError("Missing DEGEN_REFRESH_TOKEN")
    print(f"[TOKEN] Using refresh token ending in: ...{REFRESH_TOKEN[-4:]}")
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
    return r.json()

def get_player_name(player):
    for field in ["name", "characterName", "character_name", "username", "displayName"]:
        if player.get(field):
            return player[field]
    return "Unknown"

def find_eligible(donations, daily_limit):
    threshold = daily_limit * 7
    eligible = []
    # handle both list directly or wrapped in a key
    players = donations if isinstance(donations, list) else donations.get("members", donations.get("data", []))
    for player in players:
        count = player.get("count", 0)
        if count >= threshold:
            eligible.append({
                "name": get_player_name(player),
                "count": count,
            })
    return sorted(eligible, key=lambda x: x["count"], reverse=True)

def post_webhook(embed):
    r = requests.post(
        WEBHOOK_URL,
        json={"username": "SleepingForest Giveaway", "embeds": [embed]},
        timeout=15
    )
    r.raise_for_status()

def run_giveaway(eligible, daily_limit):
    threshold = daily_limit * 7
    week_str = datetime.now(timezone.utc).strftime("%b %d %Y")

    if not eligible:
        post_webhook({
            "title": "🎉 Weekly Donation Giveaway",
            "description": (
                f"⚠️ **No eligible players this week.**\n"
                f"No one reached {threshold} donations (daily limit × 7 days).\n"
                f"Better luck next week!"
            ),
            "color": 0xFF4444,
            "footer": {"text": f"Week ending {week_str} • Daily limit: {daily_limit}"}
        })
        print("No eligible players — posted to Discord.")
        return

    names_list = "\n".join(
        f"✅ **{p['name']}** — {p['count']} donations"
        for p in eligible
    )

    # Message 1 — eligible players
    post_webhook({
        "title": "🎉 Weekly Donation Giveaway!",
        "description": (
            f"**{len(eligible)} player{'s' if len(eligible) != 1 else ''} donated every single day this week!**\n\n"
            f"{names_list}\n\n"
            f"*(Minimum threshold: {threshold} donations — daily limit {daily_limit} × 7 days)*"
        ),
        "color": 0x5865F2,
        "footer": {"text": f"Week ending {week_str}"}
    })

    # Message 2 — drumroll
    time.sleep(3)
    post_webhook({
        "title": "🥁 Picking a winner...",
        "description": "🥁  🥁  🥁\n\nspinning...",
        "color": 0xFFA500,
    })

    # Message 3 — winner
    time.sleep(4)
    winner = random.choice(eligible)
    post_webhook({
        "title": "🏆 THIS WEEK'S WINNER!",
        "description": (
            f"# 🎊  {winner['name']}  🎊\n\n"
            f"**{winner['name']}** wins this week's giveaway!\n"
            f"*{winner['count']} donations this week*\n\n"
            f"📩 **Contact an admin to claim your prize!**"
        ),
        "color": 0xFFD700,
        "footer": {"text": f"Week ending {week_str} • {len(eligible)} eligible players"}
    })
    print(f"Winner: {winner['name']} — posted to Discord.")

def main():
    if not WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_GIVEAWAY_WEBHOOK")
    print(f"Weekly giveaway running at {datetime.now(timezone.utc)}")
    token = get_access_token()
    headers = make_headers(token)
    daily_limit = get_daily_limit(headers)
    print(f"Daily limit from API: {daily_limit}")
    donations = get_weekly_donations(headers)
    print(f"Raw donations response type: {type(donations)}")
    eligible = find_eligible(donations, daily_limit)
    print(f"Eligible players ({len(eligible)}): {[p['name'] for p in eligible]}")
    run_giveaway(eligible, daily_limit)
    print("Done.")

if __name__ == "__main__":
    main()
