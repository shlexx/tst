import os
import random
import json
import re
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
R34_USER_ID = os.environ["R34_USER_ID"]
R34_PASS_HASH = os.environ["R34_PASS_HASH"]

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

# Shared headers that look like a real browser
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://rule34.xxx/",
    "Cookie": f"user_id={R34_USER_ID}; pass_hash={R34_PASS_HASH}",
}

async def fetch_r34(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    pid = random.randint(0, 10)

    # Scrape the post listing page
    url = f"https://rule34.xxx/index.php?page=post&s=list&tags={encoded}&pid={pid * 42}"
    log.info(f"[r34] scraping | tags={tags!r} pid={pid} amount={amount}")
    log.info(f"[r34] url: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=BROWSER_HEADERS) as resp:
            log.info(f"[r34] http {resp.status} content-type={resp.content_type}")
            html = await resp.text()

    log.info(f"[r34] html length: {len(html)} | first 300: {html[:300]}")

    # Extract post IDs from thumbnail links: href="index.php?page=post&s=view&id=XXXXXX"
    post_ids = re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)
    post_ids = list(dict.fromkeys(post_ids))  # deduplicate, preserve order
    log.info(f"[r34] found {len(post_ids)} post ids on pid={pid}")

    if not post_ids:
        log.info("[r34] empty page, retrying pid=0")
        url0 = f"https://rule34.xxx/index.php?page=post&s=list&tags={encoded}&pid=0"
        async with aiohttp.ClientSession() as session:
            async with session.get(url0, headers=BROWSER_HEADERS) as resp:
                log.info(f"[r34] fallback http {resp.status}")
                html = await resp.text()
        post_ids = re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)
        post_ids = list(dict.fromkeys(post_ids))
        log.info(f"[r34] fallback found {len(post_ids)} post ids")

    if not post_ids:
        log.warning("[r34] no post ids found after fallback")
        return []

    # Pick random subset of IDs, then fetch each post page for the image URL
    chosen_ids = random.sample(post_ids, min(amount, len(post_ids)))
    log.info(f"[r34] fetching details for ids: {chosen_ids}")

    posts = []
    async with aiohttp.ClientSession() as session:
        for post_id in chosen_ids:
            post_url = f"https://rule34.xxx/index.php?page=post&s=view&id={post_id}"
            log.info(f"[r34] fetching post {post_id}: {post_url}")
            async with session.get(post_url, headers=BROWSER_HEADERS) as resp:
                log.info(f"[r34] post {post_id} http {resp.status}")
                post_html = await resp.text()

            # Try to extract the image URL from the post page
            # <img ... id="image" src="https://...rule34.xxx/...jpg" ...>
            img_match = re.search(r'id=["\']image["\'][^>]*src=["\']([^"\']+)["\']', post_html)
            if not img_match:
                img_match = re.search(r'src=["\']([^"\']+)["\'][^>]*id=["\']image["\']', post_html)

            # Also try video tag
            vid_match = re.search(r'<source[^>]+src=["\']([^"\']+\.(?:mp4|webm))["\']', post_html)

            # Extract score
            score_match = re.search(r'id=["\']psc(\d+)["\']', post_html)
            score = score_match.group(1) if score_match else "n/a"

            if vid_match:
                file_url = vid_match.group(1)
                log.info(f"[r34] post {post_id} video: {file_url}")
                posts.append({"id": post_id, "file_url": file_url, "score": score})
            elif img_match:
                file_url = img_match.group(1)
                log.info(f"[r34] post {post_id} image: {file_url}")
                posts.append({"id": post_id, "file_url": file_url, "score": score})
            else:
                log.warning(f"[r34] could not extract file url for post {post_id}")
                log.info(f"[r34] post html snippet: {post_html[1000:1500]}")

    log.info(f"[r34] returning {len(posts)} post(s)")
    return posts


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
        url=f"https://rule34.xxx/index.php?page=post&s=view&id={post['id']}",
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
@app_commands.checks.cooldown(1, 5)
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
