import requests
import json
import os
import time
import base64
from datetime import datetime, timezone
from nacl import encoding, public

GUILD_API     = "https://api-v1.degenidle.com/api/guilds/character/ee938e63-72e6-4b8e-82bf-672ca6e0a568"
PROFILE_API   = "https://api-v1.degenidle.com/api/characters/profile/{name}"
WEBHOOK_URL   = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
ERROR_WEBHOOK = os.environ.get("ERROR_WEBHOOK_URL", "").strip()
GH_PAT        = os.environ.get("GH_PAT", "").strip()
SNAPSHOT_FILE = "snapshots.json"

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
            requests.post(ERROR_WEBHOOK, json={"content": f"⚠️ Activity Checker Error: {message}"}, timeout=10)
        except Exception:
            pass


def update_github_secret(new_refresh_token):
    if not GH_PAT or not new_refresh_token:
        send_error_alert("⛔ GH_PAT or new_refresh_token missing — secret NOT updated")
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
            send_error_alert(f"⛔ Failed to save DEGEN_REFRESH_TOKEN to {repo} after 3 attempts")


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
        send_error_alert(f"⛔ TOKEN EXPIRED — manually update DEGEN_REFRESH_TOKEN in all GitHub Secrets. Error: {e}")
        raise

    data = r.json()
    new_refresh = data.get("refresh_token")
    if new_refresh:
        print(f"[TOKEN] New refresh token ending in: ...{new_refresh[-4:]}")
        update_github_secret(new_refresh)
    else:
        send_error_alert("⛔ No new refresh_token returned — rotation will break within 24h")

    return data["access_token"]


def get_guild_members(headers: dict) -> list[str]:
    r = requests.get(GUILD_API, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()

    characters = (
        data.get("characters") or
        data.get("members") or
        data.get("data") or
        []
    )

    names = []
    for char in characters:
        if isinstance(char, dict):
            name = (
                char.get("name") or
                char.get("characterName") or
                (char.get("character") or {}).get("name")
            )
            if name:
                names.append(name)

    print(f"[Guild] Found {len(names)} members: {names}")

    if not names:
        print(f"[DEBUG] Raw response: {str(data)[:500]}")

    return names


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


def send_discord_alert(inactive: list[str], checked_at: str):
    if not WEBHOOK_URL:
        print("[WARN] DISCORD_WEBHOOK_URL not set — skipping webhook.")
        return

    member_lines = "\n".join(f"• **{m}**" for m in inactive)
    embed = {
        "title": "⚠️ Inactive Guild Members",
        "description": (
            f"The following **{len(inactive)}** member(s) had **no skill XP gains** "
            f"since the last check:\n\n{member_lines}"
        ),
        "color": 0xFF4444,
        "footer": {"text": f"Checked at {checked_at}"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    requests.post(WEBHOOK_URL, json={"username": "Activity Watcher", "embeds": [embed]}, timeout=10)
    print(f"[Discord] Alert sent — {len(inactive)} inactive.")


def send_all_active(checked_at: str):
    if not WEBHOOK_URL:
        return
    embed = {
        "title": "✅ All Members Active",
        "description": "Every guild member has gained XP since the last check. All good!",
        "color": 0x00CC66,
        "footer": {"text": f"Checked at {checked_at}"},
        "timestamp": datetime.now(timezone.utc).isoformat()
    }
    requests.post(WEBHOOK_URL, json={"username": "Activity Watcher", "embeds": [embed]}, timeout=10)


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

    snapshots = load_snapshots()
    members   = get_guild_members(headers)

    if not members:
        print("[ABORT] No members found — check the DEBUG output above.")
        return

    new_snapshots = {}
    inactive      = []
    is_first_run  = len(snapshots) == 0

    for name in members:
        skills = get_character_skills(name, headers)
        if skills is None:
            print(f"  [SKIP]      {name}")
            continue

        new_snapshots[name] = {
            "skills":    skills,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }

        if name in snapshots:
            prev   = snapshots[name]["skills"]
            gained = any(skills.get(s, 0) > prev.get(s, 0) for s in SKILLS)
            label  = "✅ active" if gained else "❌ INACTIVE"
            print(f"  {label:<12} {name}")
            if not gained:
                inactive.append(name)
        else:
            print(f"  [NEW]       {name} — baseline saved")

    save_snapshots(new_snapshots)

    if is_first_run:
        print("\n[INFO] First run — baselines saved. Comparisons start next run.")
        return

    if inactive:
        send_discord_alert(inactive, checked_at)
    else:
        print("\n[INFO] All active — no alert needed.")
        send_all_active(checked_at)


if __name__ == "__main__":
    main()
