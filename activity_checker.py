import requests
import json
import os
from datetime import datetime, timezone

GUILD_API     = "https://api-v1.degenidle.com/api/guilds/character/ee938e63-72e6-4b8e-82bf-672ca6e0a568"
PROFILE_API   = "https://api-v1.degenidle.com/api/characters/profile/{name}"
WEBHOOK_URL   = os.environ.get("DISCORD_WEBHOOK_URL", "")
SNAPSHOT_FILE = "snapshots.json"

SKILLS = [
    "mining", "woodcutting", "tracking", "fishing", "gathering",
    "herbalism", "forging", "leatherworking", "tailoring", "crafting",
    "cooking", "alchemy", "combat", "woodcrafting", "dungeoneering",
    "bloomtide", "bossing", "exorcism", "tinkering"
]


def get_guild_members() -> list[str]:
    r = requests.get(GUILD_API, timeout=15)
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


def get_character_skills(name: str) -> dict | None:
    try:
        r = requests.get(PROFILE_API.format(name=name), timeout=15)
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

    snapshots = load_snapshots()
    members   = get_guild_members()

    if not members:
        print("[ABORT] No members found — check the DEBUG output above.")
        return

    new_snapshots = {}
    inactive      = []
    is_first_run  = len(snapshots) == 0

    for name in members:
        skills = get_character_skills(name)
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
