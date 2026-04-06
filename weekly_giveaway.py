import os
import time
import random
import requests
from datetime import datetime

REFRESH_TOKEN   = os.environ["DEGEN_REFRESH_TOKEN"]
WEBHOOK_URL     = os.environ["DISCORD_GIVEAWAY_WEBHOOK"]

GUILD_ID   = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID    = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE       = "https://api-v1.degenidle.com/api"

DONATIONS_URL  = f"{BASE}/guilds/{GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL  = f"{BASE}/guilds/{GUILD_ID}/resources?characterId={CHAR_ID}"
AUTH_URL       = "https://auth.degenidle.com/api/auth/refresh"

def get_access_token():
    r = requests.post(AUTH_URL, json={"refresh_token": REFRESH_TOKEN}, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("access_token") or data.get("token") or data.get("accessToken")

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
    for field in ["name", "characterName", "username", "displayName"]:
        if player.get(field):
            return player[field]
    return "Unknown"

def find_eligible(donations, daily_limit):
    threshold = daily_limit * 7
    eligible = []
    for player in donations:
        count = player.get("count", 0)
        if count >= threshold:
            eligible.append({
                "name": get_player_name(player),
                "count": count,
            })
    return sorted(eligible, key=lambda x: x["count"], reverse=True)

def post_webhook(embed):
    r = requests.post(WEBHOOK_URL, json={"embeds": [embed]}, timeout=15)
    r.raise_for_status()

def run_giveaway(eligible, daily_limit):
    threshold = daily_limit * 7
    week_str = datetime.utcnow().strftime("%b %d %Y")

    # Message 1 — announce eligible players
    if not eligible:
        post_webhook({
            "title": "🎉 Weekly Donation Giveaway",
            "description": f"⚠️ **No eligible players this week.**\nNo one hit {threshold} donations (max every day × 7 days).\nBetter luck next week!",
            "color": 0xFF4444,
            "footer": {"text": f"Week ending {week_str}"}
        })
        print("No eligible players — posted to Discord.")
        return

    names_list = "\n".join(
        f"✅ **{p['name']}** — {p['count']} donations"
        for p in eligible
    )
    post_webhook({
        "title": "🎉 Weekly Donation Giveaway!",
        "description": (
            f"**{len(eligible)} player{'s' if len(eligible) != 1 else ''} donated every single day this week!**\n\n"
            f"{names_list}\n\n"
            f"*(Minimum {threshold} donations required — daily limit × 7 days)*"
        ),
        "color": 0x5865F2,
        "footer": {"text": f"Week ending {week_str}"}
    })

    # Message 2 — drumroll
    time.sleep(3)
    post_webhook({
        "title": "🥁 Picking a winner...",
        "description": "🥁 🥁 🥁",
        "color": 0xFFA500,
    })

    # Message 3 — winner
    time.sleep(4)
    winner = random.choice(eligible)
    post_webhook({
        "title": "🏆 WINNER ANNOUNCED!",
        "description": (
            f"# 🎊 {winner['name']} 🎊\n\n"
            f"**{winner['name']}** wins this week's giveaway with **{winner['count']} donations**!\n\n"
            f"📩 Contact an admin to claim your prize!"
        ),
        "color": 0xFFD700,
        "footer": {"text": f"Week ending {week_str} • {len(eligible)} eligible players"}
    })
    print(f"Winner: {winner['name']} — posted to Discord.")

def main():
    print(f"Weekly giveaway running at {datetime.utcnow()}")
    token = get_access_token()
    headers = {"Authorization": f"Bearer {token}"}
    daily_limit = get_daily_limit(headers)
    print(f"Daily limit: {daily_limit}")
    donations = get_weekly_donations(headers)
    print(f"Players on leaderboard: {len(donations)}")
    eligible = find_eligible(donations, daily_limit)
    print(f"Eligible: {[p['name'] for p in eligible]}")
    run_giveaway(eligible, daily_limit)

if __name__ == "__main__":
    main()
