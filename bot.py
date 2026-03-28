import os
import io
import random
import json
import re
import aiohttp
import urllib.request
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import logging
import discord
from discord import app_commands
from discord.ui import View, Button

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("bot")

TOKEN = os.environ["DISCORD_TOKEN"]
CLIENT_ID = int(os.environ["CLIENT_ID"])
PORT = int(os.environ.get("PORT", 3000))
GEL_API_KEY = os.environ["GEL_API_KEY"]
GEL_USER_ID = os.environ["GEL_USER_ID"]

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

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/123.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://gelbooru.com/",
    "Connection": "keep-alive",
}

# ── nhentai page reader ───────────────────────────────────────────────────────

def build_page_embed(gallery: dict, page: int) -> discord.Embed:
    media_id = gallery["media_id"]
    pages = gallery["pages"]
    total = len(pages)
    ext_map = {"j": "jpg", "p": "png", "g": "gif", "w": "webp"}
    ext = ext_map.get(pages[page].get("t", "j"), "jpg")
    img_url = f"https://i.nhentai.net/galleries/{media_id}/{page + 1}.{ext}"
    embed = discord.Embed(
        title=gallery["title"],
        url=f"https://nhentai.net/g/{gallery['id']}/",
        color=0xED2553,
    )
    embed.set_image(url=img_url)
    embed.set_footer(text=f"page {page + 1} / {total} | id: {gallery['id']}")
    return embed

class ReaderView(View):
    def __init__(self, gallery: dict):
        super().__init__(timeout=300)
        self.gallery = gallery
        self.page = 0
        self.total = len(gallery["pages"])
        self.update_buttons()
        self.add_item(discord.ui.Button(
            label="open",
            style=discord.ButtonStyle.link,
            url=f"https://nhentai.net/g/{gallery['id']}/"
        ))

    def update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total - 1

    @discord.ui.button(label="prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(
            embed=build_page_embed(self.gallery, self.page), view=self
        )

    @discord.ui.button(label="next", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(
            embed=build_page_embed(self.gallery, self.page), view=self
        )

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_file_url(html: str):
    """Extract image or video URL from a booru post page."""
    # try video first
    m = re.search(r'<source[^>]+src=.([^"\'> ]+\.(?:mp4|webm))', html)
    if m:
        return m.group(1), True
    # try image with id="image"
    m = re.search(r'id=.image.[^>]+src=.([^"\'> ]+)', html)
    if m:
        return m.group(1), False
    m = re.search(r'src=.([^"\'> ]+)[^>]+id=.image.', html)
    if m:
        return m.group(1), False
    return None, False

# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def fetch_gb(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    pid = random.randint(0, 10)
    url = f"https://gelbooru.com/index.php?page=post&s=list&tags={encoded}&pid={pid * 42}"
    log.info(f"[gb] scraping tags={tags!r} pid={pid} amount={amount}")
    log.info(f"[gb] url: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=BROWSER_HEADERS) as resp:
            log.info(f"[gb] http {resp.status} content-type={resp.content_type}")
            html = await resp.text()

    log.info(f"[gb] html length={len(html)}")
    log.info(f"[gb] middle chunk: {html[5000:6000]}")

    post_ids = list(dict.fromkeys(re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)))
    log.info(f"[gb] found {len(post_ids)} post ids")

    if not post_ids:
        log.info("[gb] retrying pid=0")
        url0 = f"https://gelbooru.com/index.php?page=post&s=list&tags={encoded}&pid=0"
        async with aiohttp.ClientSession() as session:
            async with session.get(url0, headers=BROWSER_HEADERS) as resp:
                log.info(f"[gb] fallback http {resp.status}")
                html = await resp.text()
        post_ids = list(dict.fromkeys(re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)))
        log.info(f"[gb] fallback found {len(post_ids)} post ids")

    if not post_ids:
        log.warning("[gb] no post ids found")
        return []

    chosen = random.sample(post_ids, min(amount, len(post_ids)))
    posts = []

    async with aiohttp.ClientSession() as session:
        for post_id in chosen:
            post_url = f"https://gelbooru.com/index.php?page=post&s=view&id={post_id}"
            async with session.get(post_url, headers=BROWSER_HEADERS) as resp:
                log.info(f"[gb] post {post_id} http {resp.status}")
                post_html = await resp.text()

            file_url, is_video = extract_file_url(post_html)
            if file_url:
                posts.append({"id": post_id, "file_url": file_url, "is_video": is_video, "score": "n/a"})
                log.info(f"[gb] post {post_id} {'video' if is_video else 'image'}: {file_url}")
            else:
                log.warning(f"[gb] could not extract file url for post {post_id}")
                log.info(f"[gb] html snippet: {post_html[1000:1500]}")

    log.info(f"[gb] returning {len(posts)} post(s)")
    return posts


