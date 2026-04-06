import os
import random
import requests
from datetime import datetime, timezone, timedelta

REFRESH_TOKEN  = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
WEBHOOK_URL    = os.environ.get("DISCORD_GIVEAWAY_WEBHOOK", "").strip()

GUILD_ID   = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID    = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE       = "https://api-v1.degenidle.com/api"
CLIENT_ID  = "c9563b2ef30348f182e122030ef28ad7"

DONATIONS_URL = f"{BASE}/guilds/{GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL = f"{BASE}/guilds/{GUILD_ID}/resources?characterId={CHAR_ID}"

def get_week_ending():
    now = datetime.now(timezone.utc)
    days_since_saturday = (now.weekday() - 5) % 7
    last_saturday = now - timedelta(days=days_since_saturday)
    return last_saturday.strftime("%b %d %Y")

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
    players = donations if isinstance(donations, list) else donations.get("members", donations.get("data", []))
    for player in players:
        count = player.get("count", 0)
        if count >= threshold:
            eligible.append({
                "name": get_player_name(player),
                "count": count,
            })
    return sorted(eligible, key=lambda x: x["count"], reverse=True)

def post_webhook(content):
    r = requests.post(
        WEBHOOK_URL,
        json={"username": "SleepingForest", "content": content},
        timeout=15
    )
    r.raise_for_status()

def run_giveaway(eligible, daily_limit):
    threshold = daily_limit * 7
    week_ending = get_week_ending()
    footer = f"Week ending {week_ending} • Daily limit: {daily_limit}"

    if not eligible:
        message = (
            f"**Weekly Donation Giveaway**\n\n"
            f"No winner this week.\n"
            f"Nobody reached the maximum donation cap of {threshold} this week.\n"
            f"Better luck next week!\n\n"
            f"{footer}"
        )
        post_webhook(message)
        print("No eligible players — posted to Discord.")
        return

    winner = random.choice(eligible)
    count = len(eligible)
    player_word = "player" if count == 1 else "players"

    message = (
        f"**Weekly Donation Giveaway**\n\n"
        f"Congratulations {winner['name']}, you won this week's giveaway!\n"
        f"{count} {player_word} hit the max donation cap this week, well done all.\n"
        f"Keep it up and see you next week!\n\n"
        f"{footer}"
    )
    post_webhook(message)
    print(f"Winner: {winner['name']} — posted to Discord.")

def main():
    if not WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_GIVEAWAY_WEBHOOK")
    print(f"Weekly giveaway running at {datetime.now(timezone.utc)}")
    token = get_access_token()
    headers = make_headers(token)
    daily_limit = get_daily_limit(headers)
    print(f"Daily limit: {daily_limit}, threshold: {daily_limit * 7}")
    donations = get_weekly_donations(headers)
    eligible = find_eligible(donations, daily_limit)
    print(f"Eligible ({len(eligible)}): {[p['name'] for p in eligible]}")
    run_giveaway(eligible, daily_limit)
    print("Done.")

if __name__ == "__main__":
    main()
