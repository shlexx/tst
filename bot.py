import os
import random
import asyncio
import aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import logging
import discord
from discord import app_commands

# ── Logging setup ─────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

TOKEN = os.environ["DISCORD_TOKEN"]
CLIENT_ID = int(os.environ["CLIENT_ID"])
PORT = int(os.environ.get("PORT", 3000))

# ── Keep-alive server ─────────────────────────────────────────────────────────

class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, *args):
        pass

def run_keep_alive():
    HTTPServer(("0.0.0.0", PORT), KeepAlive).serve_forever()

# ── Discord client ────────────────────────────────────────────────────────────

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        log.info("✅ Slash commands synced globally.")

    async def on_ready(self):
        log.info(f"🤖 Logged in as {self.user}")

bot = Bot()

# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def fetch_r34(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    pid = random.randint(0, 19)
    url = (
        f"https://api.rule34.xxx/index.php"
        f"?page=dapi&s=post&q=index&json=1&limit=100&pid={pid}&tags={encoded}"
    )
    log.info(f"[R34] Fetching | tags={tags!r} pid={pid} amount={amount}")
    log.info(f"[R34] URL: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "DiscordBot/1.0"}) as resp:
            log.info(f"[R34] HTTP {resp.status} | content-type: {resp.content_type}")
            raw = await resp.text()

    log.info(f"[R34] Raw response (first 300 chars): {raw[:300]}")

    try:
        import json
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[R34] Failed to parse JSON: {e}")
        return []

    if not isinstance(data, list):
        log.warning(f"[R34] Unexpected response type: {type(data)} | value: {str(data)[:200]}")
        return []

    log.info(f"[R34] Got {len(data)} posts on pid={pid}")

    if not data:
        # Fallback to pid=0
        log.info("[R34] Empty page, retrying with pid=0")
        url0 = (
            f"https://api.rule34.xxx/index.php"
            f"?page=dapi&s=post&q=index&json=1&limit=100&pid=0&tags={encoded}"
        )
        log.info(f"[R34] Fallback URL: {url0}")
        async with aiohttp.ClientSession() as session:
            async with session.get(url0, headers={"User-Agent": "DiscordBot/1.0"}) as resp:
                log.info(f"[R34] Fallback HTTP {resp.status}")
                raw = await resp.text()
        log.info(f"[R34] Fallback raw (first 300 chars): {raw[:300]}")
        try:
            data = json.loads(raw)
        except Exception as e:
            log.error(f"[R34] Fallback JSON parse failed: {e}")
            return []
        log.info(f"[R34] Fallback got {len(data) if isinstance(data, list) else 'N/A'} posts")

    if not isinstance(data, list) or not data:
        log.warning("[R34] No posts found after fallback.")
        return []

    picked = random.sample(data, min(amount, len(data)))
    log.info(f"[R34] Returning {len(picked)} post(s)")
    return picked


async def fetch_e621(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    page = random.randint(1, 20)
    url = f"https://e621.net/posts.json?tags={encoded}&limit=100&page={page}"
    log.info(f"[E621] Fetching | tags={tags!r} page={page} amount={amount}")
    log.info(f"[E621] URL: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, headers={"User-Agent": "DiscordBot/1.0 (by anonymous)"}
        ) as resp:
            log.info(f"[E621] HTTP {resp.status} | content-type: {resp.content_type}")
            raw = await resp.text()

    log.info(f"[E621] Raw response (first 300 chars): {raw[:300]}")

    try:
        import json
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[E621] Failed to parse JSON: {e}")
        return []

    posts = data.get("posts", [])
    log.info(f"[E621] Got {len(posts)} posts on page={page}")

    if not posts:
        log.info("[E621] Empty page, retrying with page=1")
        url1 = f"https://e621.net/posts.json?tags={encoded}&limit=100&page=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(
                url1, headers={"User-Agent": "DiscordBot/1.0 (by anonymous)"}
            ) as resp:
                log.info(f"[E621] Fallback HTTP {resp.status}")
                raw = await resp.text()
        log.info(f"[E621] Fallback raw (first 300 chars): {raw[:300]}")
        try:
            data = json.loads(raw)
            posts = data.get("posts", [])
        except Exception as e:
            log.error(f"[E621] Fallback JSON parse failed: {e}")
            return []
        log.info(f"[E621] Fallback got {len(posts)} posts")

    if not posts:
        log.warning("[E621] No posts found after fallback.")
        return []

    picked = random.sample(posts, min(amount, len(posts)))
    log.info(f"[E621] Returning {len(picked)} post(s)")
    return picked

# ── Embed builders ────────────────────────────────────────────────────────────

def build_r34_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    log.info(f"[R34] Building embed for post id={post.get('id')} url={file_url}")
    if file_url.endswith((".mp4", ".webm")):
        return None, f"🎬 **[{tags}]** {file_url}"
    embed = discord.Embed(
        title=f"r34 — {tags}",
        url=f"https://rule34.xxx/index.php?page=post&s=view&id={post['id']}",
        color=0xFF4444,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"Score: {post.get('score', 'N/A')} | ID: {post['id']}")
    return embed, None

def build_e621_embed(post: dict, tags: str):
    file_url = (post.get("file") or {}).get("url")
    log.info(f"[E621] Building embed for post id={post.get('id')} url={file_url}")
    if not file_url:
        log.warning(f"[E621] No file URL for post id={post.get('id')}")
        return None, None
    if file_url.endswith((".mp4", ".webm")):
        return None, f"🎬 **[{tags}]** {file_url}"
    score = (post.get("score") or {}).get("total", "N/A")
    embed = discord.Embed(
        title=f"e621 — {tags}",
        url=f"https://e621.net/posts/{post['id']}",
        color=0x00549E,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"Score: {score} | ID: {post['id']}")
    return embed, None

# ── Send results ──────────────────────────────────────────────────────────────

async def send_results(interaction, posts, builder_fn, tags):
    if not posts:
        log.warning(f"No posts to send for tags={tags!r}")
        await interaction.followup.send(
            f"no results for tags: `{tags}`. Try different tags."
        )
        return

    embeds, video_links = [], []
    for post in posts:
        embed, video = builder_fn(post, tags)
        if embed:
            embeds.append(embed)
        if video:
            video_links.append(video)

    log.info(f"Sending {len(embeds)} embed(s) and {len(video_links)} video link(s)")

    if not embeds and not video_links:
        await interaction.followup.send("could not build any embeds for those posts.")
        return

    first = True
    for i in range(0, max(len(embeds), 1), 10):
        chunk = embeds[i:i + 10]
        content = "\n".join(video_links) if (first and video_links) else None
        if first:
            await interaction.followup.send(content=content, embeds=chunk)
            first = False
        else:
            await interaction.followup.send(embeds=chunk)

# ── Slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="r34", description="fetch random posts from r34")
@app_commands.describe(tags='space-separated tags, e.g. "catgirl anime"', amount="mumber of posts 1–10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def r34(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    log.info(f"[R34] Command invoked by {interaction.user} | tags={tags!r} amount={amount}")
    await interaction.response.defer()
    try:
        posts = await fetch_r34(tags, amount)
        await send_results(interaction, posts, build_r34_embed, tags)
    except Exception as e:
        log.exception(f"[R34] Unhandled error: {e}")
        await interaction.followup.send("an error occurred. Please try again.")

@bot.tree.command(name="e621", description="fetch random posts from e621")
@app_commands.describe(tags='space-separated tags, e.g. "dragon solo"', amount="number of posts 1–10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def e621(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    log.info(f"[E621] Command invoked by {interaction.user} | tags={tags!r} amount={amount}")
    await interaction.response.defer()
    try:
        posts = await fetch_e621(tags, amount)
        await send_results(interaction, posts, build_e621_embed, tags)
    except Exception as e:
        log.exception(f"[E621] Unhandled error: {e}")
        await interaction.followup.send("an error occurred. Please try again.")

@r34.error
@e621.error
async def on_cooldown(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"slow down! try again in {error.retry_after:.1f}s.", ephemeral=True
        )

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=run_keep_alive, daemon=True).start()
    log.info(f"🌐 Keep-alive server started on port {PORT}")
    bot.run(TOKEN)
