"""
Mod commands for mapping Kick channel-point rewards to USD payout amounts.

You still have to create the actual reward on Kick's dashboard/API
yourself (title, point cost) -- these commands just tell this bot what
USD amount to pay out when that reward's ID shows up in a webhook.
"""

import os

import discord
from discord import app_commands
from discord.ext import commands

from db.pool import get_pool, write_audit

MOD_ROLE_ID = int(os.environ.get("MOD_ROLE_ID", 0))


def is_mod():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not isinstance(interaction.user, discord.Member):
            return False
        return any(role.id == MOD_ROLE_ID for role in interaction.user.roles)
    return app_commands.check(predicate)


class KickRewards(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="kick-reward-add",
        description="Map a Kick reward ID to a USD payout amount.",
    )
    @app_commands.describe(
        kick_reward_id="The reward ID from Kick (visible in your Kick dashboard/API)",
        title="A label for your own reference",
        point_cost="How many channel points this reward costs",
        amount_usd="USD amount to pay out when this reward is redeemed",
    )
    @is_mod()
    async def kick_reward_add(
        self,
        interaction: discord.Interaction,
        kick_reward_id: str,
        title: str,
        point_cost: int,
        amount_usd: float,
    ):
        if point_cost <= 0 or amount_usd <= 0:
            await interaction.response.send_message(
                "Point cost and amount must be greater than 0.", ephemeral=True
            )
            return

        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                try:
                    row = await conn.fetchrow(
                        """
                        INSERT INTO kick_rewards (kick_reward_id, title, point_cost, amount_usd)
                        VALUES ($1, $2, $3, $4)
                        RETURNING id
                        """,
                        kick_reward_id,
                        title,
                        point_cost,
                        amount_usd,
                    )
                except Exception as e:
                    if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        await interaction.response.send_message(
                            f"Reward ID `{kick_reward_id}` is already mapped. "
                            f"Use `/kick-reward-remove` first if you need to change it.",
                            ephemeral=True,
                        )
                        return
                    raise

                await write_audit(
                    conn,
                    entity_type="kick_reward",
                    entity_id=row["id"],
                    action="created",
                    actor=str(interaction.user.id),
                    metadata={
                        "kick_reward_id": kick_reward_id,
                        "title": title,
                        "point_cost": point_cost,
                        "amount_usd": amount_usd,
                    },
                )

        await interaction.response.send_message(
            f"Mapped reward `{title}` ({point_cost} pts) -> ${amount_usd:.2f}",
            ephemeral=True,
        )

    @app_commands.command(
        name="kick-reward-list",
        description="List all mapped Kick rewards.",
    )
    @is_mod()
    async def kick_reward_list(self, interaction: discord.Interaction):
        pool = get_pool()
        async with pool.acquire() as conn:
            rewards = await conn.fetch(
                """
                SELECT kick_reward_id, title, point_cost, amount_usd, is_active
                FROM kick_rewards
                ORDER BY created_at DESC
                """
            )

        if not rewards:
            await interaction.response.send_message("No rewards mapped yet.", ephemeral=True)
            return

        lines = [
            f"{'✅' if r['is_active'] else '⛔'} **{r['title']}** — "
            f"{r['point_cost']} pts -> ${r['amount_usd']:.2f} "
            f"(`{r['kick_reward_id']}`)"
            for r in rewards
        ]
        embed = discord.Embed(
            title="Kick Reward Mappings",
            description="\n".join(lines),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(
        name="kick-reward-remove",
        description="Deactivate a mapped Kick reward (stops future payouts, keeps history).",
    )
    @app_commands.describe(kick_reward_id="The reward ID to deactivate")
    @is_mod()
    async def kick_reward_remove(self, interaction: discord.Interaction, kick_reward_id: str):
        pool = get_pool()
        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    UPDATE kick_rewards SET is_active = false
                    WHERE kick_reward_id = $1
                    RETURNING id, title
                    """,
                    kick_reward_id,
                )
                if row is None:
                    await interaction.response.send_message(
                        "No mapping found for that reward ID.", ephemeral=True
                    )
                    return

                await write_audit(
                    conn,
                    entity_type="kick_reward",
                    entity_id=row["id"],
                    action="deactivated",
                    actor=str(interaction.user.id),
                )

        await interaction.response.send_message(
            f"Deactivated `{row['title']}`. Existing redemptions/payouts are untouched.",
            ephemeral=True,
        )

    @kick_reward_add.error
    @kick_reward_list.error
    @kick_reward_remove.error
    async def kick_reward_error(self, interaction: discord.Interaction, error):
        if isinstance(error, app_commands.CheckFailure):
            await interaction.response.send_message(
                "You don't have permission to manage Kick rewards.", ephemeral=True
            )
        else:
            raise error


async def setup(bot: commands.Bot):
    await bot.add_cog(KickRewards(bot))
