import os
import json
import random
import requests
from datetime import datetime, timezone, timedelta

REFRESH_TOKEN  = os.environ.get("DEGEN_REFRESH_TOKEN", "").strip()
WEBHOOK_URL    = os.environ.get("DISCORD_GIVEAWAY_WEBHOOK", "").strip()

GUILD_ID   = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHAR_ID    = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE       = "https://api-v1.degenidle.com/api"
CLIENT_ID  = "c9563b2ef30348f182e122030ef28ad7"
MEMBERS_FILE = "members.json"

DONATIONS_URL = f"{BASE}/guilds/{GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL = f"{BASE}/guilds/{GUILD_ID}/resources?characterId={CHAR_ID}"

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
    data = r.json()
    print(f"[DEBUG] Weekly API response type: {type(data)}")
    if isinstance(data, dict):
        print(f"[DEBUG] Response keys: {list(data.keys())}")
        # try every known wrapper key
        for key in ["byMember", "members", "data", "leaderboard", "results", "players"]:
            if key in data and isinstance(data[key], list):
                print(f"[DEBUG] Using wrapper key: '{key}', {len(data[key])} players")
                if data[key]:
                    print(f"[DEBUG] First player sample: {data[key][0]}")
                return data[key]
        print(f"[DEBUG] No known wrapper key found, dumping full response: {data}")
        return []
    if isinstance(data, list):
        print(f"[DEBUG] Direct list, {len(data)} players")
        if data:
            print(f"[DEBUG] First player sample: {data[0]}")
        return data
    return []

def get_player_name(player):
    for field in ["name", "characterName", "character_name", "username", "displayName"]:
        if player.get(field):
            return player[field]
    return "Unknown"

def find_eligible(donations, daily_limit):
    threshold = daily_limit * 7
    eligible = []
    for player in donations:
        count = player.get("count", 0)
        print(f"[DEBUG] {get_player_name(player)}: count={count}, threshold={threshold}, eligible={count >= threshold}")
        if count >= threshold:
            eligible.append({
                "name": get_player_name(player),
                "count": count,
            })
    return sorted(eligible, key=lambda x: x["count"], reverse=True)

def post_webhook(embed, content=""):
    payload = {
        "username": "SleepingForest",
        "embeds": [embed]
    }
    if content:
        payload["content"] = content
        payload["allowed_mentions"] = {"parse": ["users"]}
    r = requests.post(WEBHOOK_URL, json=payload, timeout=15)
    r.raise_for_status()

def run_giveaway(eligible, daily_limit, discord_map):
    week_ending = get_week_ending()
    footer_text = f"Week ending {week_ending} • Daily limit: {daily_limit}"

    if not eligible:
        post_webhook({
            "title": "Weekly Donations Giveaway",
            "description": (
                "No winner this week.\n"
                "Nobody hit the donation limit every day this week.\n"
                "Better luck next week!"
            ),
            "color": 0x958AEA,
            "footer": {"text": footer_text},
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        print("No eligible players — posted to Discord.")
        return

    winner = random.choice(eligible)
    count = len(eligible)
    player_word = "player" if count == 1 else "players"

    # look up winner's Discord ID from members.json
    winner_discord_id = discord_map.get(winner["name"], "")
    mention = f"<@{winner_discord_id}>" if winner_discord_id else winner["name"]

    description = (
        f"Congratulations {mention}, you won this week's giveaway!\n"
        f"{count} {player_word} hit the donation limit every day this week, well done all.\n"
        f"Keep it up and see you next week!"
    )

    post_webhook(
        embed={
            "title": "Weekly Donations Giveaway",
            "description": description,
            "color": 0x958AEA,
            "footer": {"text": footer_text},
            "timestamp": datetime.now(timezone.utc).isoformat()
        },
        content=mention if winner_discord_id else ""
    )
    print(f"Winner: {winner['name']} — posted to Discord.")

def main():
    if not WEBHOOK_URL:
        raise RuntimeError("Missing DISCORD_GIVEAWAY_WEBHOOK")
    print(f"Weekly giveaway running at {datetime.now(timezone.utc)}")
    discord_map = load_members()
    token = get_access_token()
    headers = make_headers(token)
    daily_limit = get_daily_limit(headers)
    print(f"Daily limit: {daily_limit}, threshold: {daily_limit * 7}")
    donations = get_weekly_donations(headers)
    eligible = find_eligible(donations, daily_limit)
    print(f"Eligible ({len(eligible)}): {[p['name'] for p in eligible]}")
    run_giveaway(eligible, daily_limit, discord_map)
    print("Done.")

if __name__ == "__main__":
    main()
