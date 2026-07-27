import asyncio
import logging
import os

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db.pool import init_pool

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("giveaway-bot")

intents = discord.Intents.default()
intents.message_content = False  # not needed; slash commands only

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    logger.info(f"Logged in as {bot.user} (id: {bot.user.id})")
    guild_id = os.environ.get("DISCORD_GUILD_ID")
    if guild_id:
        guild = discord.Object(id=int(guild_id))
        bot.tree.copy_global_to(guild=guild)
        synced = await bot.tree.sync(guild=guild)
        logger.info(f"Synced {len(synced)} commands to guild {guild_id}")
    else:
        synced = await bot.tree.sync()
        logger.info(f"Synced {len(synced)} global commands")


async def main():
    await init_pool()
    async with bot:
        await bot.load_extension("bot.cogs.giveaway")
        await bot.start(os.environ["DISCORD_BOT_TOKEN"])


if __name__ == "__main__":
    asyncio.run(main())
