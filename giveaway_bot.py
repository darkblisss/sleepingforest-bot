import os
import json
import random
import asyncio
import requests
import discord
from discord.ext import tasks
from datetime import datetime, timezone, time, timedelta
from keep_alive import keep_alive

WEBHOOK_URL        = os.environ.get("DISCORD_GIVEAWAY_WEBHOOK", "").strip()
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
ADMIN_ROLE_ID   = "1487296175756410961"
ADMIN_ROLE_ID   = "1487296175756410961"

DONATIONS_URL = f"{BASE}/guilds/{DEGEN_GUILD_ID}/donations/leaderboard?period=weekly&characterId={CHAR_ID}"
RESOURCES_URL = f"{BASE}/guilds/{DEGEN_GUILD_ID}/resources?characterId={CHAR_ID}"
GUILD_API     = f"{BASE}/guilds/character/{CHAR_ID}"

GIVEAWAY_TIME = time(hour=0, minute=0, tzinfo=timezone.utc)

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = discord.Client(intents=intents)

last_run_week = None

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
    return any(str(r.id) == ADMIN_ROLE_ID for r in member.roles)

def has_admin_role(member):
    return any(str(r.id) == ADMIN_ROLE_ID for r in member.roles)

def has_admin_role(member):
    return any(str(r.id) == ADMIN_ROLE_ID for r in member.roles)

def has_members_role(member):
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
    return r.json()["access_token"]

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

def find_eligible(donations, daily_limit, discord_map, role_ids):
    if role_ids is None:
        print("[CRITICAL] Role list failed to load — aborting")
        return [], 0

    leader_count = None
    for player in donations:
        if get_player_name(player) == GUILD_LEADER:
            leader_count = player.get("count", 0)
            break
    threshold = leader_count if leader_count is not None else daily_limit * 7
    print(f"[GIVEAWAY] Threshold ({GUILD_LEADER}): {threshold}")

    eligible = []
    for player in donations:
        name = get_player_name(player)
        count = player.get("count", 0)
        if count < threshold:
            print(f"[GIVEAWAY] {name}: {count} — below threshold, excluded")
            continue
        discord_id = discord_map.get(name, "")
        if not discord_id:
            print(f"[GIVEAWAY] {name}: not in members.json, excluded")
            continue
        if discord_id not in role_ids:
            print(f"[GIVEAWAY] {name}: missing donations role, excluded")
            continue
        print(f"[GIVEAWAY] {name}: {count} — eligible")
        eligible.append({"name": name, "count": count})

    return sorted(eligible, key=lambda x: x["count"], reverse=True), threshold

