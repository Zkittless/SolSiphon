"""
Giveaway code generation + redemption.

Phase 1 scope: this cog validates and records redemptions. It does NOT move
any funds -- `payouts` rows are created in `pending` status and just sit
there until Phase 2 wires up the Squads proposal step. Nothing in this file
should ever call out to a wallet or trigger a transfer.
"""

import os
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from bot.codegen import generate_code
from bot.validation import is_valid_solana_address_format
from db.pool import get_pool, write_audit

MOD_ROLE_ID = int(os.environ.get("MOD_ROLE_ID", 0))
DEFAULT_EXPIRY_HOURS = int(os.environ.get("DEFAULT_CODE_EXPIRY_HOURS", 24))


def is_mod():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id == MOD_ROLE_ID for role in interaction.user.roles)
    return app_commands.check(predicate)


class Giveaway(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---------- Code generation (mod/streamer only) ----------

    @app_commands.command(
        name="giveaway-create",
        description="Generate a redeemable giveaway code for a fixed USD amount.",
    )
    @app_commands.describe(
        amount_usd="Dollar amount this code is worth",
        expiry_hours="Hours until the code expires (default 24)",
    )
    @is_mod()
    async def giveaway_create(
        self,
        interaction: discord.Interaction,
        amount_usd: float,
        expiry_hours: int | None = None,
    ):
        if amount_usd <= 0:
            await interaction.response.send_message(
                "Amount must be greater than 0.", ephemeral=True
            )
            return

        expiry_hours = expiry_hours or DEFAULT_EXPIRY_HOURS
        expires_at = datetime.now(timezone.utc) + timedelta(hours=expiry_hours)
        code = generate_code()

        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    INSERT INTO giveaway_codes (code, amount_usd, created_by, expires_at)
                    VALUES ($1, $2, $3, $4)
                    RETURNING id
                    """,
                    code,
                    amount_usd,
                    str(interaction.user.id),
                    expires_at,
                )
                await write_audit(
                    conn,
                    entity_type="code",
                    entity_id=row["id"],
                    action="created",
                    actor=str(interaction.user.id),
                    metadata={"amount_usd": amount_usd, "expires_at": expires_at.isoformat()},
                )

        await interaction.response.send_message(
            f"Code created: `{code}` — worth ${amount_usd:.2f}, "
            f"expires <t:{int(expires_at.timestamp())}:R>",
            ephemeral=True,
        )

    @giveaway_create.error
    async def giveaway_create_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "You don't have permission to create giveaway codes.", ephemeral=True
            )
        else:
            raise error

    # ---------- Redemption (any user, via DM-friendly command) ----------

    @app_commands.command(
        name="redeem",
        description="Redeem a giveaway code with your Solana address.",
    )
    @app_commands.describe(
        code="The giveaway code you received",
        solana_address="Your Solana wallet address",
    )
    async def redeem(
        self,
        interaction: discord.Interaction,
        code: str,
        solana_address: str,
    ):
        code = code.strip().upper()
        solana_address = solana_address.strip()

        if not is_valid_solana_address_format(solana_address):
            await interaction.response.send_message(
                "That doesn't look like a valid Solana address. Please double-check "
                "and try again — no funds move until this is confirmed correct.",
                ephemeral=True,
            )
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                # Row-level lock on the code to prevent concurrent double-redemption.
                code_row = await conn.fetchrow(
                    """
                    SELECT id, amount_usd, status, expires_at
                    FROM giveaway_codes
                    WHERE code = $1
                    FOR UPDATE
                    """,
                    code,
                )

                if code_row is None:
                    await interaction.response.send_message(
                        "That code doesn't exist. Check for typos.", ephemeral=True
                    )
                    return

                if code_row["status"] != "unused":
                    await interaction.response.send_message(
                        f"That code has already been {code_row['status']}.",
                        ephemeral=True,
                    )
                    return

                if code_row["expires_at"] < datetime.now(timezone.utc):
                    await conn.execute(
                        "UPDATE giveaway_codes SET status = 'expired' WHERE id = $1",
                        code_row["id"],
                    )
                    await write_audit(
                        conn,
                        entity_type="code",
                        entity_id=code_row["id"],
                        action="expired",
                        actor="system",
                    )
                    await interaction.response.send_message(
                        "That code has expired.", ephemeral=True
                    )
                    return

                # Mark code redeemed, insert redemption, create pending payout row.
                await conn.execute(
                    "UPDATE giveaway_codes SET status = 'redeemed' WHERE id = $1",
                    code_row["id"],
                )

                redemption_row = await conn.fetchrow(
                    """
                    INSERT INTO redemptions
                        (source, source_ref_id, discord_user_id, solana_address,
                         amount_usd, status)
                    VALUES ('discord_code', $1, $2, $3, $4, 'validated')
                    RETURNING id
                    """,
                    code_row["id"],
                    str(interaction.user.id),
                    solana_address,
                    code_row["amount_usd"],
                )

                payout_row = await conn.fetchrow(
                    """
                    INSERT INTO payouts (redemption_id, amount_usd, status)
                    VALUES ($1, $2, 'pending')
                    RETURNING id
                    """,
                    redemption_row["id"],
                    code_row["amount_usd"],
                )

                await write_audit(
                    conn,
                    entity_type="redemption",
                    entity_id=redemption_row["id"],
                    action="redeemed",
                    actor=str(interaction.user.id),
                    metadata={
                        "code_id": code_row["id"],
                        "amount_usd": float(code_row["amount_usd"]),
                        "solana_address": solana_address,
                    },
                )
                await write_audit(
                    conn,
                    entity_type="payout",
                    entity_id=payout_row["id"],
                    action="created",
                    actor="system",
                    metadata={"redemption_id": redemption_row["id"], "status": "pending"},
                )

        await interaction.response.send_message(
            f"Code redeemed for ${code_row['amount_usd']:.2f}. Your payout is queued "
            f"for approval — you'll be notified once it's processed. "
            f"(No wallet integration yet in this build — this just records the claim.)",
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Giveaway(bot))
