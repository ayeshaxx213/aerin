
import asyncio
import hashlib
import logging
import os
import secrets
import sqlite3
import string
import time
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

logger = logging.getLogger("aerin")


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_db():
    with db_connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                discord_id TEXT PRIMARY KEY,
                username TEXT NOT NULL COLLATE NOCASE UNIQUE,
                nickname TEXT NOT NULL,
                user_key TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                password_salt TEXT,
                password_hash TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id TEXT PRIMARY KEY,
                welcome_channel_id TEXT,
                welcome_message TEXT
            )
        """)

        db.execute("""
            CREATE TABLE IF NOT EXISTS warnings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                moderator_id TEXT NOT NULL,
                reason TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

        # Migrate older account databases.
        columns = {
            row["name"]
            for row in db.execute("PRAGMA table_info(accounts)").fetchall()
        }

        if "password_salt" not in columns:
            db.execute("ALTER TABLE accounts ADD COLUMN password_salt TEXT")

        if "password_hash" not in columns:
            db.execute("ALTER TABLE accounts ADD COLUMN password_hash TEXT")

        db.commit()


# ============================================================
# ACCOUNT SYSTEM
# ============================================================

def make_key():
    alphabet = string.ascii_uppercase + string.digits

    parts = [
        "".join(secrets.choice(alphabet) for _ in range(4))
        for _ in range(4)
    ]

    return "AERIN-" + "-".join(parts)


def hash_password(password, salt=None):
    if salt is None:
        salt = secrets.token_hex(16)

    password_hash = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        bytes.fromhex(salt),
        PASSWORD_ITERATIONS
    ).hex()

    return salt, password_hash


def verify_password(password, salt, expected_hash):
    if not salt or not expected_hash:
        return False

    _, actual_hash = hash_password(password, salt)

    return secrets.compare_digest(actual_hash, expected_hash)


def get_account(discord_id):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),)
        ).fetchone()


def get_account_by_username(username):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM accounts WHERE username = ? COLLATE NOCASE",
            (username.strip(),)
        ).fetchone()


def get_account_by_key(user_key):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM accounts WHERE user_key = ?",
            (user_key.strip().upper(),)
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
    username = username.strip()
    nickname = nickname.strip()

    if not username or not nickname:
        raise ValueError("Username and nickname are required.")

    if len(username) < 3 or len(username) > 24:
        raise ValueError("Username must be 3–24 characters.")

    if not all(c.isalnum() or c in "_-" for c in username):
        raise ValueError(
            "Username can only contain letters, numbers, _ and -."
        )

    if len(nickname) > 32:
        raise ValueError("Nickname must be 32 characters or less.")

    if len(password) < 10 or len(password) > 72:
        raise ValueError("Password must be 10–72 characters.")

    salt, password_hash = hash_password(password)

    with db_connect() as db:
        existing = db.execute(
            "SELECT * FROM accounts WHERE discord_id = ?",
            (str(discord_id),)
        ).fetchone()

        if existing:
            duplicate = db.execute(
                """
                SELECT discord_id FROM accounts
                WHERE username = ? COLLATE NOCASE
                AND discord_id != ?
                """,
                (username, str(discord_id))
            ).fetchone()

            if duplicate:
                raise ValueError("That username is already taken.")

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

            db.commit()
            return existing["user_key"], False

        user_key = make_key()

        for _ in range(10):
            try:
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
                        user_key,
                        datetime.now(timezone.utc).isoformat(),
                        salt,
                        password_hash
                    )
                )

                db.commit()
                return user_key, True

            except sqlite3.IntegrityError:
                user_key = make_key()

        raise RuntimeError("Could not generate a unique account key.")


def reset_account_access(discord_id):
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
        return account


# ============================================================
# EMBEDS
# ============================================================

