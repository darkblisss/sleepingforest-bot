import requests
import json
import os
import time
import base64
from datetime import datetime, timezone
from nacl import encoding, public

GUILD_ID        = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
GUILD_API       = "https://api-v1.degenidle.com/api/guilds/character/ee938e63-72e6-4b8e-82bf-672ca6e0a568"
PROFILE_API     = "https://api-v1.degenidle.com/api/characters/profile/{name}"
LEADERBOARD_API = "https://api-v1.degenidle.com/api/guilds/{guild_id}/donations/leaderboard?period=weekly&characterId=ee938e63-72e6-4b8e-82bf-672ca6e0a568"
WEBHOOK_URL     = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
ERROR_WEBHOOK   = os.environ.get("ERROR_WEBHOOK_URL", "").strip()
GH_PAT          = os.environ.get("GH_PAT", "").strip()
SNAPSHOT_FILE   = "snapshots.json"

SKILLS = [
    "mining", "woodcutting", "tracking", "fishing", "gathering",
    "herbalism", "forging", "leatherworking", "tailoring", "crafting",
    "cooking", "alchemy", "combat", "woodcrafting", "dungeoneering",
    "bloomtide", "bossing", "exorcism", "tinkering"
]

REPOS = [
    "darkblisss/worldboss-bot",
    "darkblisss/donations-bot",
    "darkblisss/guild-activity-checker",
]


def send_error_alert(message):
    if ERROR_WEBHOOK:
        try:
            requests.post(ERROR_WEBHOOK, json={"content": f"Activity Warden Error: {message}"}, timeout=10)
        except Exception:
            pass


def update_github_secret(new_refresh_token):
    if not GH_PAT or not new_refresh_token:
        send_error_alert("GH_PAT or new_refresh_token missing — secret NOT updated")
        return
    for repo in REPOS:
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
                public_key = public.PublicKey(key_data["key"].encode(), encoding.Base64Encoder)
                box = public.SealedBox(public_key)
                encrypted = base64.b64encode(box.encrypt(new_refresh_token.encode())).decode()
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
            send_error_alert(f"Failed to save DEGEN_REFRESH_TOKEN to {repo} after 3 attempts")


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
        send_error_alert(f"TOKEN EXPIRED — manually update DEGEN_REFRESH_TOKEN in all GitHub Secrets. Error: {e}")
        raise

    data = r.json()
    new_refresh = data.get("refresh_token")
    if new_refresh:
        print(f"[TOKEN] New refresh token ending in: ...{new_refresh[-4:]}")
        update_github_secret(new_refresh)
    else:
        send_error_alert("No new refresh_token returned — rotation will break within 24h")

    return data["access_token"]


def parse_joined_at(joined_at_str: str) -> datetime | None:
    try:
        s = joined_at_str.replace(" ", "T")
        if s.endswith("+00"):
            s += ":00"
        return datetime.fromisoformat(s)
    except Exception:
        return None


