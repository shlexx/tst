import os
import random
import json
import xml.etree.ElementTree as ET
import aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import logging
import discord
from discord import app_commands

# ── Logging ───────────────────────────────────────────────────────────────────

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
        self.wfile.write(b"alive")
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
        log.info("slash commands synced globally")

    async def on_ready(self):
        log.info(f"logged in as {self.user}")

bot = Bot()

# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def fetch_r34(tags: str, amount: int) -> list:
    # paheal API returns XML with posts as <tag> elements
    url_tags = "%20".join(tags.strip().split())
    page = random.randint(1, 10)
    url = f"https://rule34.paheal.net/api/danbooru/find_posts?tags={url_tags}&limit=100&page={page}"
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DiscordBot/1.0)"}

    log.info(f"[r34] tags={tags!r} page={page} amount={amount}")
    log.info(f"[r34] url: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            log.info(f"[r34] http {resp.status} content-type={resp.content_type}")
            raw = await resp.text()

    log.info(f"[r34] raw response (first 300): {raw[:300]}")

    def parse_paheal(xml_text):
        try:
            root = ET.fromstring(xml_text)
        except Exception as e:
            log.error(f"[r34] xml parse failed: {e}")
            return []
        posts = []
        for tag in root.findall(".//tag"):
            file_url = tag.get("file_url")
            if file_url:
                posts.append({
                    "file_url": file_url,
                    "id": tag.get("id", "?"),
                    "score": tag.get("score", "n/a"),
                })
        return posts

    posts = parse_paheal(raw)
    log.info(f"[r34] got {len(posts)} posts on page={page}")

    if not posts:
        log.info("[r34] empty page, retrying page=1")
        url1 = f"https://rule34.paheal.net/api/danbooru/find_posts?tags={url_tags}&limit=100&page=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url1, headers=headers) as resp:
                log.info(f"[r34] fallback http {resp.status}")
                raw = await resp.text()
        log.info(f"[r34] fallback raw (first 300): {raw[:300]}")
        posts = parse_paheal(raw)
        log.info(f"[r34] fallback got {len(posts)} posts")

    if not posts:
        log.warning("[r34] no posts found after fallback")
        return []

    picked = random.sample(posts, min(amount, len(posts)))
    log.info(f"[r34] returning {len(picked)} post(s)")
    return picked


async def fetch_e621(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    page = random.randint(1, 20)
    url = f"https://e621.net/posts.json?tags={encoded}&limit=100&page={page}"
    headers = {"User-Agent": "DiscordBot/1.0 (by anonymous)"}

    log.info(f"[e621] tags={tags!r} page={page} amount={amount}")
    log.info(f"[e621] url: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            log.info(f"[e621] http {resp.status} content-type={resp.content_type}")
            raw = await resp.text()

    log.info(f"[e621] raw response (first 300): {raw[:300]}")

    try:
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[e621] json parse failed: {e}")
        return []

    posts = data.get("posts", [])
    log.info(f"[e621] got {len(posts)} posts on page={page}")

    if not posts:
        log.info("[e621] empty page, retrying page=1")
        url1 = f"https://e621.net/posts.json?tags={encoded}&limit=100&page=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url1, headers=headers) as resp:
                log.info(f"[e621] fallback http {resp.status}")
                raw = await resp.text()
        log.info(f"[e621] fallback raw (first 300): {raw[:300]}")
        try:
            data = json.loads(raw)
            posts = data.get("posts", [])
        except Exception as e:
            log.error(f"[e621] fallback json parse failed: {e}")
            return []
        log.info(f"[e621] fallback got {len(posts)} posts")

    if not posts:
        log.warning("[e621] no posts found after fallback")
        return []

    picked = random.sample(posts, min(amount, len(posts)))
    log.info(f"[e621] returning {len(picked)} post(s)")
    return picked

# ── Embed builders ────────────────────────────────────────────────────────────

def build_r34_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    log.info(f"[r34] building embed id={post.get('id')} url={file_url}")
    if file_url.endswith((".mp4", ".webm")):
        return None, f"[{tags}] {file_url} (video)"
    embed = discord.Embed(
        title=f"rule34 / {tags}",
        url=f"https://rule34.paheal.net/post/view/{post['id']}",
        color=0xFF4444,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {post.get('score', 'n/a')} | id: {post['id']}")
    return embed, None

def build_e621_embed(post: dict, tags: str):
    file_url = (post.get("file") or {}).get("url")
    log.info(f"[e621] building embed id={post.get('id')} url={file_url}")
    if not file_url:
        log.warning(f"[e621] no file url for post id={post.get('id')}")
        return None, None
    if file_url.endswith((".mp4", ".webm")):
        return None, f"[{tags}] {file_url} (video)"
    score = (post.get("score") or {}).get("total", "n/a")
    embed = discord.Embed(
        title=f"e621 / {tags}",
        url=f"https://e621.net/posts/{post['id']}",
        color=0x00549E,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {score} | id: {post['id']}")
    return embed, None

# ── Send results ──────────────────────────────────────────────────────────────

async def send_results(interaction, posts, builder_fn, tags):
    if not posts:
        log.warning(f"no posts to send for tags={tags!r}")
        await interaction.followup.send(f"no results for tags: `{tags}`. try different tags.")
        return

    embeds, video_links = [], []
    for post in posts:
        embed, video = builder_fn(post, tags)
        if embed:
            embeds.append(embed)
        if video:
            video_links.append(video)

    log.info(f"sending {len(embeds)} embed(s) and {len(video_links)} video link(s)")

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

@bot.tree.command(name="r34", description="fetch random posts from rule34")
@app_commands.describe(tags='space-separated tags, e.g. "catgirl anime"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def r34(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    log.info(f"[r34] invoked by {interaction.user} | tags={tags!r} amount={amount}")
    await interaction.response.defer()
    try:
        posts = await fetch_r34(tags, amount)
        await send_results(interaction, posts, build_r34_embed, tags)
    except Exception as e:
        log.exception(f"[r34] unhandled error: {e}")
        await interaction.followup.send("an error occurred. please try again.")

@bot.tree.command(name="e621", description="fetch random posts from e621")
@app_commands.describe(tags='space-separated tags, e.g. "dragon solo"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def e621(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    log.info(f"[e621] invoked by {interaction.user} | tags={tags!r} amount={amount}")
    await interaction.response.defer()
    try:
        posts = await fetch_e621(tags, amount)
        await send_results(interaction, posts, build_e621_embed, tags)
    except Exception as e:
        log.exception(f"[e621] unhandled error: {e}")
        await interaction.followup.send("an error occurred. please try again.")

@r34.error
@e621.error
async def on_cooldown(interaction: discord.Interaction, error):
    if isinstance(error, app_commands.CommandOnCooldown):
        await interaction.response.send_message(
            f"slow down. try again in {error.retry_after:.1f}s.", ephemeral=True
        )

# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    threading.Thread(target=run_keep_alive, daemon=True).start()
    log.info(f"keep-alive server started on port {PORT}")
    bot.run(TOKEN)