def make_embed(title, description=None):
    embed = discord.Embed(
        title=title,
        description=description,
        color=PINK,
        timestamp=datetime.now(timezone.utc)
    )

    embed.set_footer(text="Aerin")
    return embed


# ============================================================
# DISCORD BOT
# ============================================================

intents = discord.Intents.default()
intents.members = True
intents.message_content = False

bot = commands.Bot(
    command_prefix="!",
    intents=intents
)

api_runner = None
start_time = time.monotonic()


# ============================================================
# REGISTRATION MODAL
# ============================================================

class RegisterModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title="Aerin Registration")

        self.username = discord.ui.TextInput(
            label="Username",
            placeholder="Enter your username",
            min_length=3,
            max_length=24
        )

        self.nickname = discord.ui.TextInput(
            label="Nickname",
            placeholder="Enter your nickname",
            max_length=32
        )

        self.password = discord.ui.TextInput(
            label="Password",
            placeholder="At least 10 characters",
            min_length=10,
            max_length=72,
            style=discord.TextStyle.short
        )

        self.add_item(self.username)
        self.add_item(self.nickname)
        self.add_item(self.password)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            key, created = register_account(
                interaction.user.id,
                self.username.value,
                self.nickname.value,
                self.password.value
            )

            embed = make_embed(
                "Registration complete",
                "Your Aerin account is ready."
            )

            embed.add_field(
                name="Username",
                value=self.username.value,
                inline=True
            )

            embed.add_field(
                name="Nickname",
                value=self.nickname.value,
                inline=True
            )

            embed.add_field(
                name="Account key",
                value=f"`{key}`",
                inline=False
            )

            embed.add_field(
                name="Account status",
                value="Created" if created else "Updated",
                inline=False
            )

            await interaction.response.send_message(
                embed=embed,
                ephemeral=True
            )

        except ValueError as error:
            await interaction.response.send_message(
                str(error),
                ephemeral=True
            )

        except Exception:
            logger.exception("Registration failed")

            await interaction.response.send_message(
                "Registration failed. Please try again.",
                ephemeral=True
            )


# ============================================================
# ACCOUNT PANEL
# ============================================================

class AerinPanel(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Register",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:register"
    )
    async def register_button(self, interaction, button):
        account = get_account(interaction.user.id)

        if account:
            await interaction.response.send_message(
                "You already have an Aerin account. Register again to update your username, nickname, or password.",
                ephemeral=True
            )
            return

        await interaction.response.send_modal(RegisterModal())

    @discord.ui.button(
        label="Get Key",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:get_key"
    )
    async def key_button(self, interaction, button):
        account = get_account(interaction.user.id)

        if not account:
            await interaction.response.send_message(
                "You need to register first.",
                ephemeral=True
            )
            return

        embed = make_embed(
            "Your Aerin key",
            f"Your account key is:\n`{account['user_key']}`"
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    @discord.ui.button(
        label="Get Script",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:get_script"
    )
    async def script_button(self, interaction, button):
        account = get_account(interaction.user.id)

        if not account:
            await interaction.response.send_message(
                "You need to register first.",
                ephemeral=True
            )
            return

        embed = make_embed(
            "Aerin account information",
            "Your account is registered. Use the Aerin account verification system in your own Roblox Studio experience."
        )

        embed.add_field(
            name="Username",
            value=account["username"],
            inline=True
        )

        embed.add_field(
            name="API address",
            value=f"`{PUBLIC_URL}/verify`",
            inline=False
        )

        embed.add_field(
            name="Important",
            value="This is an account verification endpoint, not a remote code loader.",
            inline=False
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )


# ============================================================
# RESET ACCESS
# ============================================================

class ResetAccessConfirmView(discord.ui.View):
    def __init__(self, target):
        super().__init__(timeout=60)
        self.target = target

    async def interaction_check(self, interaction):
        if interaction.user.id != self.target.id:
            await interaction.response.send_message(
                "This confirmation belongs to another moderator.",
                ephemeral=True
            )
            return False

        return True

    @discord.ui.button(
        label="Confirm reset",
        style=discord.ButtonStyle.danger
    )
    async def confirm(self, interaction, button):
        account = reset_account_access(self.target.id)

        if not account:
            await interaction.response.edit_message(
                content="This user does not have an account.",
                embed=None,
                view=None
            )
            return

        await interaction.response.edit_message(
            content="Account password access has been reset.",
            embed=None,
            view=None
        )

        try:
            await self.target.send(
                "Your Aerin password access was reset. Register again to set a new password."
            )
        except discord.Forbidden:
            pass

    @discord.ui.button(
        label="Cancel",
        style=discord.ButtonStyle.secondary
    )
    async def cancel(self, interaction, button):
        await interaction.response.edit_message(
            content="Reset cancelled.",
            embed=None,
            view=None
        )


# ============================================================
# WELCOME SETTINGS
# ============================================================

DEFAULT_WELCOME = "Welcome {mention} to **{server}**! You are member #{member_count}."


def get_guild_settings(guild_id):
    with db_connect() as db:
        return db.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (str(guild_id),)
        ).fetchone()


