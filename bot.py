
import os
import sqlite3
import secrets
import string
import logging

import discord
from discord.ext import commands
from discord import app_commands

# ================= CONFIG =================

TOKEN = os.getenv("DISCORD_TOKEN")
GUILD_ID = int(os.getenv("GUILD_ID", "0"))
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0"))
DATABASE_PATH = os.getenv("DATABASE_PATH", "aerin.db")

if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN is missing.")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aerin")

# ================= DATABASE =================

def connect():
    return sqlite3.connect(DATABASE_PATH)

def setup_database():
    with connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                user_key TEXT UNIQUE NOT NULL,
                verified INTEGER DEFAULT 1
            )
        """)

def generate_key():
    alphabet = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(alphabet) for _ in range(16))
    return f"AERIN-{code[:4]}-{code[4:8]}-{code[8:12]}-{code[12:]}"

def get_user_key(user_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT user_key FROM users WHERE user_id = ?",
            (str(user_id),)
        ).fetchone()
    return row[0] if row else None

def register_user(user):
    existing = get_user_key(user.id)

    if existing:
        with connect() as conn:
            conn.execute(
                "UPDATE users SET username = ? WHERE user_id = ?",
                (user.name, str(user.id))
            )
        return existing

    key = generate_key()

    with connect() as conn:
        conn.execute(
            """INSERT INTO users
               (user_id, username, user_key, verified)
               VALUES (?, ?, ?, 1)""",
            (str(user.id), user.name, key)
        )

    return key

def is_verified(user_id):
    with connect() as conn:
        row = conn.execute(
            "SELECT verified FROM users WHERE user_id = ?",
            (str(user_id),)
        ).fetchone()

    return bool(row and row[0] == 1)

# ================= REDEEM =================

class RedeemModal(discord.ui.Modal, title="Redeem Aerin Key"):

    key = discord.ui.TextInput(
        label="Aerin Key",
        placeholder="Enter your Aerin key",
        max_length=60
    )

    async def on_submit(self, interaction):
        supplied = str(self.key.value).strip().upper()

        with connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM users WHERE user_key = ?",
                (supplied,)
            ).fetchone()

            if not row:
                await interaction.response.send_message(
                    "Invalid key.",
                    ephemeral=True
                )
                return

            if row[0] != str(interaction.user.id):
                await interaction.response.send_message(
                    "This key belongs to another account.",
                    ephemeral=True
                )
                return

            conn.execute(
                "UPDATE users SET verified = 1 WHERE user_id = ?",
                (str(interaction.user.id),)
            )

        await interaction.response.send_message(
            "Your Aerin account is verified!",
            ephemeral=True
        )

# ================= PANEL =================

class AerinPanel(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register",
        emoji="📝",
        style=discord.ButtonStyle.primary,
        custom_id="aerin:register"
    )
    async def register(self, interaction, button):
        key = register_user(interaction.user)

        await interaction.response.send_message(
            "Your Aerin account has been registered.\n"
            f"Your key: `{key}`",
            ephemeral=True
        )

    @discord.ui.button(
        label="Get Key",
        emoji="🔑",
        style=discord.ButtonStyle.success,
        custom_id="aerin:getkey"
    )
    async def get_key(self, interaction, button):
        key = get_user_key(interaction.user.id)

        if not key:
            await interaction.response.send_message(
                "Please register first.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Your Aerin key: `{key}`",
            ephemeral=True
        )

    @discord.ui.button(
        label="Get Script",
        emoji="★",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:getscript"
    )
    async def get_script(self, interaction, button):
        if not is_verified(interaction.user.id):
            await interaction.response.send_message(
                "Register your Aerin account first.",
                ephemeral=True
            )
            return

        key = get_user_key(interaction.user.id)

        response = (
            "```lua\n"
            "-- Aerin Main — Personalized Account\n"
            f'local USERNAME = "{interaction.user.name}"\n'
            f'local KEY = "{key}"\n'
            "-- Account information\n"
            'print("Aerin account verified for " .. USERNAME)\n'
            "```"
        )

        await interaction.response.send_message(
            response,
            ephemeral=True
        )

    @discord.ui.button(
        label="Reset HWID",
        emoji="↻",
        style=discord.ButtonStyle.danger,
        custom_id="aerin:reset"
    )
    async def reset(self, interaction, button):
        if not is_verified(interaction.user.id):
            await interaction.response.send_message(
                "Register your account first.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Contact an Aerin administrator for account reset assistance.",
            ephemeral=True
        )

# ================= BOT =================

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def setup_hook():
    setup_database()
    bot.add_view(AerinPanel())

    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        await bot.tree.sync(guild=guild)
    else:
        await bot.tree.sync()

@bot.event
async def on_ready():
    log.info("Aerin online as %s", bot.user)

@bot.tree.command(
    name="panel",
    description="Send the Aerin account panel"
)
@app_commands.default_permissions(administrator=True)
async def panel(interaction):
    embed = discord.Embed(
        title="AERIN",
        description=(
            "Welcome to Aerin.\n\n"
            "**ACCOUNT ACCESS**\n"
            "Register your account and manage your key below.\n\n"
            "**AVAILABLE ACTIONS**\n"
            "📝 Register\n"
            "🔑 Get Key\n"
            "★ Get Script\n"
            "↻ Reset HWID"
        ),
        color=discord.Color.from_rgb(242, 190, 210)
    )

    embed.set_footer(text="AERIN • MAIN PANEL")

    await interaction.response.send_message(
        embed=embed,
        view=AerinPanel()
    )

@bot.tree.command(
    name="generatekey",
    description="Generate an Aerin account key"
)
@app_commands.describe(username="Discord username")
@app_commands.default_permissions(administrator=True)
async def generatekey(interaction, username: str):
    member = discord.utils.find(
        lambda m: m.name.lower() == username.lower(),
        interaction.guild.members
    )

    if not member:
        await interaction.response.send_message(
            "User not found in this server.",
            ephemeral=True
        )
        return

    key = register_user(member)

    await interaction.response.send_message(
        f"Generated account key for **{member.name}**:\n`{key}`",
        ephemeral=True
    )

bot.run(TOKEN)
