import json, sys, urllib.request, os
from datetime import datetime

AUTH_TOKEN   = os.environ.get("DEGEN_TOKEN", "")
GUILD_ID     = "d08f77ef-fc13-4781-adce-0fcf88f9f77b"
CHARACTER_ID = "ee938e63-72e6-4b8e-82bf-672ca6e0a568"
BASE_URL     = "https://api-v1.degenidle.com/api"

def get(path):
    req = urllib.request.Request(f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {AUTH_TOKEN}"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())

def fmt_num(n):
    if n is None: return "N/A"
    n = float(n)
    if n >= 1_000_000: return f"{n/1_000_000:.2f}M"
    if n >= 1_000: return f"{n/1_000:.1f}K"
    return f"{n:.0f}"

def fmt_time(s):
    if not s: return "N/A"
    dt = datetime.fromisoformat(s.replace(" ", "T").split("+")[0] + "+00:00")
    return dt.strftime("%d %b %Y %H:%M UTC")

def duration_secs(start, end):
    if not start or not end: return None
    s = datetime.fromisoformat(start.replace(" ", "T").split("+")[0] + "+00:00")
    e = datetime.fromisoformat(end.replace(" ", "T").split("+")[0] + "+00:00")
    return max(1, (e - s).total_seconds())

def fmt_dur(secs):
    if not secs: return "N/A"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h: return f"{h}h {m}m {s}s"
    if m: return f"{m}m {s}s"
    return f"{s}s"

def hp_bar(pct, width=24):
    filled = int(pct / 100 * width)
    return "=" * filled + "-" * (width - filled)

def hr(w=64): print("-" * w)

if not AUTH_TOKEN:
    print("Missing token. Run: DEGEN_TOKEN='your_bearer_token' python3 boss_stats.py")
    sys.exit(1)

history = get(f"/guild-worldboss/{GUILD_ID}/history?limit=20&offset=0")
raids   = history["data"]

print()
print("  SLEEPINGFOREST GUILD - WORLD BOSS STATS")
print()

char_names, leaderboards = {}, {}
for raid in raids:
    lb = get(f"/guild-worldboss/leaderboard/{raid['id']}?characterId={CHARACTER_ID}")
    leaderboards[raid["id"]] = lb
    for e in lb.get("data", []):
        char_names[e["character_id"]] = e["character_name"]

for i, raid in enumerate(raids, 1):
    boss      = raid["boss"]
    init_id   = raid["initiator_character_id"]
    init_name = char_names.get(init_id, f"Unknown ({init_id[:8]}...)")
    secs      = duration_secs(raid["spawn_time"], raid["end_time"])
    total_dmg = float(raid["total_damage_dealt"]) if raid["total_damage_dealt"] else 0
    hp_pct    = (total_dmg / boss["max_hp"] * 100) if boss["max_hp"] else 0
    outcome   = "DEFEATED" if raid["boss_defeated"] else "Not defeated"

    hr()
    print(f"  RAID {i} | {boss['name']} Lv.{boss['level']} | {outcome}")
    hr()
    print(f"  Initiator : {init_name}")
    print(f"  Scheduled : {fmt_time(raid['scheduled_time'])}")
    print(f"  Started   : {fmt_time(raid['spawn_time'])}")
    print(f"  Ended     : {fmt_time(raid['end_time'])}")
    print(f"  Duration  : {fmt_dur(secs)}")
    print(f"  Boss HP   : {fmt_num(boss['max_hp'])}")
    print(f"  Dmg Done  : {fmt_num(total_dmg)} ({hp_pct:.1f}% of boss HP)")
    print(f"  Progress  : [{hp_bar(hp_pct)}] {hp_pct:.1f}%")

    entries = leaderboards[raid["id"]].get("data", [])
    if not entries:
        print("\n  No participants joined this raid.")
        continue

    print()
    print(f"  {'#':<4}{'Player':<18}{'Damage':>12}{'DPS/s':>10}{'%':>7}  Survived")
    hr()
    for e in entries:
        dps = e["damage_dealt"] / secs if secs else 0
        surv = "Yes" if e["survived"] else "No"
        print(f"  {e['rank']:<4}{e['character_name']:<18}{fmt_num(e['damage_dealt']):>12}{fmt_num(dps):>10}{e['percentage']:>6.1f}%  {surv}")
    lb = leaderboards[raid["id"]]
    print(f"\n  Guild total : {fmt_num(lb['total_damage'])} dmg | Boss HP: {fmt_num(lb['denominator'])}")

print()
print(f"  Total raids tracked: {len(raids)}")
print()