async def fetch_xbooru(tags: str, amount: int) -> list:
    encoded = "%20".join(tags.strip().split())
    pid = random.randint(0, 19)
    url = (
        f"https://xbooru.com/index.php?page=dapi&s=post&q=index"
        f"&json=1&limit=100&pid={pid}&tags={encoded}"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DiscordBot/1.0)"}
    log.info(f"[xb] tags={tags!r} pid={pid} amount={amount}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            log.info(f"[xb] http {resp.status}")
            raw = await resp.text()
    log.info(f"[xb] raw (first 300): {raw[:300]}")
    try:
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[xb] json parse failed: {e}")
        return []
    posts = data if isinstance(data, list) else data.get("post", [])
    if not posts:
        url0 = (
            f"https://xbooru.com/index.php?page=dapi&s=post&q=index"
            f"&json=1&limit=100&pid=0&tags={encoded}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url0, headers=headers) as resp:
                raw = await resp.text()
        try:
            data = json.loads(raw)
            posts = data if isinstance(data, list) else data.get("post", [])
        except:
            return []
    if not posts:
        log.warning("[xb] no posts found")
        return []
    return random.sample(posts, min(amount, len(posts)))


async def fetch_rb(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    pid = random.randint(0, 10)
    url = f"https://realbooru.com/index.php?page=post&s=list&tags={encoded}&pid={pid * 42}"
    log.info(f"[rb] scraping tags={tags!r} pid={pid} amount={amount}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=BROWSER_HEADERS) as resp:
            log.info(f"[rb] http {resp.status}")
            html = await resp.text()
    log.info(f"[rb] html length={len(html)} first 300: {html[:300]}")
    post_ids = list(dict.fromkeys(re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)))
    log.info(f"[rb] found {len(post_ids)} post ids")
    if not post_ids:
        url0 = f"https://realbooru.com/index.php?page=post&s=list&tags={encoded}&pid=0"
        async with aiohttp.ClientSession() as session:
            async with session.get(url0, headers=BROWSER_HEADERS) as resp:
                html = await resp.text()
        post_ids = list(dict.fromkeys(re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)))
        log.info(f"[rb] fallback found {len(post_ids)} post ids")
    if not post_ids:
        log.warning("[rb] no post ids found")
        return []
    chosen = random.sample(post_ids, min(amount, len(post_ids)))
    posts = []
    async with aiohttp.ClientSession() as session:
        for post_id in chosen:
            post_url = f"https://realbooru.com/index.php?page=post&s=view&id={post_id}"
            async with session.get(post_url, headers=BROWSER_HEADERS) as resp:
                post_html = await resp.text()
            file_url, is_video = extract_file_url(post_html)
            if file_url:
                posts.append({"id": post_id, "file_url": file_url, "is_video": is_video, "score": "n/a"})
            else:
                log.warning(f"[rb] could not extract file url for post {post_id}")
    log.info(f"[rb] returning {len(posts)} post(s)")
    return posts


async def fetch_nhentai_gallery(tags: str):
    encoded = "%20".join(tags.strip().split())
    page = random.randint(1, 5)
    url = f"https://nhentai.net/search/?q={encoded}&page={page}"
    log.info(f"[nh] searching tags={tags!r} page={page}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=BROWSER_HEADERS) as resp:
            log.info(f"[nh] http {resp.status}")
            html = await resp.text()
    ids = list(dict.fromkeys(re.findall(r'href="/g/(\d+)/"', html)))
    log.info(f"[nh] found {len(ids)} gallery ids")
    if not ids:
        return None
    gid = random.choice(ids)
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://nhentai.net/api/gallery/{gid}", headers=BROWSER_HEADERS) as resp:
            log.info(f"[nh] gallery {gid} http {resp.status}")
            raw = await resp.text()
    try:
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[nh] gallery parse failed: {e}")
        return None
    pages = data.get("images", {}).get("pages", [])
    title = (data.get("title") or {}).get("english") or (data.get("title") or {}).get("pretty", "unknown")
    if not pages:
        return None
    return {"id": gid, "media_id": data.get("media_id"), "title": title, "pages": pages}


async def fetch_e621(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    page = random.randint(1, 20)
    url = f"https://e621.net/posts.json?tags={encoded}&limit=100&page={page}"
    headers = {"User-Agent": "DiscordBot/1.0 (by anonymous)"}
    log.info(f"[e621] tags={tags!r} page={page} amount={amount}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            log.info(f"[e621] http {resp.status}")
            raw = await resp.text()
    log.info(f"[e621] raw (first 300): {raw[:300]}")
    try:
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[e621] json parse failed: {e}")
        return []
    posts = data.get("posts", [])
    if not posts:
        url1 = f"https://e621.net/posts.json?tags={encoded}&limit=100&page=1"
        async with aiohttp.ClientSession() as session:
            async with session.get(url1, headers=headers) as resp:
                raw = await resp.text()
        try:
            posts = json.loads(raw).get("posts", [])
        except:
            return []
    if not posts:
        return []
    return random.sample(posts, min(amount, len(posts)))

# ── Embed builders ────────────────────────────────────────────────────────────

def build_gb_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    if not file_url:
        return None, None
    if post.get("is_video") or file_url.endswith((".mp4", ".webm")):
        return "video", file_url
    embed = discord.Embed(
        title=f"gb / {tags}",
        url=f"https://gelbooru.com/index.php?page=post&s=view&id={post.get('id')}",
        color=0xFF4444,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {post.get('score', 'n/a')} | id: {post.get('id')}")
    return "embed", embed

def build_xbooru_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    if not file_url:
        image = post.get("image", "")
        directory = post.get("directory", "")
        if image and directory:
            file_url = f"https://img.xbooru.com/images/{directory}/{image}"
    if not file_url:
        return None, None
    if file_url.endswith((".mp4", ".webm")):
        return "video", file_url
    embed = discord.Embed(
        title=f"xb / {tags}",
        url=f"https://xbooru.com/index.php?page=post&s=view&id={post.get('id')}",
        color=0x9B59B6,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {post.get('score', 'n/a')} | id: {post.get('id')}")
    return "embed", embed

def build_rb_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    if not file_url:
        return None, None
    if post.get("is_video") or file_url.endswith((".mp4", ".webm")):
        return "video", file_url
    embed = discord.Embed(
        title=f"rb / {tags}",
        url=f"https://realbooru.com/index.php?page=post&s=view&id={post.get('id')}",
        color=0xFF8800,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {post.get('score', 'n/a')} | id: {post.get('id')}")
    return "embed", embed

def build_e621_embed(post: dict, tags: str):
    file_url = (post.get("file") or {}).get("url")
    if not file_url:
        return None, None
    if file_url.endswith((".mp4", ".webm")):
        return "video", file_url
    score = (post.get("score") or {}).get("total", "n/a")
    embed = discord.Embed(
        title=f"e621 / {tags}",
        url=f"https://e621.net/posts/{post['id']}",
        color=0x00549E,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {score} | id: {post['id']}")
    return "embed", embed

# ── Send results ──────────────────────────────────────────────────────────────

async def send_results(interaction, posts, builder_fn, tags):
    if not posts:
        await interaction.followup.send(f"no results for tags: `{tags}`. try different tags.")
        return
    embeds, video_urls = [], []
    for post in posts:
        result = builder_fn(post, tags)
        if not result or result[0] is None:
            continue
        kind, value = result
        if kind == "embed":
            embeds.append(value)
        elif kind == "video":
            video_urls.append(value)
    if not embeds and not video_urls:
        await interaction.followup.send("could not build any embeds for those posts.")
        return
    if embeds:
        for i in range(0, len(embeds), 10):
            await interaction.followup.send(embeds=embeds[i:i + 10])
    for url in video_urls:
        log.info(f"sending video: {url}")
        await interaction.followup.send(f"|| {url} ||")

# ── Slash commands ────────────────────────────────────────────────────────────

@bot.tree.command(name="gb", description="fetch random posts from gelbooru (nsfw channels only)")
@app_commands.describe(tags='space-separated tags, e.g. "catgirl anime"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 5)
async def gb(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    if not getattr(interaction.channel, "nsfw", False):
        await interaction.response.send_message("this command can only be used in nsfw channels.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        posts = await fetch_gb(tags, amount)
        await send_results(interaction, posts, build_gb_embed, tags)
    except Exception as e:
        log.exception(f"[gb] unhandled error: {e}")
        await interaction.followup.send("an error occurred. please try again.")

@bot.tree.command(name="xb", description="fetch random posts from xbooru (nsfw channels only)")
@app_commands.describe(tags='space-separated tags, e.g. "catgirl anime"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def xb(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    if not getattr(interaction.channel, "nsfw", False):
        await interaction.response.send_message("this command can only be used in nsfw channels.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        posts = await fetch_xbooru(tags, amount)
        await send_results(interaction, posts, build_xbooru_embed, tags)
    except Exception as e:
        log.exception(f"[xb] unhandled error: {e}")
        await interaction.followup.send("an error occurred. please try again.")

@bot.tree.command(name="rb", description="fetch random posts from realbooru (nsfw channels only)")
@app_commands.describe(tags='space-separated tags, e.g. "blonde"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 5)
async def rb(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    if not getattr(interaction.channel, "nsfw", False):
        await interaction.response.send_message("this command can only be used in nsfw channels.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        posts = await fetch_rb(tags, amount)
        await send_results(interaction, posts, build_rb_embed, tags)
    except Exception as e:
        log.exception(f"[rb] unhandled error: {e}")
        await interaction.followup.send("an error occurred. please try again.")

@bot.tree.command(name="nh", description="read a random doujin from nhentai (nsfw channels only)")
@app_commands.describe(tags='space-separated tags, e.g. "milf big breasts"')
@app_commands.checks.cooldown(1, 5)
async def nh(interaction: discord.Interaction, tags: str):
    if not getattr(interaction.channel, "nsfw", False):
        await interaction.response.send_message("this command can only be used in nsfw channels.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        gallery = await fetch_nhentai_gallery(tags)
        if not gallery:
            await interaction.followup.send(f"no results for tags: `{tags}`. try different tags.")
            return
        view = ReaderView(gallery)
        await interaction.followup.send(embed=build_page_embed(gallery, 0), view=view)
    except Exception as e:
        log.exception(f"[nh] unhandled error: {e}")
        await interaction.followup.send("an error occurred. please try again.")

@bot.tree.command(name="e621", description="fetch random posts from e621 (nsfw channels only)")
@app_commands.describe(tags='space-separated tags, e.g. "dragon solo"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def e621(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    if not getattr(interaction.channel, "nsfw", False):
        await interaction.response.send_message("this command can only be used in nsfw channels.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        posts = await fetch_e621(tags, amount)
        await send_results(interaction, posts, build_e621_embed, tags)
    except Exception as e:
        log.exception(f"[e621] unhandled error: {e}")
        await interaction.followup.send("an error occurred. please try again.")

@gb.error
@xb.error
@rb.error
@nh.error
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
