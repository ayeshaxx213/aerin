
import asyncio
import json
import logging
import os
import secrets
import sqlite3
import string
from datetime import datetime, timezone

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands


# ============================================================
# AERIN CONFIGURATION
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
PORT = int(os.getenv("PORT", "10000") or "10000")
DATABASE_PATH = os.getenv("DATABASE_PATH", "aerin.sqlite3")

PUBLIC_URL = os.getenv(
    "AERIN_PUBLIC_URL",
    "https://aerin-2.onrender.com"
).rstrip("/")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s"
)
log = logging.getLogger("aerin")


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
    with db_connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                discord_id TEXT PRIMARY KEY,
                username TEXT NOT NULL,
                nickname TEXT NOT NULL,
                user_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL
            )
        """)
        db.execute("""
            CREATE TABLE IF NOT EXISTS keys (
                key TEXT PRIMARY KEY,
                redeemed_by TEXT,
                redeemed_at TEXT
            )
        """)
        db.commit()


def make_key():
    alphabet = string.ascii_uppercase + string.digits
    groups = [
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    ]
    return "AERIN-" + "-".join(groups)


def get_account(discord_id):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),)
        ).fetchone()


def get_account_by_credentials(username, key):
    with db_connect() as db:
        return db.execute(
            """
            SELECT * FROM accounts
            WHERE lower(username) = lower(?) AND user_key = ?
            """,
            (username.strip(), key.strip())
        ).fetchone()


def create_or_update_account(discord_id, username, nickname):
    existing = get_account(discord_id)
    now = datetime.now(timezone.utc).isoformat()

    if existing:
        with db_connect() as db:
            db.execute(
                """
                UPDATE accounts
                SET username = ?, nickname = ?
                WHERE discord_id = ?
                """,
                (username, nickname, str(discord_id))
            )
            db.commit()
        return get_account(discord_id), False

    key = make_key()
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO accounts
            (discord_id, username, nickname, user_key, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (str(discord_id), username, nickname, key, now)
        )
        db.commit()

    return get_account(discord_id), True


# ============================================================
# DISCORD EMBEDS
# ============================================================

PINK = discord.Color.from_rgb(245, 174, 204)


def account_embed(account, title="Aerin account"):
    embed = discord.Embed(
        title=title,
        color=PINK,
        timestamp=datetime.now(timezone.utc)
    )
    embed.add_field(name="Username", value=account["username"], inline=True)
    embed.add_field(name="Nickname", value=account["nickname"], inline=True)
    embed.add_field(name="Account status", value="Registered", inline=False)
    embed.set_footer(text="Aerin account system")
    return embed


# ============================================================
# REGISTER MODAL
# ============================================================

class RegisterModal(discord.ui.Modal, title="Register Aerin account"):
    nickname = discord.ui.TextInput(
        label="Nickname",
        placeholder="Enter your Aerin nickname",
        min_length=2,
        max_length=32
    )

    async def on_submit(self, interaction: discord.Interaction):
        username = interaction.user.name
        nickname = str(self.nickname.value).strip()

        try:
            account, created = create_or_update_account(
                interaction.user.id,
                username,
                nickname
            )
        except sqlite3.Error:
            log.exception("Could not save account")
            await interaction.response.send_message(
                "A database error occurred. Please try again later.",
                ephemeral=True
            )
            return

        message = (
            "Your Aerin account has been created."
            if created
            else "Your Aerin account has been updated."
        )

        await interaction.response.send_message(
            content=message,
            embed=account_embed(account),
            ephemeral=True
        )


# ============================================================
# PANEL BUTTONS
# ============================================================

class AerinPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:register"
    )
    async def register_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        await interaction.response.send_modal(RegisterModal())

    @discord.ui.button(
        label="Get Key",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:getkey"
    )
    async def get_key_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        account = get_account(interaction.user.id)

        if not account:
            await interaction.response.send_message(
                "You do not have an Aerin account yet. Press Register first.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Your Aerin key",
            description=f"```{account['user_key']}```",
            color=PINK
        )
        embed.add_field(
            name="Username",
            value=account["username"],
            inline=True
        )
        embed.set_footer(text="Keep your key private.")

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @discord.ui.button(
        label="Get Script",
        style=discord.ButtonStyle.primary,
        custom_id="aerin:getscript"
    )
    async def get_script_button(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        account = get_account(interaction.user.id)

        if not account:
            await interaction.response.send_message(
                "You do not have an Aerin account yet. Press Register first.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Aerin resource",
            description=(
                "Your account is registered. Use the Aerin resource link "
                "below to view the account service response."
            ),
            color=PINK
        )
        embed.add_field(
            name="Username",
            value=account["username"],
            inline=True
        )
        embed.add_field(
            name="Status",
            value="Verified",
            inline=True
        )
        embed.add_field(
            name="Resource",
            value=f"[Open Aerin resource]({PUBLIC_URL}/load/aerin)",
            inline=False
        )
        embed.set_footer(text="Aerin account system")

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()


class AerinBot(commands.Bot):
    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )
        self.web_runner = None

    async def setup_hook(self):
        self.add_view(AerinPanel())

        try:
            if GUILD_ID:
                guild = discord.Object(id=GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %s guild commands.", len(synced))
            else:
                synced = await self.tree.sync()
                log.info("Synced %s global commands.", len(synced))
        except discord.HTTPException:
            log.exception("Could not sync application commands.")

    async def on_ready(self):
        log.info("Aerin Discord bot connected as %s", self.user)

    async def close(self):
        if self.web_runner:
            await self.web_runner.cleanup()
            self.web_runner = None
        await super().close()


bot = AerinBot()


# ============================================================
# DISCORD SLASH COMMANDS
# ============================================================

@bot.tree.command(
    name="panel",
    description="Post the Aerin account panel."
)
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Aerin",
        description=(
            "Register your account, retrieve your key, "
            "or open the Aerin resource."
        ),
        color=PINK
    )
    embed.set_footer(text="Aerin account system")

    await interaction.response.send_message(
        embed=embed,
        view=AerinPanel()
    )


@bot.tree.command(
    name="generatekey",
    description="Create or update an Aerin account for a server member."
)
@app_commands.describe(
    member="The Discord member",
    nickname="The Aerin nickname for this account"
)
@app_commands.checks.has_permissions(administrator=True)
async def generatekey(
    interaction: discord.Interaction,
    member: discord.Member,
    nickname: str
):
    nickname = nickname.strip()

    if len(nickname) < 2 or len(nickname) > 32:
        await interaction.response.send_message(
            "Nickname must be between 2 and 32 characters.",
            ephemeral=True
        )
        return

    try:
        account, created = create_or_update_account(
            member.id,
            member.name,
            nickname
        )
    except sqlite3.Error:
        log.exception("Could not create account")
        await interaction.response.send_message(
            "A database error occurred.",
            ephemeral=True
        )
        return

    status = "created" if created else "updated"
    embed = account_embed(account, "Aerin key generated")
    embed.add_field(
        name="Key",
        value=f"```{account['user_key']}```",
        inline=False
    )

    try:
        await member.send(embed=embed)
        dm_status = "The account was created and its details were sent by DM."
    except discord.Forbidden:
        dm_status = (
            "The account was created, but I could not DM the member. "
            "They can use Get Key in the panel."
        )

    await interaction.response.send_message(
        f"Account {status} for {member.mention}. {dm_status}",
        ephemeral=True
    )


# ============================================================
# API RESPONSE HELPERS
# ============================================================

def json_response(data, status=200):
    return web.json_response(data, status=status)


async def home(request):
    return json_response({
        "service": "Aerin",
        "status": "online",
        "message": "Aerin account API is running."
    })


async def health(request):
    return json_response({
        "service": "Aerin",
        "status": "healthy"
    })


async def verify(request):
    try:
        payload = await request.json()
    except (json.JSONDecodeError, ValueError):
        return json_response({
            "success": False,
            "reason": "Invalid JSON."
        }, 400)

    username = str(payload.get("username", "")).strip()
    key = str(payload.get("key", "")).strip()

    if not username or not key:
        return json_response({
            "success": False,
            "reason": "Username and key are required."
        }, 400)

    account = get_account_by_credentials(username, key)

    if not account:
        return json_response({
            "success": False,
            "reason": "Invalid username or key."
        }, 401)

    return json_response({
        "success": True,
        "username": account["username"],
        "nickname": account["nickname"],
        "status": "verified"
    })


async def load_aerin(request):
    # Returns account-service information only.
    # Does not return executable code or an executor payload.
    return json_response({
        "success": True,
        "service": "Aerin",
        "status": "online",
        "message": "Aerin resource endpoint is working.",
        "verification_endpoint": f"{PUBLIC_URL}/verify"
    })


def create_web_app():
    app = web.Application(client_max_size=16 * 1024)
    app.router.add_get("/", home)
    app.router.add_get("/health", health)
    app.router.add_post("/verify", verify)
    app.router.add_get("/load/aerin", load_aerin)
    return app


async def start_api():
    app = create_web_app()
    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()

    bot.web_runner = runner
    log.info("Aerin API listening on port %s", PORT)


# ============================================================
# STARTUP
# ============================================================

async def main():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Add it in your Render environment variables."
        )

    init_database()
    await start_api()

    log.info("Starting Aerin Discord bot...")
    await bot.start(TOKEN)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Aerin stopped.")
