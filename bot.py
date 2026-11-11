
import asyncio
import logging
import os
import secrets
import sqlite3
import string

import discord
from aiohttp import web
from discord import app_commands
from discord.ext import commands


# CONFIGURATION

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
PORT = int(os.getenv("PORT", "10000"))
DATABASE = os.getenv("DATABASE_PATH", "aerin.sqlite3")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

log = logging.getLogger("aerin")
intents = discord.Intents.default()


# DATABASE

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
    value = "".join(secrets.choice(alphabet) for _ in range(16))
    return (
        f"AERIN-{value[:4]}-{value[4:8]}-"
        f"{value[8:12]}-{value[12:]}"
    )


def make_unique_key(con):
    while True:
        key = make_key()
        exists = con.execute(
            "SELECT 1 FROM accounts WHERE user_key = ?",
            (key,),
        ).fetchone()

        if not exists:
            return key


def get_account(discord_id):
    with db() as con:
        return con.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),),
        ).fetchone()


def register_account(user, nickname=None):
    display_name = nickname or getattr(
        user, "display_name", user.name
    )

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

            account = con.execute(
                "SELECT * FROM accounts WHERE discord_id = ?",
                (str(user.id),),
            ).fetchone()

            return account, False

        key = make_unique_key(con)

        con.execute(
            """
            INSERT INTO accounts
            (discord_id, username, nickname, user_key)
            VALUES (?, ?, ?, ?)
            """,
            (str(user.id), user.name, display_name, key),
        )

        account = con.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(user.id),),
        ).fetchone()

        return account, True


# AERIN API

async def health(request):
    return web.json_response({
        "success": True,
        "service": "Aerin",
        "status": "online",
        "message": "Aerin API is running.",
        "verification_endpoint": "/verify",
        "method": "POST",
    })


async def load_info(request):
    return web.json_response({
        "success": True,
        "service": "Aerin",
        "status": "online",
    })


async def verify_account(request):
    try:
        payload = await request.json()
    except (ValueError, TypeError):
        return web.json_response(
            {"success": False, "reason": "Invalid JSON request"},
            status=400,
        )

    if not isinstance(payload, dict):
        return web.json_response(
            {"success": False, "reason": "Invalid request body"},
            status=400,
        )

    username = payload.get("username")
    key = payload.get("key")

    if not isinstance(username, str) or not isinstance(key, str):
        return web.json_response(
            {"success": False, "reason": "Username and key are required"},
            status=400,
        )

    username = username.strip()
    key = key.strip()

    if (
        not username
        or not key
        or len(username) > 64
        or len(key) > 64
    ):
        return web.json_response(
            {"success": False, "reason": "Invalid username or key"},
            status=400,
        )

    with db() as con:
        account = con.execute(
            """
            SELECT username, nickname
            FROM accounts
            WHERE lower(username) = lower(?)
            AND user_key = ?
            """,
            (username, key),
        ).fetchone()

    if account is None:
        return web.json_response({
            "success": False,
            "reason": "Invalid username or key",
        })

    return web.json_response({
        "success": True,
        "username": account["username"],
        "nickname": account["nickname"],
        "message": "Aerin account verified",
    })


def create_api_app():
    app = web.Application(client_max_size=16 * 1024)
    app.router.add_get("/", health)
    app.router.add_get("/health", health)
    app.router.add_get("/load/aerin", load_info)
    app.router.add_post("/verify", verify_account)
    return app


# REGISTER MODAL

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

        message = (
            "Your Aerin account has been created."
            if created
            else "Your Aerin account has been updated."
        )

        await interaction.response.send_message(
            f"{message}\n"
            f"Username: `{account['username']}`\n"
            f"Nickname: `{account['nickname']}`\n"
            "Use Get Key to view your account key.",
            ephemeral=True,
        )


# AERIN PANEL

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
        label="Account Status",
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

        embed = discord.Embed(
            title="Aerin account verified",
            description=(
                "Your Aerin account has been successfully verified."
            ),
            color=discord.Color.from_rgb(242, 190, 207),
        )

        embed.add_field(
            name="Username",
            value=account["username"],
            inline=True,
        )
        embed.add_field(
            name="Nickname",
            value=account["nickname"] or "Not set",
            inline=True,
        )
        embed.add_field(
            name="Account status",
            value="Verified",
            inline=False,
        )
        embed.set_footer(text="Aerin account system")

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True,
        )


# BOT

class AerinBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents,
        )

    async def setup_hook(self):
        self.add_view(AerinPanel())

        try:
            if GUILD_ID:
                guild = discord.Object(id=GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
            else:
                synced = await self.tree.sync()

            log.info("Synced %s application commands", len(synced))

        except discord.HTTPException:
            log.exception("Could not sync application commands")


bot = AerinBot()


@bot.event
async def on_ready():
    log.info("Aerin online as %s", bot.user)


# /PANEL

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
            "Register your Aerin account, view your key, "
            "and check your account status.\n"
            "Choose an option below to continue."
        ),
        color=discord.Color.from_rgb(242, 190, 207),
    )

    embed.set_footer(text="Aerin account panel")

    await interaction.response.send_message(
        embed=embed,
        view=AerinPanel(),
    )


# /GENERATEKEY

@bot.tree.command(
    name="generatekey",
    description="Generate an Aerin account for a member",
)
@app_commands.guild_only()
@app_commands.default_permissions(administrator=True)
@app_commands.describe(
    member="The Discord member",
    nickname="Optional Aerin nickname",
)
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
            "Keep your key private.",
        )

        dm_message = "The account details were sent to their DMs."

    except discord.Forbidden:
        dm_message = (
            "I couldn't DM the member. "
            "They can use Get Key in the panel."
        )

    status = (
        "Created a new account."
        if created
        else "Updated the account."
    )

    await interaction.followup.send(
        f"{status} {dm_message}",
        ephemeral=True,
    )


# COMMAND ERROR HANDLING

@panel.error
@generatekey.error
async def command_error(interaction, error):

    if isinstance(error, app_commands.MissingPermissions):
        message = "Only administrators can use this command."
    elif isinstance(error, app_commands.CheckFailure):
        message = "You do not have permission to use this command."
    else:
        log.error(
            "Command failed",
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
        log.exception("Could not send error response")


# START API BEFORE DISCORD LOGIN

async def start_api():
    setup_database()

    app = create_api_app()
    runner = web.AppRunner(app)

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT,
    )

    await site.start()

    log.info("Aerin API listening on port %s", PORT)
    return runner


async def main():
    runner = await start_api()

    log.info("Starting Aerin Discord bot...")

    try:
        await bot.start(TOKEN)

    except discord.HTTPException as error:
        if error.status == 429:
            log.error(
                "Discord returned HTTP 429. "
                "No automatic login retries will be made. "
                "The Aerin API will remain online."
            )
        else:
            log.exception(
                "Discord HTTP error during startup: %s",
                error.status,
            )

        await asyncio.Event().wait()

    except discord.LoginFailure:
        log.exception(
            "Discord rejected the configured bot token. "
            "Check the DISCORD_TOKEN environment variable."
        )
        await asyncio.Event().wait()

    finally:
        await bot.close()
        await runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Aerin shutting down.")
