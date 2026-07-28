"""
HTTP server receiving Kick webhooks -- runs alongside the Discord bot in
the same process (see bot/main.py), since Railway only routes traffic to
one exposed service per deploy.

Only the `reward-redeemed` event is handled. Everything else is
acknowledged (200) and ignored, so Kick doesn't retry/disable the
subscription over event types we don't care about yet.

This module ONLY validates, maps the reward to a USD amount, and inserts
into `redemptions` / `payouts` -- the exact same pipeline the Discord
/redeem command feeds into. No wallet code here.
"""

import logging

from aiohttp import web

from bot.kick.signature import verify_kick_signature
from bot.validation import is_valid_solana_address_format
from db.pool import get_pool, write_audit

logger = logging.getLogger("kick-webhook")

routes = web.RouteTableDef()


@routes.post("/webhooks/kick")
async def handle_kick_webhook(request: web.Request) -> web.Response:
    raw_body = await request.read()

    if not verify_kick_signature(request.headers, raw_body):
        logger.warning("Rejected Kick webhook -- signature verification failed")
        return web.Response(status=401, text="invalid signature")

    event_type = request.headers.get("Kick-Event-Type", "")

    try:
        payload = await request.json()
    except ValueError:
        return web.Response(status=400, text="invalid json")

    if event_type != "reward-redeemed":
        # Acknowledge anything we're not handling yet so Kick doesn't
        # retry or auto-disable the subscription.
        return web.Response(status=200, text="ignored")

    await _handle_reward_redeemed(payload)
    return web.Response(status=200, text="ok")


async def _handle_reward_redeemed(payload: dict) -> None:
    """
    Expected shape (best-effort -- confirm against actual Kick payloads
    once you're subscribed and can inspect a real delivery):

    {
      "id": "<kick redemption id>",
      "reward": {"id": "<kick reward id>"},
      "user": {"id": "<kick user id>"},
      "user_input": "<solana address, if reward requires input>"
    }
    """
    kick_redemption_id = str(payload.get("id", ""))
    reward_id = str(payload.get("reward", {}).get("id", ""))
    kick_user_id = str(payload.get("user", {}).get("id", ""))
    solana_address = (payload.get("user_input") or "").strip()

    if not (kick_redemption_id and reward_id and kick_user_id):
        logger.warning(f"Malformed reward-redeemed payload, skipping: {payload}")
        return

    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            reward = await conn.fetchrow(
                """
                SELECT id, amount_usd FROM kick_rewards
                WHERE kick_reward_id = $1 AND is_active = true
                """,
                reward_id,
            )
            if reward is None:
                logger.warning(
                    f"Redemption for unmapped/inactive Kick reward {reward_id} -- "
                    f"add it with /kick-reward-add or it can't be paid out."
                )
                await write_audit(
                    conn,
                    entity_type="kick_reward",
                    entity_id=0,
                    action="unmapped_redemption_received",
                    actor="system",
                    metadata={"kick_reward_id": reward_id, "kick_redemption_id": kick_redemption_id},
                )
                return

            if not is_valid_solana_address_format(solana_address):
                logger.warning(
                    f"Kick redemption {kick_redemption_id} has invalid/missing "
                    f"Solana address -- flagging for manual review."
                )
                await write_audit(
                    conn,
                    entity_type="kick_reward",
                    entity_id=reward["id"],
                    action="invalid_address_received",
                    actor="system",
                    metadata={"kick_redemption_id": kick_redemption_id, "raw_input": solana_address},
                )
                return

            try:
                redemption_row = await conn.fetchrow(
                    """
                    INSERT INTO redemptions
                        (source, source_ref_id, kick_redemption_id, kick_user_id,
                         solana_address, amount_usd, status)
                    VALUES ('kick_reward', $1, $2, $3, $4, $5, 'validated')
                    RETURNING id
                    """,
                    reward["id"],
                    kick_redemption_id,
                    kick_user_id,
                    solana_address,
                    reward["amount_usd"],
                )
            except Exception as e:
                if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                    logger.info(f"Duplicate Kick redemption {kick_redemption_id}, ignoring.")
                    return
                raise

            payout_row = await conn.fetchrow(
                """
                INSERT INTO payouts (redemption_id, amount_usd, status)
                VALUES ($1, $2, 'pending')
                RETURNING id
                """,
                redemption_row["id"],
                reward["amount_usd"],
            )

            await write_audit(
                conn,
                entity_type="redemption",
                entity_id=redemption_row["id"],
                action="redeemed",
                actor=kick_user_id,
                metadata={
                    "source": "kick_reward",
                    "kick_redemption_id": kick_redemption_id,
                    "amount_usd": float(reward["amount_usd"]),
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

    logger.info(f"Recorded Kick redemption {kick_redemption_id} -> payout {payout_row['id']}")


def create_app() -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    return app
