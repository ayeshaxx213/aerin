
import asyncio
import logging
import os
import secrets
import sqlite3
import string

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands


# ============================================================
# AERIN CONFIG
# Set these in Render -> Environment.
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0") or "0")

API_BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")
API_SECRET = os.getenv("API_SECRET", "").strip()
DATABASE = os.getenv("DATABASE_PATH", "aerin.sqlite3")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("aerin")

intents = discord.Intents.default()
intents.members = True


# ============================================================
# DATABASE
# ============================================================

def db():
    con = sqlite3.connect(DATABASE, timeout=20)
    con.row_factory = sqlite3.Row
    return con


def setup_database():
    with db() as con:
        con.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                key TEXT PRIMARY KEY,
                redeemed_by TEXT,
                redeemed_at TEXT
            )
        """)

        con.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                discord_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                nickname TEXT NOT NULL DEFAULT '',
                user_key TEXT UNIQUE NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)


def make_key():
    alphabet = string.ascii_uppercase + string.digits
    value = "".join(secrets.choice(alphabet) for _ in range(12))
    return f"AERIN-{value[:4]}-{value[4:8]}-{value[8:]}"


def make_unique_key(con):
    key = make_key()

    while (
        con.execute(
            "SELECT 1 FROM accounts WHERE user_key = ?", (key,)
        ).fetchone()
        or con.execute(
            "SELECT 1 FROM keys WHERE key = ?", (key,)
        ).fetchone()
    ):
        key = make_key()

    return key


def get_account(discord_id):
    with db() as con:
        return con.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),),
        ).fetchone()


def register_account(user, nickname=None):
    display_name = nickname or getattr(user, "display_name", user.name)

    with db() as con:
        existing = con.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(user.id),),
        ).fetchone()

        if existing:
            con.execute(
                """
                UPDATE accounts
                SET username = ?, nickname = ?
                WHERE discord_id = ?
                """,
                (user.name, display_name, str(user.id)),
            )

            return con.execute(
                "SELECT * FROM accounts WHERE discord_id = ?",
                (str(user.id),),
            ).fetchone(), False

        key = make_unique_key(con)

        con.execute(
            """
            INSERT INTO accounts
            (discord_id, username, nickname, user_key)
            VALUES (?, ?, ?, ?)
            """,
            (str(user.id), user.name, display_name, key),
        )

        return con.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(user.id),),
        ).fetchone(), True


def is_registered(discord_id):
    return get_account(discord_id) is not None


# ============================================================
# RESET HWID API
# ============================================================

async def reset_device_binding(discord_id):
    if not API_BASE_URL or not API_SECRET:
        return False, "Device reset is not configured."

    headers = {
        "Content-Type": "application/json",
        "X-API-Secret": API_SECRET,
    }

    payload = {
        "discord_id": str(discord_id)
    }

    timeout = aiohttp.ClientTimeout(total=15)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{API_BASE_URL}/api/reset-hwid",
                json=payload,
                headers=headers,
            ) as response:

                try:
                    data = await response.json(content_type=None)
                except (aiohttp.ContentTypeError, ValueError):
                    data = {}

                if response.status == 200 and data.get("success") is True:
                    return True, data.get(
                        "message",
                        "Your device binding reset request was completed.",
                    )

                return False, data.get(
                    "message",
                    f"Reset service returned HTTP {response.status}.",
                )

    except asyncio.TimeoutError:
        return False, "The Aerin reset service timed out."

    except aiohttp.ClientError:
        log.exception("Reset API request failed")
        return False, "Could not connect to the Aerin reset service."


# ============================================================
# REGISTER MODAL
# ============================================================

class RegisterModal(discord.ui.Modal, title="Register for Aerin"):

    nickname = discord.ui.TextInput(
        label="Nickname",
        placeholder="Enter your Aerin nickname",
        min_length=1,
        max_length=32,
        required=True,
    )

    async def on_submit(self, interaction: discord.Interaction):

        account, created = register_account(
            interaction.user,
            str(self.nickname.value).strip(),
        )

        if created:
            message = "Your Aerin account has been created."
        else:
            message = "Your existing Aerin account was updated."

        await interaction.response.send_message(
            f"{message}\n"
            f"Username: **{account['username']}**\n"
            f"Nickname: **{account['nickname']}**\n"
            "Press **Get Key** to view your key.",
            ephemeral=True,
        )


# ============================================================
# AERIN PANEL — NO EMOJIS
# ============================================================

class AerinPanel(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register",
        style=discord.ButtonStyle.primary,
        custom_id="aerin:register",
    )
    async def register(self, interaction, button):
        await interaction.response.send_modal(RegisterModal())

    @discord.ui.button(
        label="Get Key",
        style=discord.ButtonStyle.success,
        custom_id="aerin:getkey",
    )
    async def get_key(self, interaction, button):

        account = get_account(interaction.user.id)

        if account is None:
            await interaction.response.send_message(
                "Register your Aerin account first.",
                ephemeral=True,
            )
            return

        await interaction.response.send_message(
            "**Your Aerin account**\n"
            f"Username: `{account['username']}`\n"
            f"Nickname: `{account['nickname']}`\n"
            f"Key: `{account['user_key']}`\n\n"
            "Keep your key private.",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Get Script",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:getscript",
    )
    async def get_script(self, interaction, button):

        account = get_account(interaction.user.id)

        if account is None:
            await interaction.response.send_message(
                "Register your Aerin account first.",
                ephemeral=True,
            )
            return

        account_info = (
            "-- Aerin account information\n"
            f'local USERNAME = "{account["username"]}"\n'
            f'local NICKNAME = "{account["nickname"]}"\n'
            f'local KEY = "{account["user_key"]}"\n'
            'print("Aerin account registered for " .. USERNAME)\n'
        )

        await interaction.response.send_message(
            "Your registered Aerin account details:\n"
            f"```lua\n{account_info}```",
            ephemeral=True,
        )

    @discord.ui.button(
        label="Reset HWID",
        style=discord.ButtonStyle.danger,
        custom_id="aerin:reset",
    )
    async def reset_hwid(self, interaction, button):

        if not is_registered(interaction.user.id):
            await interaction.response.send_message(
                "Register your Aerin account first.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        success, message = await reset_device_binding(
            interaction.user.id
        )

        await interaction.followup.send(
            message,
            ephemeral=True,
        )


# ============================================================
# BOT
# ============================================================

class AerinBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self):
        setup_database()

        self.add_view(AerinPanel())

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)

            self.tree.copy_global_to(guild=guild)

            synced = await self.tree.sync(guild=guild)

            log.info(
                "Synced %s commands to guild %s",
                len(synced),
                GUILD_ID,
            )
        else:
            synced = await self.tree.sync()
            log.info("Synced %s global commands", len(synced))


bot = AerinBot()


@bot.event
async def on_ready():
    log.info("Aerin online as %s", bot.user)


# ============================================================
# /PANEL
# ============================================================

@bot.tree.command(
    name="panel",
    description="Post the Aerin account panel",
)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):

    embed = discord.Embed(
        title="aerin panel",
        description=(
            "Register your Aerin account and manage your account details.\n"
            "Use the buttons below to continue."
        ),
        color=discord.Color.from_rgb(242, 190, 207),
    )

    embed.set_footer(text="Aerin account panel")

    await interaction.response.send_message(
        embed=embed,
        view=AerinPanel(),
    )


# ============================================================
# /GENERATEKEY
# ============================================================

@bot.tree.command(
    name="generatekey",
    description="Register a member and generate their Aerin key",
)
@app_commands.guild_only()
@app_commands.describe(
    member="The Discord member to register",
    nickname="Optional Aerin nickname",
)
@app_commands.default_permissions(administrator=True)
async def generatekey(
    interaction: discord.Interaction,
    member: discord.Member,
    nickname: str = None,
):

    if not interaction.user.guild_permissions.administrator:
        await interaction.response.send_message(
            "Only administrators can use this command.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(ephemeral=True)

    account, created = register_account(member, nickname)

    try:
        await member.send(
            "**Your Aerin account details**\n"
            f"Username: `{account['username']}`\n"
            f"Nickname: `{account['nickname']}`\n"
            f"Key: `{account['user_key']}`\n\n"
            "Keep this key private.",
        )

        dm_result = "The account details were sent to the member's DMs."

    except discord.Forbidden:
        dm_result = (
            "I could not DM the member. "
            "They can use Get Key in the panel."
        )

    status = (
        "Created a new account."
        if created
        else "Updated the existing account."
    )

    await interaction.followup.send(
        f"{status} {dm_result}",
        ephemeral=True,
    )


# ============================================================
# ERROR HANDLING
# ============================================================

@panel.error
@generatekey.error
async def command_error(interaction, error):

    if isinstance(error, app_commands.MissingPermissions):
        message = "Only administrators can use this command."

    elif isinstance(error, app_commands.CheckFailure):
        message = "You do not have permission to use this command."

    else:
        log.error(
            "Slash command failed",
            exc_info=(type(error), error, error.__traceback__),
        )
        message = "Something went wrong. Check Render logs."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True,
            )

    except discord.HTTPException:
        log.exception("Could not send command error response")


# ============================================================
# START BOT
# ============================================================

if __name__ == "__main__":
    bot.run(TOKEN)