def update_guild_settings(
    guild_id,
    welcome_channel_id=None,
    welcome_message=None
):
    with db_connect() as db:
        existing = db.execute(
            "SELECT * FROM guild_settings WHERE guild_id = ?",
            (str(guild_id),)
        ).fetchone()

        if existing:
            channel_id = (
                welcome_channel_id
                if welcome_channel_id is not None
                else existing["welcome_channel_id"]
            )

            message = (
                welcome_message
                if welcome_message is not None
                else existing["welcome_message"]
            )

            db.execute(
                """
                UPDATE guild_settings
                SET welcome_channel_id = ?,
                    welcome_message = ?
                WHERE guild_id = ?
                """,
                (channel_id, message, str(guild_id))
            )

        else:
            db.execute(
                """
                INSERT INTO guild_settings (
                    guild_id,
                    welcome_channel_id,
                    welcome_message
                )
                VALUES (?, ?, ?)
                """,
                (
                    str(guild_id),
                    welcome_channel_id,
                    welcome_message or DEFAULT_WELCOME
                )
            )

        db.commit()


@bot.event
async def on_member_join(member):
    settings = get_guild_settings(member.guild.id)

    if not settings or not settings["welcome_channel_id"]:
        return

    channel = member.guild.get_channel(
        int(settings["welcome_channel_id"])
    )

    if not channel:
        return

    message = settings["welcome_message"] or DEFAULT_WELCOME

    message = (
        message
        .replace("{mention}", member.mention)
        .replace("{username}", member.name)
        .replace("{server}", member.guild.name)
        .replace("{member_count}", str(member.guild.member_count or 0))
    )

    try:
        await channel.send(
            embed=make_embed("Welcome", message)
        )
    except discord.Forbidden:
        logger.warning(
            "Missing permission to send welcome message in guild %s",
            member.guild.id
        )


# ============================================================
# WARNINGS
# ============================================================

