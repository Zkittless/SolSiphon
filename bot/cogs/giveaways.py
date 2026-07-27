"""
Timed giveaways with button entry.

Flow:
  /giveaway-create amount_usd [duration_hours] [duration_minutes]
    -> posts an embed with "Enter" and "View Joiners" buttons in the channel
    -> users click Enter to join (one entry per user, enforced at the DB level)
    -> a background loop checks every 30s for giveaways past their end time
       and ends them automatically; /giveaway-end lets a mod end one early
    -> ending picks a random entrant, creates a giveaway_codes row for them
       (same table /code-create uses), and DMs them the code

Winners still redeem through the normal /redeem command -- this cog only
handles entry + selection + code generation + DM delivery. No wallet code
here, same as the rest of Phase 1.
"""

import os
import random
from datetime import datetime, timedelta, timezone

import asyncpg
import discord
from discord import app_commands
from discord.ext import commands, tasks

from bot.codegen import generate_code
from db.pool import get_pool, write_audit

MOD_ROLE_ID = int(os.environ.get("MOD_ROLE_ID", 0))

HOUR_CHOICES = [
    app_commands.Choice(name="2 hours", value=2),
    app_commands.Choice(name="4 hours", value=4),
    app_commands.Choice(name="8 hours", value=8),
    app_commands.Choice(name="12 hours", value=12),
    app_commands.Choice(name="24 hours", value=24),
]


def is_mod():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id == MOD_ROLE_ID for role in interaction.user.roles)
    return app_commands.check(predicate)


def _giveaway_embed(giveaway_id: int, amount_usd: float, ends_at: datetime, entrant_count: int, status: str = "open") -> discord.Embed:
    if status == "open":
        embed = discord.Embed(
            title="🎉 Giveaway!",
            description=(
                f"**Prize:** ${amount_usd:.2f}\n"
                f"**Ends:** <t:{int(ends_at.timestamp())}:R>\n\n"
                f"Click **Enter** below to join. One entry per person."
            ),
            color=discord.Color.gold(),
        )
    else:
        embed = discord.Embed(
            title="🎉 Giveaway Ended",
            description=f"**Prize:** ${amount_usd:.2f}\n\nWinner has been notified by DM.",
            color=discord.Color.dark_grey(),
        )
    embed.set_footer(text=f"Giveaway #{giveaway_id} • {entrant_count} entrant(s)")
    return embed


class GiveawayView(discord.ui.View):
    """Persistent view -- custom_ids encode the giveaway ID so it survives restarts."""

    def __init__(self, giveaway_id: int):
        super().__init__(timeout=None)
        self.giveaway_id = giveaway_id
        self.enter_button.custom_id = f"giveaway_enter:{giveaway_id}"
        self.view_joiners_button.custom_id = f"giveaway_joiners:{giveaway_id}"

    @discord.ui.button(label="Enter", style=discord.ButtonStyle.green, emoji="🎉")
    async def enter_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                giveaway = await conn.fetchrow(
                    "SELECT status, amount_usd, ends_at FROM giveaways WHERE id = $1 FOR UPDATE",
                    self.giveaway_id,
                )
                if giveaway is None:
                    await interaction.response.send_message(
                        "This giveaway no longer exists.", ephemeral=True
                    )
                    return
                if giveaway["status"] != "open":
                    await interaction.response.send_message(
                        "This giveaway has already ended.", ephemeral=True
                    )
                    return

                try:
                    await conn.execute(
                        """
                        INSERT INTO giveaway_entrants (giveaway_id, discord_user_id)
                        VALUES ($1, $2)
                        """,
                        self.giveaway_id,
                        str(interaction.user.id),
                    )
                except asyncpg.UniqueViolationError:
                    # Real duplicate entry for THIS giveaway_id specifically.
                    await interaction.response.send_message(
                        "You're already entered in this giveaway.", ephemeral=True
                    )
                    return

                await write_audit(
                    conn,
                    entity_type="giveaway",
                    entity_id=self.giveaway_id,
                    action="giveaway_entered",
                    actor=str(interaction.user.id),
                )

                entrant_count = await conn.fetchval(
                    "SELECT COUNT(*) FROM giveaway_entrants WHERE giveaway_id = $1",
                    self.giveaway_id,
                )

        await interaction.response.send_message("You're entered! Good luck 🎉", ephemeral=True)

        # Live-update the entrant count on the original embed. Best-effort --
        # if the message was deleted or edit fails, the entry itself is
        # already recorded, so don't fail the interaction over a cosmetic edit.
        try:
            updated_embed = _giveaway_embed(
                self.giveaway_id,
                float(giveaway["amount_usd"]),
                giveaway["ends_at"],
                entrant_count,
                status="open",
            )
            await interaction.message.edit(embed=updated_embed, view=self)
        except Exception:
            pass

    @discord.ui.button(label="View Joiners", style=discord.ButtonStyle.grey, emoji="👥")
    async def view_joiners_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        pool = get_pool()
        async with pool.acquire() as conn:
            entrants = await conn.fetch(
                """
                SELECT discord_user_id FROM giveaway_entrants
                WHERE giveaway_id = $1
                ORDER BY entered_at ASC
                """,
                self.giveaway_id,
            )

        if not entrants:
            await interaction.response.send_message(
                "No one has entered yet.", ephemeral=True
            )
            return

        # Discord message limits mean a very large entrant list needs
        # truncating -- show the first 50 and a count of the rest.
        shown = entrants[:50]
        mentions = "\n".join(f"{i+1}. <@{row['discord_user_id']}>" for i, row in enumerate(shown))
        extra = len(entrants) - len(shown)
        if extra > 0:
            mentions += f"\n...and {extra} more"

        embed = discord.Embed(
            title=f"Entrants ({len(entrants)})",
            description=mentions,
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)


