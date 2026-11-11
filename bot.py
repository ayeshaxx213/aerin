
import asyncio
import hashlib
import hmac
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

PASSWORD_ITERATIONS = 310_000
PINK = discord.Color.from_rgb(245, 174, 204)

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

        columns = {
            row["name"]
            for row in db.execute(
                "PRAGMA table_info(accounts)"
            ).fetchall()
        }

        if "password_salt" not in columns:
            db.execute(
                "ALTER TABLE accounts ADD COLUMN password_salt TEXT"
            )

        if "password_hash" not in columns:
            db.execute(
                "ALTER TABLE accounts ADD COLUMN password_hash TEXT"
            )

        db.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_accounts_username_nocase
            ON accounts(username COLLATE NOCASE)
        """)

        db.commit()


def make_key():
    alphabet = string.ascii_uppercase + string.digits
    groups = [
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    ]
    return "AERIN-" + "-".join(groups)


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS
    )
    return salt.hex(), digest.hex()


def verify_password(password, salt_hex, expected_hash):
    if not salt_hex or not expected_hash:
        return False

    try:
        salt = bytes.fromhex(salt_hex)
    except ValueError:
        return False

    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PASSWORD_ITERATIONS
    )

    return hmac.compare_digest(digest.hex(), expected_hash)


def get_account(discord_id):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),)
        ).fetchone()


def get_account_by_username(username):
    with db_connect() as db:
        return db.execute(
            """
            SELECT * FROM accounts
            WHERE username = ? COLLATE NOCASE
            """,
            (username.strip(),)
        ).fetchone()


def authenticate_account(username, password):
    account = get_account_by_username(username)

    if not account:
        return None

    if not verify_password(
        password,
        account["password_salt"],
        account["password_hash"]
    ):
        return None

    return account


def register_account(discord_id, username, nickname, password):
    salt, password_hash = hash_password(password)
    now = datetime.now(timezone.utc).isoformat()

    with db_connect() as db:
        existing = db.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),)
        ).fetchone()

        username_owner = db.execute(
            """
            SELECT discord_id FROM accounts
            WHERE username = ? COLLATE NOCASE
            AND discord_id != ?
            """,
            (username, str(discord_id))
        ).fetchone()

        if username_owner:
            raise ValueError("USERNAME_TAKEN")

        if existing:
            db.execute(
                """
                UPDATE accounts
                SET username = ?,
                    nickname = ?,
                    password_salt = ?,
                    password_hash = ?
                WHERE discord_id = ?
                """,
                (
                    username,
                    nickname,
                    salt,
                    password_hash,
                    str(discord_id)
                )
            )
        else:
            db.execute(
                """
                INSERT INTO accounts (
                    discord_id,
                    username,
                    nickname,
                    user_key,
                    created_at,
                    password_salt,
                    password_hash
                )
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(discord_id),
                    username,
                    nickname,
                    make_key(),
                    now,
                    salt,
                    password_hash
                )
            )

        db.commit()

    return get_account(discord_id)