def get_guild_members(headers: dict) -> list[dict]:
    r = requests.get(GUILD_API, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    members = []
    for char in data.get("members") or []:
        if isinstance(char, dict):
            name = char.get("character_name")
            if name:
                members.append({
                    "name":      name,
                    "joined_at": char.get("joined_at", "")
                })

    print(f"[Guild] Found {len(members)} members: {[m['name'] for m in members]}")
    return members


def get_weekly_donation_counts(headers: dict) -> dict[str, int]:
    """Returns {character_name: weekly_count} from the weekly leaderboard."""
    try:
        url = LEADERBOARD_API.format(guild_id=GUILD_ID)
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()

        counts = {}
        for entry in data.get("byMember") or []:
            if not isinstance(entry, dict):
                continue
            name  = entry.get("character_name")
            count = int(entry.get("count") or 0)
            if name:
                counts[name] = count

        print(f"[Donations] Weekly counts: {counts}")
        return counts

    except Exception as e:
        print(f"[ERROR] Could not fetch donation leaderboard: {e}")
        return {}


def format_avg_daily_donations(weekly_count: int, joined_at_str: str) -> str:
    """
    Average donations per day since joining, capped to 7 days.
    1 donation count = 200 gold or 50 of a resource.
    dubz example: 60 count / 5 days in guild = 12
    """
    try:
        joined = parse_joined_at(joined_at_str)
        if not joined:
            return "?"
        days_in_guild = (datetime.now(timezone.utc) - joined).total_seconds() / 86400
        days = max(1, min(7, days_in_guild))
        avg  = weekly_count / days
        # Round to nearest int, keep one decimal only if meaningful
        if avg == int(avg):
            return str(int(avg))
        return str(round(avg, 1))
    except Exception:
        return "?"


def get_character_skills(name: str, headers: dict) -> dict | None:
    try:
        r = requests.get(PROFILE_API.format(name=name), headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        if data.get("success"):
            skills = data["profile"]["skills"]
            return {skill: int(skills.get(skill, 0)) for skill in SKILLS}
    except Exception as e:
        print(f"[ERROR] Could not fetch skills for {name}: {e}")
    return None


def load_snapshots() -> dict:
    if os.path.exists(SNAPSHOT_FILE):
        with open(SNAPSHOT_FILE, "r") as f:
            return json.load(f)
    return {}


def save_snapshots(snapshots: dict):
    with open(SNAPSHOT_FILE, "w") as f:
        json.dump(snapshots, f, indent=2)
    print(f"[Snapshots] Saved {len(snapshots)} members.")


def format_inactive_duration(iso_str: str) -> str:
    try:
        last = datetime.fromisoformat(iso_str)
        delta = datetime.now(timezone.utc) - last
        total_seconds = int(delta.total_seconds())
        total_minutes = total_seconds // 60
        total_hours   = total_seconds // 3600
        total_days    = total_seconds // 86400

        if total_hours >= 240:
            return f"{total_days}d"
        elif total_hours >= 1:
            mins = (total_seconds % 3600) // 60
            return f"{total_hours}h {mins}m"
        else:
            return f"{total_minutes}m"
    except Exception:
        return "unknown"


def time_since(iso_str: str) -> str:
    try:
        last = datetime.fromisoformat(iso_str)
        delta = datetime.now(timezone.utc) - last
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        if hours > 0:
            return f"{hours}h {minutes}m"
        return f"{minutes}m"
    except Exception:
        return "unknown"


def send_discord_alert(inactive: list[dict], last_check_ts: str):
    if not WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL not set — skipping webhook.")
        return

    since = time_since(last_check_ts) if last_check_ts else "first check"

    # Sort: longest inactive first, then lowest avg donations
    inactive_sorted = sorted(
        inactive,
        key=lambda x: (x["last_active_ts"], x["avg_donations_raw"])
    )

    member_lines = []
    for entry in inactive_sorted:
        duration = format_inactive_duration(entry["last_active_ts"])
        member_lines.append(f"• {entry['name']} ({duration}) [{entry['avg_donations']}]")

    embed = {
        "title": "Inactive Guild Members",
        "description": (
            f"The following **{len(inactive)}** member(s) had no skill XP gains "
            f"since the last check **({since} ago)**."
        ),
        "color": 0xC0392B,
        "fields": [
            {
                "name": "Members",
                "value": "\n".join(member_lines),
                "inline": False
            }
        ],
        "footer": {"text": "SleepingForest"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    requests.post(WEBHOOK_URL, json={"username": "SleepingForest Warden", "embeds": [embed]}, timeout=10)
    print(f"[Discord] Alert sent — {len(inactive)} inactive.")


def send_all_active(last_check_ts: str):
    if not WEBHOOK_URL:
        return

    since = time_since(last_check_ts) if last_check_ts else "first check"

    embed = {
        "title": "All Members Active",
        "description": f"Every guild member gained XP since the last check **({since} ago)**.",
        "color": 0x27AE60,
        "footer": {"text": "SleepingForest"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }

    requests.post(WEBHOOK_URL, json={"username": "SleepingForest Warden", "embeds": [embed]}, timeout=10)


def main():
    checked_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\n── Activity Check @ {checked_at} ──────────────────────")

    access_token = refresh_access_token()

    headers = {
        "accept": "application/json",
        "origin": "https://degenidle.com",
        "referer": "https://degenidle.com/",
        "authorization": f"Bearer {access_token}",
        "user-agent": "Mozilla/5.0",
    }

    snapshots       = load_snapshots()
    last_check_ts   = snapshots.get("_last_run")
    members         = get_guild_members(headers)
    donation_counts = get_weekly_donation_counts(headers)

    if not members:
        print("[ABORT] No members found.")
        return

    now_iso       = datetime.now(timezone.utc).isoformat()
    new_snapshots = {"_last_run": now_iso}
    inactive      = []
    is_first_run  = not any(k != "_last_run" for k in snapshots)

    for member in members:
        name      = member["name"]
        joined_at = member["joined_at"]

        skills = get_character_skills(name, headers)
        if skills is None:
            print(f"  [SKIP]    {name}")
            if name in snapshots:
                new_snapshots[name] = snapshots[name]
            continue

        prev_data        = snapshots.get(name, {})
        prev_skills      = prev_data.get("skills", {})
        prev_streak      = prev_data.get("inactive_streak", 0)
        prev_last_active = prev_data.get("last_active_ts", now_iso)

        weekly_count  = donation_counts.get(name, 0)
        avg_str       = format_avg_daily_donations(weekly_count, joined_at)
        try:
            avg_raw = float(avg_str)
        except Exception:
            avg_raw = 0.0

        if prev_skills:
            gained = any(skills.get(s, 0) > prev_skills.get(s, 0) for s in SKILLS)
            if gained:
                streak      = 0
                last_active = now_iso
                print(f"  [active]       {name}")
            else:
                streak      = prev_streak + 1
                last_active = prev_last_active
                duration    = format_inactive_duration(last_active)
                print(f"  [INACTIVE ×{streak}]  {name} — {duration} [{avg_str}]")
                inactive.append({
                    "name":             name,
                    "streak":           streak,
                    "last_active_ts":   last_active,
                    "avg_donations":    avg_str,
                    "avg_donations_raw": avg_raw
                })
        else:
            streak      = 0
            last_active = now_iso
            print(f"  [NEW]          {name} — baseline saved")

        new_snapshots[name] = {
            "skills":          skills,
            "timestamp":       now_iso,
            "inactive_streak": streak,
            "last_active_ts":  last_active
        }

    save_snapshots(new_snapshots)

    if is_first_run:
        print("\n[INFO] First run — baselines saved. Comparisons start next run.")
        return

    if inactive:
        send_discord_alert(inactive, last_check_ts)
    else:
        print("\n[INFO] All active — no alert needed.")
        send_all_active(last_check_ts)


if __name__ == "__main__":
    main()