class Giveaways(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.check_expired_giveaways.start()

    def cog_unload(self):
        self.check_expired_giveaways.cancel()

    # ---------- Start a timed giveaway ----------

    @app_commands.command(
        name="giveaway-create",
        description="Start a giveaway with button entry. Winner gets a code via DM.",
    )
    @app_commands.describe(
        amount_usd="Dollar amount the winner receives",
        duration_hours="How long entries stay open (pick a preset)",
        duration_minutes="Custom duration in minutes -- overrides duration_hours if set",
    )
    @app_commands.choices(duration_hours=HOUR_CHOICES)
    @is_mod()
    async def giveaway_create(
        self,
        interaction: discord.Interaction,
        amount_usd: float,
        duration_hours: app_commands.Choice[int] | None = None,
        duration_minutes: int | None = None,
    ):
        if amount_usd <= 0:
            await interaction.response.send_message(
                "Amount must be greater than 0.", ephemeral=True
            )
            return

        if duration_minutes is not None:
            if duration_minutes <= 0:
                await interaction.response.send_message(
                    "Custom duration must be greater than 0 minutes.", ephemeral=True
                )
                return
            total_minutes = duration_minutes
        elif duration_hours is not None:
            total_minutes = duration_hours.value * 60
        else:
            await interaction.response.send_message(
                "Pick a duration_hours preset or specify duration_minutes.", ephemeral=True
            )
            return

        ends_at = datetime.now(timezone.utc) + timedelta(minutes=total_minutes)

        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO giveaways (amount_usd, created_by, channel_id, ends_at)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    amount_usd,
                    str(interaction.user.id),
                    str(interaction.channel_id),
                    ends_at,
                )
                giveaway_id = row["id"]
                await write_audit(
                    conn,
                    entity_type="giveaway",
                    entity_id=giveaway_id,
                    action="giveaway_started",
                    actor=str(interaction.user.id),
                    metadata={"amount_usd": amount_usd, "ends_at": ends_at.isoformat()},
                )

        embed = _giveaway_embed(giveaway_id, amount_usd, ends_at, entrant_count=0)
        view = GiveawayView(giveaway_id)

        await interaction.response.send_message(embed=embed, view=view)
        sent_message = await interaction.original_response()

        pool = get_pool()
        async with pool.acquire() as conn:
            await conn.execute(
                "UPDATE giveaways SET message_id = $1 WHERE id = $2",
                str(sent_message.id),
                giveaway_id,
            )

    # ---------- End early (mod command) ----------

    @app_commands.command(
        name="giveaway-end",
        description="End a giveaway early and pick a winner now.",
    )
    @app_commands.describe(giveaway_id="The giveaway's ID (shown when it was started)")
    @is_mod()
    async def giveaway_end(self, interaction: discord.Interaction, giveaway_id: int):
        await interaction.response.defer(ephemeral=True)
        result = await self._end_giveaway(giveaway_id)
        await interaction.followup.send(result, ephemeral=True)

    # ---------- Background loop: auto-end expired giveaways ----------

    @tasks.loop(seconds=30)
    async def check_expired_giveaways(self):
        pool = get_pool()
        async with pool.acquire() as conn:
            due = await conn.fetch(
                """
                SELECT id FROM giveaways
                WHERE status = 'open' AND ends_at <= now()
                """
            )
        for row in due:
            await self._end_giveaway(row["id"])

    @check_expired_giveaways.before_loop
    async def before_check_expired(self):
        await self.bot.wait_until_ready()

    # ---------- Shared ending logic ----------

    async def _end_giveaway(self, giveaway_id: int) -> str:
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                giveaway = await conn.fetchrow(
                    "SELECT * FROM giveaways WHERE id = $1 FOR UPDATE",
                    giveaway_id,
                )
                if giveaway is None:
                    return "Giveaway not found."
                if giveaway["status"] != "open":
                    return "That giveaway has already been ended."

                entrants = await conn.fetch(
                    "SELECT discord_user_id FROM giveaway_entrants WHERE giveaway_id = $1",
                    giveaway_id,
                )

                if not entrants:
                    await conn.execute(
                        "UPDATE giveaways SET status = 'cancelled', ended_at = now() WHERE id = $1",
                        giveaway_id,
                    )
                    await write_audit(
                        conn,
                        entity_type="giveaway",
                        entity_id=giveaway_id,
                        action="giveaway_cancelled_no_entrants",
                        actor="system",
                    )
                    winner_id = None
                    code = None
                else:
                    winner_id = random.choice(entrants)["discord_user_id"]
                    code = generate_code()
                    code_row = await conn.fetchrow(
                        """
                        INSERT INTO giveaway_codes (code, amount_usd, created_by, expires_at)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id
                        """,
                        code,
                        giveaway["amount_usd"],
                        giveaway["created_by"],
                        datetime.now(timezone.utc) + timedelta(hours=48),
                    )
                    await conn.execute(
                        """
                        UPDATE giveaways
                        SET status = 'ended', ended_at = now(),
                            winner_user_id = $1, winning_code_id = $2
                        WHERE id = $3
                        """,
                        winner_id,
                        code_row["id"],
                        giveaway_id,
                    )
                    await write_audit(
                        conn,
                        entity_type="giveaway",
                        entity_id=giveaway_id,
                        action="giveaway_ended",
                        actor="system",
                        metadata={
                            "winner_user_id": winner_id,
                            "code_id": code_row["id"],
                            "entrant_count": len(entrants),
                        },
                    )

        # Update the original message (best-effort -- don't fail the whole
        # operation if the message/channel was deleted).
        try:
            channel = self.bot.get_channel(int(giveaway["channel_id"])) or \
                await self.bot.fetch_channel(int(giveaway["channel_id"]))
            if giveaway["message_id"]:
                message = await channel.fetch_message(int(giveaway["message_id"]))
                embed = _giveaway_embed(
                    giveaway_id, float(giveaway["amount_usd"]), giveaway["ends_at"],
                    len(entrants), status="ended",
                )
                await message.edit(embed=embed, view=None)
        except Exception:
            pass

        if winner_id is None:
            return f"Giveaway #{giveaway_id} ended with no entrants -- nothing to award."

        # DM the winner their code.
        try:
            user = self.bot.get_user(int(winner_id)) or await self.bot.fetch_user(int(winner_id))
            await user.send(
                f"🎉 You won the giveaway for **${float(giveaway['amount_usd']):.2f}**!\n\n"
                f"Your code: `{code}`\n\n"
                f"Redeem it with `/redeem` and your Solana address to claim your payout."
            )
            dm_status = "DM sent."
        except discord.Forbidden:
            dm_status = (
                "Could not DM the winner (DMs closed) -- "
                f"<@{winner_id}> needs to be given the code `{code}` manually."
            )

        return f"Giveaway #{giveaway_id} ended. Winner: <@{winner_id}>. {dm_status}"


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaways(bot))