def post_webhook(embed, content=""):
    payload = {"username": "SleepingForest Giveaway", "embeds": [embed]}
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
    role_ids = get_role_member_ids()
    eligible, threshold = find_eligible(donations, daily_limit, discord_map, role_ids)

    if threshold == 0:
        return "aborted: role check failed"

    week_ending = get_week_ending()
    footer_text = f"Week ending {week_ending} • Daily limit: {daily_limit}"
    print(f"[GIVEAWAY] Final eligible ({len(eligible)}): {[p['name'] for p in eligible]}")

    if not eligible:
        post_webhook({
            "title": "Weekly Donations Giveaway",
            "description": "No winner this week.\nNobody hit the donation cap every day this week.\nBetter luck next week!",
            "color": 0x958AEA,
            "footer": {"text": footer_text},
            "timestamp": datetime.now(timezone.utc).isoformat()
        })
        return "posted: no eligible players"

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
        content=mention if winner_discord_id else ""
    )
    return f"posted: winner is {winner['name']}"

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
        # Get current SHA of the file
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

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return

    content = message.content.strip()
    author_id = str(message.author.id)
    is_owner = author_id == OWNER_ID

    # !link — members role only, result DMed
    if content.lower().startswith("!link "):
        if not has_members_role(message.author) and not is_owner:
            return
        ingame_name = content[6:].strip()
        if not ingame_name:
            try:
                await message.author.send("Usage: `!link YourIngameName`")
            except Exception:
                pass
            return
        try:
            result = await asyncio.get_running_loop().run_in_executor(None, do_link, author_id, ingame_name)
            try:
                await message.author.send(result)
            except Exception:
                await message.channel.send(f"{message.author.mention} {result}")
        except Exception as e:
            await message.channel.send(f"Error: {e}")
        return

    # !unlink — members role = unlinks themselves, owner = force unlinks by name
    if content.lower() == "!unlink" or content.lower().startswith("!unlink "):
        parts = content.split(None, 1)
        force_name = parts[1].strip() if len(parts) > 1 else None

        if force_name and is_owner:
            # Owner force-unlinking someone by in-game name
            try:
                result = await asyncio.get_running_loop().run_in_executor(None, do_force_unlink, force_name)
                try:
                    await message.author.send(result)
                except Exception:
                    await message.channel.send(result)
            except Exception as e:
                await message.channel.send(f"Error: {e}")
        elif not force_name and (has_members_role(message.author) or is_owner):
            # Member unlinking themselves
            try:
                result = await asyncio.get_running_loop().run_in_executor(None, do_self_unlink, author_id)
                try:
                    await message.author.send(result)
                except Exception:
                    await message.channel.send(f"{message.author.mention} {result}")
            except Exception as e:
                await message.channel.send(f"Error: {e}")
        return

    # Owner or admin commands
    if not is_owner and not has_admin_role(message.author):
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
            result = await asyncio.get_running_loop().run_in_executor(None, run_giveaway_logic)
            await message.channel.send(f"Result: {result}")
        except Exception as e:
            await message.channel.send(f"Error: {e}")


        os.environ["DEGEN_REFRESH_TOKEN"] = new_token
        render_api_key = os.environ.get("RENDER_API_KEY", "").strip()
        svc_id = os.environ.get("RENDER_SERVICE_ID_DONATIONS", "").strip()
        lines = ["Token update results:", "IN-MEMORY: updated immediately (active now)"]
        if render_api_key and svc_id:
            try:
                r = requests.get(
                    f"https://api.render.com/v1/services/{svc_id}/env-vars",
                    headers={"Authorization": f"Bearer {render_api_key}", "Accept": "application/json"},
                    timeout=15
                )
                existing = r.json() if r.status_code == 200 else []
                updated_vars = []
                found = False
                for item in existing:
                    ev = item.get("envVar", item)
                    k = ev.get("key", "")
                    v = new_token if k == "DEGEN_REFRESH_TOKEN" else ev.get("value", "")
                    if k == "DEGEN_REFRESH_TOKEN":
                        found = True
                    updated_vars.append({"key": k, "value": v})
                if not found:
                    updated_vars.append({"key": "DEGEN_REFRESH_TOKEN", "value": new_token})
                put_r = requests.put(
                    f"https://api.render.com/v1/services/{svc_id}/env-vars",
                    headers={"Authorization": f"Bearer {render_api_key}", "Accept": "application/json", "Content-Type": "application/json"},
                    json=updated_vars,
                    timeout=15
                )
                if put_r.status_code in (200, 201):
                    lines.append("RENDER: env var updated (persists after restart)")
                else:
                    lines.append(f"RENDER: update failed HTTP {put_r.status_code}")
            except Exception as e:
                lines.append(f"RENDER: error - {e}")
        else:
            lines.append("RENDER: RENDER_API_KEY or service ID not set — in-memory only")
        msg = "\n".join(lines)
        try:
            await message.author.send(msg)
        except Exception:
            await message.channel.send(f"{message.author.mention}\n{msg}")
        return

        os.environ["DEGEN_REFRESH_TOKEN"] = new_token
        lines = ["Token update results:", "IN-MEMORY: updated immediately (active now)"]

        # Update all 3 GitHub secrets
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

        # Update Render env var
        render_api_key = os.environ.get("RENDER_API_KEY", "").strip()
        svc_id = os.environ.get("RENDER_SERVICE_ID_DONATIONS", "").strip()
        if render_api_key and svc_id:
            try:
                r = requests.get(
                    f"https://api.render.com/v1/services/{svc_id}/env-vars",
                    headers={"Authorization": f"Bearer {render_api_key}", "Accept": "application/json"},
                    timeout=15
                )
                existing = r.json() if r.status_code == 200 else []
                updated_vars = []
                found = False
                for item in existing:
                    ev = item.get("envVar", item)
                    k = ev.get("key", "")
                    v = new_token if k == "DEGEN_REFRESH_TOKEN" else ev.get("value", "")
                    if k == "DEGEN_REFRESH_TOKEN":
                        found = True
                    updated_vars.append({"key": k, "value": v})
                if not found:
                    updated_vars.append({"key": "DEGEN_REFRESH_TOKEN", "value": new_token})
                put_r = requests.put(
                    f"https://api.render.com/v1/services/{svc_id}/env-vars",
                    headers={"Authorization": f"Bearer {render_api_key}", "Accept": "application/json", "Content-Type": "application/json"},
                    json=updated_vars,
                    timeout=15
                )
                if put_r.status_code in (200, 201):
                    lines.append("RENDER: env var updated (persists after restart)")
                else:
                    lines.append(f"RENDER: update failed HTTP {put_r.status_code}")
            except Exception as e:
                lines.append(f"RENDER: error - {e}")
        else:
            lines.append("RENDER: API key or service ID not set — skipped")

        msg = "\n".join(lines)
        try:
            await message.author.send(msg)
        except Exception:
            await message.channel.send(f"{message.author.mention}\n{msg}")
        return

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

        # Update all 3 GitHub secrets
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

        # Update Render env var
        render_api_key = os.environ.get("RENDER_API_KEY", "").strip()
        svc_id = os.environ.get("RENDER_SERVICE_ID_DONATIONS", "").strip()
        if render_api_key and svc_id:
            try:
                r = requests.get(
                    f"https://api.render.com/v1/services/{svc_id}/env-vars",
                    headers={"Authorization": f"Bearer {render_api_key}", "Accept": "application/json"},
                    timeout=15
                )
                existing = r.json() if r.status_code == 200 else []
                updated_vars = []
                found = False
                for item in existing:
                    ev = item.get("envVar", item)
                    k = ev.get("key", "")
                    v = new_token if k == "DEGEN_REFRESH_TOKEN" else ev.get("value", "")
                    if k == "DEGEN_REFRESH_TOKEN":
                        found = True
                    updated_vars.append({"key": k, "value": v})
                if not found:
                    updated_vars.append({"key": "DEGEN_REFRESH_TOKEN", "value": new_token})
                put_r = requests.put(
                    f"https://api.render.com/v1/services/{svc_id}/env-vars",
                    headers={"Authorization": f"Bearer {render_api_key}", "Accept": "application/json", "Content-Type": "application/json"},
                    json=updated_vars,
                    timeout=15
                )
                if put_r.status_code in (200, 201):
                    lines.append("RENDER: env var updated (persists after restart)")
                else:
                    lines.append(f"RENDER: update failed HTTP {put_r.status_code}")
            except Exception as e:
                lines.append(f"RENDER: error - {e}")
        else:
            lines.append("RENDER: API key or service ID not set — skipped")

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

@bot.event
async def on_ready():
    print(f"[BOT] Logged in as {bot.user} — online")
    if not weekly_giveaway.is_running():
        weekly_giveaway.start()

keep_alive()
bot.run(BOT_TOKEN)