def create_admin_account(discord_id, nickname):
    now = datetime.now(timezone.utc).isoformat()

    with db_connect() as db:
        existing = db.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),)
        ).fetchone()

        if existing:
            db.execute(
                """
                UPDATE accounts
                SET nickname = ?
                WHERE discord_id = ?
                """,
                (nickname, str(discord_id))
            )
        else:
            db.execute(
                """
                INSERT INTO accounts (
                    discord_id,
                    username,
                    nickname,
                    user_key,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    str(discord_id),
                    f"user_{discord_id}",
                    nickname,
                    make_key(),
                    now
                )
            )

        db.commit()

    return get_account(discord_id)


def reset_account_access(discord_id):
    """Clear password credentials while keeping the account and key."""
    with db_connect() as db:
        account = db.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),)
        ).fetchone()

        if not account:
            return None

        db.execute(
            """
            UPDATE accounts
            SET password_salt = NULL,
                password_hash = NULL
            WHERE discord_id = ?
            """,
            (str(discord_id),)
        )

        db.commit()

    return get_account(discord_id)


# ============================================================
# EMBEDS
# ============================================================

def account_embed(account, title="Aerin account"):
    embed = discord.Embed(
        title=title,
        color=PINK,
        timestamp=datetime.now(timezone.utc)
    )

    embed.add_field(
        name="Username",
        value=account["username"],
        inline=True
    )

    embed.add_field(
        name="Nickname",
        value=account["nickname"],
        inline=True
    )

    embed.add_field(
        name="Account status",
        value=(
            "Ready"
            if account["password_hash"]
            else "Password setup required"
        ),
        inline=False
    )

    embed.set_footer(text="Aerin account system")
    return embed


# ============================================================
# REGISTER MODAL
# ============================================================

class RegisterModal(discord.ui.Modal, title="Register Aerin account"):

    username = discord.ui.TextInput(
        label="Username",
        placeholder="Choose your Aerin username",
        min_length=3,
        max_length=24,
        required=True
    )

    nickname = discord.ui.TextInput(
        label="Nickname",
        placeholder="Enter your nickname",
        min_length=2,
        max_length=32,
        required=True
    )

    password = discord.ui.TextInput(
        label="Password",
        placeholder="Create a unique Aerin password",
        min_length=10,
        max_length=72,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        username = str(self.username.value).strip()
        nickname = str(self.nickname.value).strip()
        password = str(self.password.value)

        if not username.replace("_", "").replace("-", "").isalnum():
            await interaction.response.send_message(
                "Use only letters, numbers, underscores, and hyphens.",
                ephemeral=True
            )
            return

        try:
            account = register_account(
                interaction.user.id,
                username,
                nickname,
                password
            )

        except ValueError as error:
            if str(error) == "USERNAME_TAKEN":
                await interaction.response.send_message(
                    "That username is already taken.",
                    ephemeral=True
                )
                return
            raise

        except sqlite3.Error:
            log.exception("Registration database error")
            await interaction.response.send_message(
                "A database error occurred. Please try again.",
                ephemeral=True
            )
            return

        embed = account_embed(
            account,
            "Aerin registration complete"
        )

        embed.description = (
            "Your Aerin account has been registered successfully."
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


# ============================================================
# RESET ACCESS CONFIRMATION
# ============================================================

class ResetAccessConfirmView(discord.ui.View):

    def __init__(self, requester_id, target):
        super().__init__(timeout=60)
        self.requester_id = requester_id
        self.target = target

    async def interaction_check(self, interaction):
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Only the administrator who started this reset can confirm it.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="Confirm reset",
        style=discord.ButtonStyle.danger,
        custom_id="aerin:confirm_reset"
    )
    async def confirm_reset(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        try:
            account = reset_account_access(self.target.id)

        except sqlite3.Error:
            log.exception("Account access reset failed")
            await interaction.response.edit_message(
                content="A database error occurred. The reset could not be completed.",
                embed=None,
                view=None
            )
            return

        if not account:
            await interaction.response.edit_message(
                content="This member does not have an Aerin account.",
                embed=None,
                view=None
            )
            return

        for child in self.children:
            child.disabled = True

        embed = discord.Embed(
            title="Aerin access reset",
            description=(
                f"Access credentials for {self.target.mention} "
                "have been reset."
            ),
            color=PINK
        )

        embed.add_field(
            name="Username",
            value=account["username"],
            inline=True
        )

        embed.add_field(
            name="Key",
            value="Unchanged",
            inline=True
        )

        embed.add_field(
            name="Next step",
            value="The member must use Register to create a new password.",
            inline=False
        )

        await interaction.response.edit_message(
            content=None,
            embed=embed,
            view=self
        )

        try:
            await self.target.send(
                "Your Aerin account access has been reset by an administrator. "
                "Use the Aerin Register button to set up a new password."
            )
        except discord.Forbidden:
            pass

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:cancel_reset"
    )
    async def cancel_reset(
        self,
        interaction: discord.Interaction,
        button: discord.ui.Button
    ):
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="Account access reset cancelled.",
            embed=None,
            view=self
        )

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ============================================================
# DISCORD PANEL
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
                "Register your Aerin account first.",
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
                "Register your Aerin account first.",
                ephemeral=True
            )
            return

        username = (
            account["username"]
            .replace("\\", "\\\\")
            .replace('"', '\\"')
        )

        key = (
            account["user_key"]
            .replace("\\", "\\\\")
            .replace('"', '\\"')
        )

        loader = (
            "-- Aerin Main : -- /aerin Loader\n"
            f'local USERNAME = "{username}"\n'
            f'local KEY = "{key}"\n'
            f'local URL = "{PUBLIC_URL}/load/aerin"\n'
            'local HttpService = game:GetService("HttpService")\n'
            "\n"
            'print("[/aerin] Aerin account details loaded.")\n'
            'print("[/aerin] Username:", USERNAME)\n'
            'print("[/aerin] Verification endpoint:", URL)\n'
            'print("[/aerin] Keep your key private.")'
        )

        await interaction.response.send_message(
            content=f"```lua\n{loader}\n```",
            ephemeral=True
        )


# ============================================================
# BOT
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
            else:
                synced = await self.tree.sync()

            log.info("Synced %s slash commands.", len(synced))

        except discord.HTTPException:
            log.exception("Could not sync slash commands.")

    async def on_ready(self):
        log.info("Aerin connected as %s", self.user)

    async def close(self):
        if self.web_runner:
            await self.web_runner.cleanup()
            self.web_runner = None

        await super().close()


bot = AerinBot()


# ============================================================
# SLASH COMMANDS
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
            "Register your Aerin username and password, "
            "retrieve your key, or get your copyable Lua snippet."
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
    description="Create an Aerin account record for a member."
)
@app_commands.describe(
    member="The Discord member",
    nickname="The Aerin nickname"
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
        account = create_admin_account(
            member.id,
            nickname
        )

    except sqlite3.Error:
        log.exception("Account creation error")
        await interaction.response.send_message(
            "A database error occurred.",
            ephemeral=True
        )
        return

    embed = account_embed(
        account,
        "Aerin account created"
    )

    embed.add_field(
        name="Key",
        value=f"```{account['user_key']}```",
        inline=False
    )

    try:
        await member.send(embed=embed)
        message = "Account details were sent by DM."
    except discord.Forbidden:
        message = "The member can retrieve their key using Get Key."

    await interaction.response.send_message(
        f"Account created for {member.mention}. {message}",
        ephemeral=True
    )


@bot.tree.command(
    name="resetaccess",
    description="Reset a member's Aerin password access."
)
@app_commands.describe(
    member="The member whose Aerin access should be reset"
)
@app_commands.checks.has_permissions(administrator=True)
async def resetaccess(
    interaction: discord.Interaction,
    member: discord.Member
):
    account = get_account(member.id)

    if not account:
        await interaction.response.send_message(
            "That member does not have an Aerin account.",
            ephemeral=True
        )
        return

    if not account["password_hash"]:
        await interaction.response.send_message(
            "This account already requires password setup. "
            "The member can use Register to set a password.",
            ephemeral=True
        )
        return

    embed = discord.Embed(
        title="Confirm Aerin access reset",
        description=(
            f"Are you sure you want to reset access for {member.mention}?\n\n"
            "Their password credentials will be cleared. "
            "Their username, nickname, key, and account record will remain."
        ),
        color=PINK
    )

    await interaction.response.send_message(
        embed=embed,
        view=ResetAccessConfirmView(
            interaction.user.id,
            member
        ),
        ephemeral=True
    )


# ============================================================
# API
# ============================================================

def json_response(data, status=200):
    return web.json_response(data, status=status)


async def home(request):
    return json_response({
        "service": "Aerin",
        "status": "online"
    })


async def health(request):
    return json_response({
        "service": "Aerin",
        "status": "healthy"
    })


async def authenticate_request(request):

    try:
        payload = await request.json()

    except (json.JSONDecodeError, ValueError):
        return None, json_response({
            "success": False,
            "reason": "Invalid JSON."
        }, 400)

    if not isinstance(payload, dict):
        return None, json_response({
            "success": False,
            "reason": "JSON body must be an object."
        }, 400)

    username = str(payload.get("username", "")).strip()
    password = str(payload.get("password", ""))

    if not username or not password:
        return None, json_response({
            "success": False,
            "reason": "Username and password are required."
        }, 400)

    account = authenticate_account(username, password)

    if not account:
        return None, json_response({
            "success": False,
            "reason": "Invalid username or password."
        }, 401)

    return account, None


async def verify(request):

    account, error = await authenticate_request(request)

    if error:
        return error

    return json_response({
        "success": True,
        "username": account["username"],
        "nickname": account["nickname"],
        "status": "verified"
    })


async def load_aerin(request):

    if request.method == "GET":
        return json_response({
            "service": "Aerin",
            "endpoint": "/load/aerin",
            "method": "POST",
            "required_fields": ["username", "password"],
            "status": "online",
            "note": "Aerin account verification endpoint."
        })

    account, error = await authenticate_request(request)

    if error:
        return error

    return json_response({
        "success": True,
        "username": account["username"],
        "nickname": account["nickname"],
        "status": "verified",
        "message": "Aerin account verified successfully."
    })


def create_web_app():

    app = web.Application(client_max_size=16 * 1024)

    app.router.add_get("/", home)
    app.router.add_get("/health", health)
    app.router.add_post("/verify", verify)

    app.router.add_get("/load/aerin", load_aerin)
    app.router.add_post("/load/aerin", load_aerin)

    return app


async def start_api():

    app = create_web_app()
    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    bot.web_runner = runner

    log.info("Aerin API listening on port %s", PORT)


# ============================================================
# STARTUP
# ============================================================

async def main():

    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing. Add it in Render environment variables."
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
