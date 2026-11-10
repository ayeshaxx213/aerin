
import os
import sqlite3
import secrets
import string
import logging

import discord
from discord import app_commands
from discord.ext import commands

# ---------------- CONFIG ----------------

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0") or "0")
SCRIPT_URL = os.getenv("SCRIPT_URL", "").strip()
DATABASE = os.getenv("DATABASE_PATH", "aerin.sqlite3")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN environment variable.")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aerin")

intents = discord.Intents.default()
intents.members = True


# ---------------- DATABASE ----------------

def db():
    con = sqlite3.connect(DATABASE)
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
            CREATE TABLE IF NOT EXISTS members (
                discord_id TEXT PRIMARY KEY,
                hwid TEXT
            )
        """)


def create_key():
    chars = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(chars) for _ in range(8))
    return f"AERIN-VLSD-{code}-EL"


def is_verified(user_id):
    with db() as con:
        return con.execute(
            "SELECT 1 FROM keys WHERE redeemed_by = ? LIMIT 1",
            (str(user_id),)
        ).fetchone() is not None


# ---------------- REDEEM MODAL ----------------

class RedeemModal(discord.ui.Modal, title="Redeem Aerin Key"):

    key = discord.ui.TextInput(
        label="Enter your Aerin key",
        placeholder="AERIN-VLSD-XXXXXXXX-EL",
        min_length=15,
        max_length=40
    )

    async def on_submit(self, interaction: discord.Interaction):
        submitted = str(self.key.value).strip().upper()

        with db() as con:
            row = con.execute(
                "SELECT * FROM keys WHERE key = ?",
                (submitted,)
            ).fetchone()

            if row is None:
                await interaction.response.send_message(
                    "Invalid Aerin key.",
                    ephemeral=True
                )
                return

            if row["redeemed_by"] is not None:
                message = (
                    "You already redeemed this key."
                    if row["redeemed_by"] == str(interaction.user.id)
                    else "This key has already been used."
                )
                await interaction.response.send_message(
                    message,
                    ephemeral=True
                )
                return

            con.execute(
                """UPDATE keys
                   SET redeemed_by = ?, redeemed_at = CURRENT_TIMESTAMP
                   WHERE key = ? AND redeemed_by IS NULL""",
                (str(interaction.user.id), submitted)
            )

        await interaction.response.send_message(
            "Your Aerin key has been redeemed successfully.",
            ephemeral=True
        )


# ---------------- PANEL BUTTONS ----------------

class AerinPanel(discord.ui.View):

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Redeem Key",
        style=discord.ButtonStyle.primary,
        custom_id="aerin:redeem"
    )
    async def redeem(self, interaction, button):
        await interaction.response.send_modal(RedeemModal())

    @discord.ui.button(
        label="Get Script",
        style=discord.ButtonStyle.secondary,
        custom_id="aerin:script"
    )
    async def get_script(self, interaction, button):
        if not is_verified(interaction.user.id):
            await interaction.response.send_message(
                "Redeem your Aerin key first.",
                ephemeral=True
            )
            return

        if not SCRIPT_URL:
            await interaction.response.send_message(
                "The Aerin script URL has not been configured.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Your Aerin script link:\n{SCRIPT_URL}",
            ephemeral=True
        )

    @discord.ui.button(
        label="Get Role",
        style=discord.ButtonStyle.success,
        custom_id="aerin:role"
    )
    async def get_role(self, interaction, button):
        if not interaction.guild:
            await interaction.response.send_message(
                "Use this button in the server.",
                ephemeral=True
            )
            return

        if not is_verified(interaction.user.id):
            await interaction.response.send_message(
                "Redeem your Aerin key first.",
                ephemeral=True
            )
            return

        role = interaction.guild.get_role(VERIFIED_ROLE_ID)
        member = interaction.guild.get_member(interaction.user.id)

        if not role or not member:
            await interaction.response.send_message(
                "The verified role is not configured correctly.",
                ephemeral=True
            )
            return

        if role in member.roles:
            await interaction.response.send_message(
                "You already have the Aerin role.",
                ephemeral=True
            )
            return

        try:
            await member.add_roles(role, reason="Aerin key redeemed")
        except discord.Forbidden:
            await interaction.response.send_message(
                "I cannot assign that role. Check my role permissions and hierarchy.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Your Aerin role has been assigned.",
            ephemeral=True
        )

    @discord.ui.button(
        label="Reset HWID",
        style=discord.ButtonStyle.danger,
        custom_id="aerin:reset_hwid"
    )
    async def reset_hwid(self, interaction, button):
        if not is_verified(interaction.user.id):
            await interaction.response.send_message(
                "Redeem your Aerin key first.",
                ephemeral=True
            )
            return

        with db() as con:
            con.execute(
                """INSERT INTO members(discord_id, hwid)
                   VALUES(?, NULL)
                   ON CONFLICT(discord_id)
                   DO UPDATE SET hwid = NULL""",
                (str(interaction.user.id),)
            )

        await interaction.response.send_message(
            "Your saved Aerin HWID field has been cleared.",
            ephemeral=True
        )


# ---------------- BOT ----------------

class AerinBot(commands.Bot):

    def __init__(self):
        super().__init__(
            command_prefix="!",
            intents=intents
        )

    async def setup_hook(self):
        setup_database()
        self.add_view(AerinPanel())

        if GUILD_ID:
            guild = discord.Object(id=GUILD_ID)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()


bot = AerinBot()


@bot.event
async def on_ready():
    log.info("Aerin online as %s", bot.user)


# ---------------- PANEL COMMAND ----------------

@bot.tree.command(
    name="panel",
    description="Post the Aerin panel"
)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction: discord.Interaction):

    embed = discord.Embed(
        title="aerin panel",
        description=(
            "Redeem your key to unlock Aerin access.\n"
            "Use the buttons below to manage your access."
        ),
        color=discord.Color.from_rgb(242, 190, 207)
    )

    await interaction.response.send_message(
        embed=embed,
        view=AerinPanel()
    )


# ---------------- KEY GENERATOR ----------------

@bot.tree.command(
    name="generatekey",
    description="Generate Aerin redemption keys"
)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(amount="Number of keys to generate (1-25)")
async def generatekey(
    interaction: discord.Interaction,
    amount: app_commands.Range[int, 1, 25] = 1
):

    generated = []

    with db() as con:
        for _ in range(amount):
            key = create_key()

            while con.execute(
                "SELECT 1 FROM keys WHERE key = ?",
                (key,)
            ).fetchone():
                key = create_key()

            con.execute(
                "INSERT INTO keys(key) VALUES(?)",
                (key,)
            )
            generated.append(key)

    try:
        await interaction.user.send(
            "Your Aerin keys:\n" +
            "\n".join(f"`{key}`" for key in generated)
        )
        await interaction.response.send_message(
            f"Generated {len(generated)} key(s). Check your DMs.",
            ephemeral=True
        )
    except discord.Forbidden:
        await interaction.response.send_message(
            "I couldn't DM you. Enable server DMs and try again.",
            ephemeral=True
        )


# ---------------- ERROR HANDLING ----------------

@panel.error
@generatekey.error
async def command_error(interaction, error):

    if isinstance(error, app_commands.MissingPermissions):
        message = "Only administrators can use this command."
    else:
        log.error("Command error: %s", error)
        message = "Something went wrong. Check the bot logs."

    if interaction.response.is_done():
        await interaction.followup.send(message, ephemeral=True)
    else:
        await interaction.response.send_message(message, ephemeral=True)


if __name__ == "__main__":
    bot.run(TOKEN)