def add_warning(guild_id, user_id, moderator_id, reason):
    with db_connect() as db:
        db.execute(
            """
            INSERT INTO warnings (
                guild_id,
                user_id,
                moderator_id,
                reason,
                created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                str(guild_id),
                str(user_id),
                str(moderator_id),
                reason,
                datetime.now(timezone.utc).isoformat()
            )
        )
        db.commit()


def get_warnings(guild_id, user_id):
    with db_connect() as db:
        return db.execute(
            """
            SELECT * FROM warnings
            WHERE guild_id = ? AND user_id = ?
            ORDER BY id DESC
            LIMIT 25
            """,
            (str(guild_id), str(user_id))
        ).fetchall()


def clear_warnings(guild_id, user_id):
    with db_connect() as db:
        cursor = db.execute(
            """
            DELETE FROM warnings
            WHERE guild_id = ? AND user_id = ?
            """,
            (str(guild_id), str(user_id))
        )
        db.commit()
        return cursor.rowcount


# ============================================================
# MODERATION COMMANDS
# ============================================================

@bot.tree.command(name="clear", description="Delete messages from this channel.")
@app_commands.describe(amount="Number of messages to delete (1–100)")
@app_commands.checks.has_permissions(manage_messages=True)
async def clear(interaction: discord.Interaction, amount: app_commands.Range[int, 1, 100]):
    if not interaction.channel or not hasattr(interaction.channel, "purge"):
        await interaction.response.send_message(
            "This command cannot be used in this channel.",
            ephemeral=True
        )
        return

    await interaction.response.defer(ephemeral=True)

    try:
        deleted = await interaction.channel.purge(
            limit=amount,
            reason=f"Cleared by {interaction.user}"
        )

        await interaction.followup.send(
            f"Deleted {len(deleted)} message(s).",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "Aerin needs the Manage Messages permission.",
            ephemeral=True
        )


@bot.tree.command(name="kick", description="Kick a member from the server.")
@app_commands.describe(member="Member to kick", reason="Reason for the kick")
@app_commands.checks.has_permissions(kick_members=True)
async def kick(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):
    if member.id == interaction.user.id:
        await interaction.response.send_message(
            "You cannot kick yourself.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message(
            "You cannot kick someone with an equal or higher role.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Aerin's role must be higher than the target member's role.",
            ephemeral=True
        )
        return

    try:
        await member.kick(
            reason=f"{reason} | Moderator: {interaction.user}"
        )

        await interaction.response.send_message(
            f"Successfully kicked **{member}**.\nReason: {reason}"
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "Aerin does not have permission to kick this member.",
            ephemeral=True
        )


@bot.tree.command(name="ban", description="Ban a member from the server.")
@app_commands.describe(member="Member to ban", reason="Reason for the ban")
@app_commands.checks.has_permissions(ban_members=True)
async def ban(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str = "No reason provided"
):
    if member.id == interaction.user.id:
        await interaction.response.send_message(
            "You cannot ban yourself.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message(
            "You cannot ban someone with an equal or higher role.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Aerin's role must be higher than the target member's role.",
            ephemeral=True
        )
        return

    try:
        await member.ban(
            reason=f"{reason} | Moderator: {interaction.user}",
            delete_message_seconds=0
        )

        await interaction.response.send_message(
            f"Successfully banned **{member}**.\nReason: {reason}"
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "Aerin does not have permission to ban this member.",
            ephemeral=True
        )


@bot.tree.command(name="unban", description="Unban a user by their Discord ID.")
@app_commands.describe(user_id="The Discord user ID", reason="Reason for unbanning")
@app_commands.checks.has_permissions(ban_members=True)
async def unban(
    interaction: discord.Interaction,
    user_id: str,
    reason: str = "No reason provided"
):
    if not user_id.isdigit():
        await interaction.response.send_message(
            "Enter a valid numeric Discord user ID.",
            ephemeral=True
        )
        return

    try:
        user = await bot.fetch_user(int(user_id))

        await interaction.guild.unban(
            user,
            reason=f"{reason} | Moderator: {interaction.user}"
        )

        await interaction.response.send_message(
            f"Successfully unbanned **{user}**."
        )

    except discord.NotFound:
        await interaction.response.send_message(
            "That user is not banned or could not be found.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "Aerin does not have permission to unban users.",
            ephemeral=True
        )


@bot.tree.command(name="timeout", description="Temporarily timeout a member.")
@app_commands.describe(
    member="Member to timeout",
    minutes="Timeout duration in minutes (1–40320)",
    reason="Reason for the timeout"
)
@app_commands.checks.has_permissions(moderate_members=True)
async def timeout(
    interaction: discord.Interaction,
    member: discord.Member,
    minutes: app_commands.Range[int, 1, 40320],
    reason: str = "No reason provided"
):
    if member.id == interaction.user.id:
        await interaction.response.send_message(
            "You cannot timeout yourself.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.user.top_role and interaction.guild.owner_id != interaction.user.id:
        await interaction.response.send_message(
            "You cannot timeout someone with an equal or higher role.",
            ephemeral=True
        )
        return

    if member.top_role >= interaction.guild.me.top_role:
        await interaction.response.send_message(
            "Aerin's role must be higher than the target member's role.",
            ephemeral=True
        )
        return

    try:
        from datetime import timedelta

        await member.timeout(
            timedelta(minutes=minutes),
            reason=f"{reason} | Moderator: {interaction.user}"
        )

        await interaction.response.send_message(
            f"**{member}** has been timed out for {minutes} minute(s).\nReason: {reason}"
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "Aerin does not have permission to timeout this member.",
            ephemeral=True
        )


@bot.tree.command(name="untimeout", description="Remove a member's timeout.")
@app_commands.describe(member="Member to remove timeout from")
@app_commands.checks.has_permissions(moderate_members=True)
async def untimeout(
    interaction: discord.Interaction,
    member: discord.Member
):
    try:
        await member.timeout(
            None,
            reason=f"Timeout removed by {interaction.user}"
        )

        await interaction.response.send_message(
            f"Timeout removed for **{member}**."
        )

    except discord.Forbidden:
        await interaction.response.send_message(
            "Aerin does not have permission to remove this timeout.",
            ephemeral=True
        )


@bot.tree.command(name="warn", description="Give a member a warning.")
@app_commands.describe(member="Member to warn", reason="Reason for the warning")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn(
    interaction: discord.Interaction,
    member: discord.Member,
    reason: str
):
    if member.bot:
        await interaction.response.send_message(
            "You cannot warn a bot.",
            ephemeral=True
        )
        return

    add_warning(
        interaction.guild.id,
        member.id,
        interaction.user.id,
        reason
    )

    embed = make_embed(
        "Warning issued",
        f"**Member:** {member.mention}\n"
        f"**Moderator:** {interaction.user.mention}\n"
        f"**Reason:** {reason}"
    )

    await interaction.response.send_message(embed=embed)

    try:
        await member.send(
            embed=make_embed(
                "Server warning",
                f"You received a warning in **{interaction.guild.name}**.\nReason: {reason}"
            )
        )
    except discord.Forbidden:
        pass


@bot.tree.command(name="warnings", description="View a member's warnings.")
@app_commands.describe(member="Member whose warnings you want to view")
@app_commands.checks.has_permissions(moderate_members=True)
async def warnings(
    interaction: discord.Interaction,
    member: discord.Member
):
    entries = get_warnings(interaction.guild.id, member.id)

    if not entries:
        await interaction.response.send_message(
            f"**{member}** has no warnings.",
            ephemeral=True
        )
        return

    lines = []

    for entry in entries:
        lines.append(
            f"**#{entry['id']}** — {entry['reason']}\n"
            f"<t:{int(datetime.fromisoformat(entry['created_at']).timestamp())}:R>"
        )

    embed = make_embed(
        f"Warnings for {member}",
        "\n\n".join(lines)
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


@bot.tree.command(name="clearwarnings", description="Clear a member's warnings.")
@app_commands.describe(member="Member whose warnings should be cleared")
@app_commands.checks.has_permissions(moderate_members=True)
async def clearwarnings(
    interaction: discord.Interaction,
    member: discord.Member
):
    count = clear_warnings(
        interaction.guild.id,
        member.id
    )

    await interaction.response.send_message(
        f"Cleared {count} warning(s) for **{member}**.",
        ephemeral=True
    )


# ============================================================
# WELCOME COMMANDS
# ============================================================

@bot.tree.command(name="setwelcome", description="Set the welcome channel.")
@app_commands.describe(channel="Channel where welcome messages will appear")
@app_commands.checks.has_permissions(manage_guild=True)
async def setwelcome(
    interaction: discord.Interaction,
    channel: discord.TextChannel
):
    update_guild_settings(
        interaction.guild.id,
        welcome_channel_id=channel.id
    )

    await interaction.response.send_message(
        f"Welcome messages will now appear in {channel.mention}.",
        ephemeral=True
    )


@bot.tree.command(name="setwelcomemessage", description="Customize the welcome message.")
@app_commands.describe(message="Welcome text. Use {mention}, {username}, {server}, {member_count}.")
@app_commands.checks.has_permissions(manage_guild=True)
async def setwelcomemessage(
    interaction: discord.Interaction,
    message: app_commands.Range[str, 1, 1000]
):
    update_guild_settings(
        interaction.guild.id,
        welcome_message=message
    )

    await interaction.response.send_message(
        "Welcome message updated.",
        ephemeral=True
    )


@bot.tree.command(name="welcome", description="Preview the welcome message.")
@app_commands.checks.has_permissions(manage_guild=True)
async def welcome(interaction: discord.Interaction):
    settings = get_guild_settings(interaction.guild.id)

    message = (
        settings["welcome_message"]
        if settings and settings["welcome_message"]
        else DEFAULT_WELCOME
    )

    message = (
        message
        .replace("{mention}", interaction.user.mention)
        .replace("{username}", interaction.user.name)
        .replace("{server}", interaction.guild.name)
        .replace(
            "{member_count}",
            str(interaction.guild.member_count or 0)
        )
    )

    await interaction.response.send_message(
        embed=make_embed("Welcome preview", message),
        ephemeral=True
    )


@bot.tree.command(name="resetwelcome", description="Reset the welcome message.")
@app_commands.checks.has_permissions(manage_guild=True)
async def resetwelcome(interaction: discord.Interaction):
    update_guild_settings(
        interaction.guild.id,
        welcome_message=DEFAULT_WELCOME
    )

    await interaction.response.send_message(
        "Welcome message reset to the default.",
        ephemeral=True
    )


# ============================================================
# INFORMATION COMMANDS
# ============================================================

@bot.tree.command(name="userinfo", description="View information about a member.")
@app_commands.describe(member="Member to inspect")
async def userinfo(
    interaction: discord.Interaction,
    member: discord.Member
):
    embed = make_embed(
        f"User information: {member}",
        f"**Username:** {member.name}\n"
        f"**Display name:** {member.display_name}\n"
        f"**User ID:** `{member.id}`\n"
        f"**Joined server:** {discord.utils.format_dt(member.joined_at, style='F') if member.joined_at else 'Unknown'}\n"
        f"**Account created:** {discord.utils.format_dt(member.created_at, style='F')}"
    )

    embed.set_thumbnail(url=member.display_avatar.url)

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


@bot.tree.command(name="serverinfo", description="View server information.")
async def serverinfo(interaction: discord.Interaction):
    guild = interaction.guild

    embed = make_embed(
        f"{guild.name} information",
        f"**Owner:** {guild.owner.mention if guild.owner else 'Unknown'}\n"
        f"**Members:** {guild.member_count}\n"
        f"**Channels:** {len(guild.channels)}\n"
        f"**Roles:** {len(guild.roles)}\n"
        f"**Created:** {discord.utils.format_dt(guild.created_at, style='F')}"
    )

    if guild.icon:
        embed.set_thumbnail(url=guild.icon.url)

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="ping", description="Check Aerin's response time.")
async def ping(interaction: discord.Interaction):
    latency = round(bot.latency * 1000)

    await interaction.response.send_message(
        f"Aerin is online.\nLatency: `{latency}ms`",
        ephemeral=True
    )


@bot.tree.command(name="help", description="Show Aerin's commands.")
async def help_command(interaction: discord.Interaction):
    embed = make_embed(
        "Aerin commands",
        "Here are the available commands."
    )

    embed.add_field(
        name="Moderation",
        value=(
            "`/clear` — Delete messages\n"
            "`/kick` — Kick a member\n"
            "`/ban` — Ban a member\n"
            "`/unban` — Unban a user\n"
            "`/timeout` — Timeout a member\n"
            "`/untimeout` — Remove timeout\n"
            "`/warn` — Warn a member\n"
            "`/warnings` — View warnings\n"
            "`/clearwarnings` — Clear warnings"
        ),
        inline=False
    )

    embed.add_field(
        name="Welcome",
        value=(
            "`/setwelcome` — Set welcome channel\n"
            "`/setwelcomemessage` — Set welcome text\n"
            "`/welcome` — Preview welcome message\n"
            "`/resetwelcome` — Reset welcome text"
        ),
        inline=False
    )

    embed.add_field(
        name="Information",
        value=(
            "`/userinfo` — Member information\n"
            "`/serverinfo` — Server information\n"
            "`/ping` — Bot latency\n"
            "`/help` — Command list"
        ),
        inline=False
    )

    embed.add_field(
        name="Account system",
        value=(
            "`/panel` — Show account panel\n"
            "`/generatekey` — Generate account\n"
            "`/resetaccess` — Reset account access"
        ),
        inline=False
    )

    await interaction.response.send_message(
        embed=embed,
        ephemeral=True
    )


# ============================================================
# ACCOUNT ADMIN COMMANDS
# ============================================================

@bot.tree.command(name="panel", description="Display the Aerin account panel.")
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):
    embed = make_embed(
        "Aerin account panel",
        "Register an account, retrieve your account key, or view account information."
    )

    await interaction.response.send_message(
        embed=embed,
        view=AerinPanel()
    )


@bot.tree.command(name="generatekey", description="Create an Aerin account for a member.")
@app_commands.describe(
    member="Member who will receive the account",
    nickname="Account nickname"
)
@app_commands.checks.has_permissions(administrator=True)
async def generatekey(
    interaction: discord.Interaction,
    member: discord.Member,
    nickname: str
):
    if len(nickname) > 32:
        await interaction.response.send_message(
            "Nickname must be 32 characters or less.",
            ephemeral=True
        )
        return

    existing = get_account(member.id)

    if existing:
        await interaction.response.send_message(
            "This member already has an account.",
            ephemeral=True
        )
        return

    username = f"user_{member.id}"

    try:
        key, _ = register_account(
            member.id,
            username,
            nickname,
            secrets.token_urlsafe(18)
        )

        embed = make_embed(
            "Account generated",
            f"An Aerin account was created for {member.mention}."
        )

        embed.add_field(
            name="Username",
            value=username,
            inline=True
        )

        embed.add_field(
            name="Nickname",
            value=nickname,
            inline=True
        )

        embed.add_field(
            name="Key",
            value=f"`{key}`",
            inline=False
        )

        await interaction.response.send_message(
            embed=embed,
            ephemeral=True
        )

    except Exception:
        logger.exception("Account generation failed")

        await interaction.response.send_message(
            "Could not generate the account.",
            ephemeral=True
        )


@bot.tree.command(name="resetaccess", description="Reset a member's Aerin password access.")
@app_commands.describe(member="Member whose access should be reset")
@app_commands.checks.has_permissions(administrator=True)
async def resetaccess(
    interaction: discord.Interaction,
    member: discord.Member
):
    if not get_account(member.id):
        await interaction.response.send_message(
            "This member does not have an Aerin account.",
            ephemeral=True
        )
        return

    embed = make_embed(
        "Confirm account reset",
        f"Reset password access for **{member}**?"
    )

    await interaction.response.send_message(
        embed=embed,
        view=ResetAccessConfirmView(member),
        ephemeral=True
    )


# ============================================================
# SLASH COMMAND ERROR HANDLER
# ============================================================

@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    if isinstance(error, app_commands.MissingPermissions):
        message = "You do not have permission to use this command."

    elif isinstance(error, app_commands.BotMissingPermissions):
        message = "Aerin is missing the permissions needed for this command."

    elif isinstance(error, app_commands.CommandOnCooldown):
        message = "Please wait before using that command again."

    else:
        logger.error(
            "Command error: %s",
            error,
            exc_info=error
        )
        message = "Something went wrong while running that command."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(
                message,
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                message,
                ephemeral=True
            )
    except discord.HTTPException:
        pass


# ============================================================
# API AUTHENTICATION
# ============================================================

async def read_json(request):
    try:
        data = await request.json()
    except Exception:
        raise web.HTTPBadRequest(
            text="Invalid JSON body."
        )

    if not isinstance(data, dict):
        raise web.HTTPBadRequest(
            text="JSON body must be an object."
        )

    return data


async def api_root(request):
    return web.json_response({
        "service": "Aerin",
        "status": "online",
        "version": "2.0"
    })


async def api_health(request):
    return web.json_response({
        "status": "healthy",
        "service": "Aerin"
    })


async def api_verify(request):
    data = await read_json(request)

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    if not username or not password:
        return web.json_response(
            {
                "verified": False,
                "message": "Username and password are required."
            },
            status=400
        )

    account = authenticate_account(username, password)

    if not account:
        return web.json_response(
            {
                "verified": False,
                "message": "Invalid username or password."
            },
            status=401
        )

    return web.json_response({
        "verified": True,
        "username": account["username"],
        "nickname": account["nickname"],
        "message": "Account verified."
    })


async def api_load(request):
    if request.method == "GET":
        return web.json_response({
            "service": "Aerin",
            "endpoint": "/load/aerin",
            "method": "POST",
            "description": "Account verification endpoint"
        })

    data = await read_json(request)

    username = str(data.get("username", "")).strip()
    password = str(data.get("password", ""))

    if not username or not password:
        return web.json_response(
            {
                "verified": False,
                "message": "Username and password are required."
            },
            status=400
        )

    account = authenticate_account(username, password)

    if not account:
        return web.json_response(
            {
                "verified": False,
                "message": "Invalid username or password."
            },
            status=401
        )

    return web.json_response({
        "verified": True,
        "username": account["username"],
        "nickname": account["nickname"],
        "message": "Aerin account verified."
    })


# ============================================================
# API SERVER
# ============================================================

async def start_api():
    global api_runner

    app = web.Application(
        client_max_size=16 * 1024
    )

    app.router.add_get("/", api_root)
    app.router.add_get("/health", api_health)
    app.router.add_post("/verify", api_verify)
    app.router.add_route("*", "/load/aerin", api_load)

    api_runner = web.AppRunner(app)
    await api_runner.setup()

    site = web.TCPSite(
        api_runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    logger.info("Aerin API running on port %s", PORT)


# ============================================================
# BOT STARTUP
# ============================================================

@bot.event
async def on_ready():
    logger.info("Logged in as %s (%s)", bot.user, bot.user.id)

    if not getattr(bot, "_aerin_views_added", False):
        bot.add_view(AerinPanel())
        bot._aerin_views_added = True

    if GUILD_ID:
        guild = discord.Object(id=GUILD_ID)
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(
            "Synced %s commands to guild %s",
            len(synced),
            GUILD_ID
        )
    else:
        synced = await bot.tree.sync()
        logger.info(
            "Synced %s global commands",
            len(synced)
        )


async def main():
    if not TOKEN:
        raise RuntimeError(
            "DISCORD_TOKEN is missing from environment variables."
        )

    init_db()
    await start_api()

    try:
        await bot.start(TOKEN)
    finally:
        if api_runner:
            await api_runner.cleanup()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Aerin stopped.")
