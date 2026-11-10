
import os
import sqlite3
import secrets
import string
import logging

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

# ================ CONFIG ================

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
GUILD_ID = int(os.getenv("GUILD_ID", "0") or "0")
VERIFIED_ROLE_ID = int(os.getenv("VERIFIED_ROLE_ID", "0") or "0")

SCRIPT_URL = os.getenv("SCRIPT_URL", "").strip()
API_BASE_URL = os.getenv("API_BASE_URL", "").rstrip("/")
API_SECRET = os.getenv("API_SECRET", "").strip()

DATABASE = os.getenv("DATABASE_PATH", "aerin.sqlite3")

if not TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN.")

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("aerin")

intents = discord.Intents.default()
intents.members = True


# ================ DATABASE ================

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


def create_key():
    chars = string.ascii_uppercase + string.digits
    code = "".join(secrets.choice(chars) for _ in range(8))
    return f"AERIN-VLSD-{code}-EL"


def is_verified(user_id):
    with db() as con:
        row = con.execute(
            "SELECT 1 FROM keys WHERE redeemed_by = ? LIMIT 1",
            (str(user_id),)
        ).fetchone()
    return row is not None


# ================ AUTH API ================

async def reset_server_hwid(user_id):
    """
    API contract required:
    POST {API_BASE_URL}/api/reset-hwid
    JSON: {"discord_id": "123456"}
    Header: X-API-Secret
    Expected JSON: {"success": true}
    """
    if not API_BASE_URL or not API_SECRET:
        return False, "Aerin authentication API is not configured."

    url = f"{API_BASE_URL}/api/reset-hwid"

    headers = {
        "Content-Type": "application/json",
        "X-API-Secret": API_SECRET
    }

    payload = {"discord_id": str(user_id)}

    timeout = aiohttp.ClientTimeout(total=15)

    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                url,
                json=payload,
                headers=headers
            ) as response:

                data = await response.json(content_type=None)

                if response.status == 200 and data.get("success"):
                    return True, "Your device binding has been reset."

                return False, data.get(
                    "message",
                    "The authentication server rejected the reset."
                )

    except Exception:
        log.exception("HWID reset API request failed")
        return False, "Could not contact the Aerin authentication server."


# ================ REDEEM MODAL ================

class RedeemModal(discord.ui.Modal, title="Redeem Aerin Key"):

    key = discord.ui.TextInput(
        label="Your Aerin key",
        placeholder="AERIN-VLSD-XXXXXXXX-EL",
        min_length=15,
        max_length=40
    )

    async def on_submit(self, interaction):
        submitted = str(self.key.value).strip().upper()

        with db() as con:
            row = con.execute(
                "SELECT * FROM keys WHERE key = ?",
                (submitted,)
            ).fetchone()

            if not row:
                await interaction.response.send_message(
                    "Invalid Aerin key.",
                    ephemeral=True
                )
                return

            if row["redeemed_by"]:
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
                """
                UPDATE keys
                SET redeemed_by = ?, redeemed_at = CURRENT_TIMESTAMP
                WHERE key = ? AND redeemed_by IS NULL
                """,
                (str(interaction.user.id), submitted)
            )

        await interaction.response.send_message(
            "Aerin key redeemed successfully.",
            ephemeral=True
        )


# ================ PANEL ================

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
                "The Aerin script URL is not configured.",
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
                "Use this button inside the server.",
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
                "The Aerin role is not configured correctly.",
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
            await member.add_roles(
                role,
                reason="Aerin key redeemed"
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "I cannot assign this role. Check my permissions.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Aerin role assigned successfully.",
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

        await interaction.response.defer(ephemeral=True)

        success, message = await reset_server_hwid(
            interaction.user.id
        )

        await interaction.followup.send(
            message,
            ephemeral=True
        )


# ================ BOT ================

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


# ================ PANEL COMMAND ================

@bot.tree.command(
    name="panel",
    description="Post the Aerin panel"
)
@app_commands.guild_only()
@app_commands.checks.has_permissions(administrator=True)
async def panel(interaction):

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


# ================ KEY GENERATOR ================

@bot.tree.command(
    name="generatekey",
    description="Generate Aerin keys"
)
@app_commands.guild_only()
@app_commands.describe(amount="Number of keys (1-25)")
async def generatekey(
    interaction,
    amount: app_commands.Range[int, 1, 25] = 1
):

    await interaction.response.defer(ephemeral=True)

    if not interaction.user.guild_permissions.administrator:
        await interaction.followup.send(
            "You are not authorized.",
            ephemeral=True
        )
        return

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

        await interaction.followup.send(
            f"Generated {len(generated)} key(s). Check your DMs.",
            ephemeral=True
        )

    except discord.Forbidden:
        await interaction.followup.send(
            "I couldn't DM you. Enable server DMs.",
            ephemeral=True
        )


# ================ ERROR HANDLING ================

@panel.error
@generatekey.error
async def command_error(interaction, error):

    if isinstance(error, app_commands.MissingPermissions):
        message = "Only administrators can use this command."
    else:
        log.exception("Command error", exc_info=error)
        message = "Something went wrong. Check Render logs."

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


if __name__ == "__main__":
    bot.run(TOKEN)
