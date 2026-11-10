import os
import secrets
import threading
from functools import wraps

import discord
from discord import app_commands
from dotenv import load_dotenv
from flask import Flask, jsonify, request

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "")
API_SECRET = os.getenv("API_SECRET", "")
ADMIN_IDS = {
    int(x.strip()) for x in os.getenv("ADMIN_USER_IDS", "").split(",")
    if x.strip().isdigit()
}
HOST = os.getenv("API_HOST", "127.0.0.1")
PORT = int(os.getenv("API_PORT", "5000"))

# Demo storage: keys and bindings are kept in memory and reset when the process stops.
# Use a database for production deployments.
keys = {}       # key -> {"roblox_user_id": int | None, "active": bool}
bindings = {}   # Roblox UserId -> key

app = Flask(__name__)

def require_api_secret(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if not API_SECRET or request.headers.get("X-API-SECRET") != API_SECRET:
            return jsonify({"ok": False, "error": "unauthorized"}), 401
        return fn(*args, **kwargs)
    return wrapped

@app.post("/validate")
@require_api_secret
def validate_key():
    data = request.get_json(silent=True) or {}
    key = str(data.get("key", "")).strip()
    try:
        user_id = int(data.get("roblox_user_id"))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "invalid_user_id"}), 400

    record = keys.get(key)
    if not record or not record["active"]:
        return jsonify({"ok": False, "error": "invalid_key"}), 403

    bound_id = record["roblox_user_id"]
    if bound_id is None:
        if user_id in bindings:
            return jsonify({"ok": False, "error": "account_already_bound"}), 403
        record["roblox_user_id"] = user_id
        bindings[user_id] = key
    elif bound_id != user_id:
        return jsonify({"ok": False, "error": "key_bound_to_another_account"}), 403

    return jsonify({"ok": True, "message": "authorized"})

@app.get("/health")
def health():
    return jsonify({"ok": True})

def run_api():
    app.run(host=HOST, port=PORT, threaded=True)

intents = discord.Intents.default()
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

def is_admin(interaction: discord.Interaction) -> bool:
    return interaction.user.id in ADMIN_IDS

@tree.command(name="generatekey", description="Generate a one-time Roblox registration key")
@app_commands.describe(amount="Number of keys to create (1-10)")
async def generatekey(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 10] = 1):
    if not is_admin(interaction):
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    made = []
    for _ in range(amount):
        key = "AY-" + secrets.token_urlsafe(18)
        keys[key] = {"roblox_user_id": None, "active": True}
        made.append(key)
    await interaction.response.send_message(
        "Generated key(s):\n" + "\n".join(f"`{k}`" for k in made),
        ephemeral=True
    )

@tree.command(name="resetbinding", description="Unbind a Roblox UserId so it can register a new key")
@app_commands.describe(roblox_user_id="The Roblox account UserId to unbind")
async def resetbinding(interaction: discord.Interaction, roblox_user_id: str):
    if not is_admin(interaction):
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    try:
        uid = int(roblox_user_id)
    except ValueError:
        await interaction.response.send_message("Enter a numeric Roblox UserId.", ephemeral=True)
        return
    old_key = bindings.pop(uid, None)
    if old_key and old_key in keys:
        keys[old_key]["roblox_user_id"] = None
    await interaction.response.send_message(
        f"Binding reset for Roblox UserId `{uid}`. No IP address or hardware ID was collected.",
        ephemeral=True
    )

@tree.command(name="revoke", description="Revoke a registration key")
@app_commands.describe(key="The exact key to revoke")
async def revoke(interaction: discord.Interaction, key: str):
    if not is_admin(interaction):
        await interaction.response.send_message("You are not authorized.", ephemeral=True)
        return
    record = keys.get(key)
    if not record:
        await interaction.response.send_message("Key not found.", ephemeral=True)
        return
    record["active"] = False
    uid = record["roblox_user_id"]
    if uid is not None:
        bindings.pop(uid, None)
    await interaction.response.send_message("Key revoked.", ephemeral=True)

@client.event
async def on_ready():
    await tree.sync()
    print(f"Bot online as {client.user}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        raise SystemExit("Set DISCORD_TOKEN in .env first.")
    if not API_SECRET or API_SECRET == "replace_with_a_long_random_secret":
        raise SystemExit("Set a strong API_SECRET in .env first.")
    threading.Thread(target=run_api, daemon=True).start()
    client.run(DISCORD_TOKEN)
