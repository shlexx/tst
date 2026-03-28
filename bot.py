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
from discord.ui import View, Button

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
    "Accept-Language": "en-US,en;q=0.5",
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
        # set link button url
        self.open_btn.url = f"https://nhentai.net/g/{gallery['id']}/"

    def update_buttons(self):
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total - 1

    @discord.ui.button(label="◀ prev", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(
            embed=build_page_embed(self.gallery, self.page), view=self
        )

    @discord.ui.button(label="next ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(
            embed=build_page_embed(self.gallery, self.page), view=self
        )

    @discord.ui.button(label="🔗 open", style=discord.ButtonStyle.link)
    async def open_btn(self, interaction: discord.Interaction, button: Button):
        pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True

# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def fetch_gelbooru(tags: str, amount: int) -> list:
    encoded = "%20".join(tags.strip().split())
    pid = random.randint(0, 19)
    url = (
        f"https://gelbooru.com/index.php?page=dapi&s=post&q=index"
        f"&json=1&limit=100&pid={pid}&tags={encoded}"
        f"&api_key={GEL_API_KEY}&user_id={GEL_USER_ID}"
    )
    headers = {"User-Agent": "Mozilla/5.0 (compatible; DiscordBot/1.0)"}
    log.info(f"[gel] tags={tags!r} pid={pid} amount={amount}")
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=headers) as resp:
            log.info(f"[gel] http {resp.status}")
            raw = await resp.text()
    log.info(f"[gel] raw (first 300): {raw[:300]}")
    try:
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[gel] json parse failed: {e}")
        return []
    posts = data.get("post", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    if not posts:
        url0 = (
            f"https://gelbooru.com/index.php?page=dapi&s=post&q=index"
            f"&json=1&limit=100&pid=0&tags={encoded}"
            f"&api_key={GEL_API_KEY}&user_id={GEL_USER_ID}"
        )
        async with aiohttp.ClientSession() as session:
            async with session.get(url0, headers=headers) as resp:
                raw = await resp.text()
        try:
            data = json.loads(raw)
            posts = data.get("post", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
        except:
            return []
    if not posts:
        log.warning("[gel] no posts found")
        return []
    return random.sample(posts, min(amount, len(posts)))


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


async def fetch_realbooru(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    pid = random.randint(0, 10)
    url = f"https://realbooru.com/index.php?page=post&s=list&tags={encoded}&pid={pid * 42}"
    log.info(f"[realb] scraping tags={tags!r} pid={pid} amount={amount}")
    log.info(f"[realb] url: {url}")

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=BROWSER_HEADERS) as resp:
            log.info(f"[realb] http {resp.status} content-type={resp.content_type}")
            html = await resp.text()

    log.info(f"[realb] html length: {len(html)} | first 300: {html[:300]}")

    post_ids = list(dict.fromkeys(re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)))
    log.info(f"[realb] found {len(post_ids)} post ids")

    if not post_ids:
        log.info("[realb] retrying pid=0")
        url0 = f"https://realbooru.com/index.php?page=post&s=list&tags={encoded}&pid=0"
        async with aiohttp.ClientSession() as session:
            async with session.get(url0, headers=BROWSER_HEADERS) as resp:
                log.info(f"[realb] fallback http {resp.status}")
                html = await resp.text()
        post_ids = list(dict.fromkeys(re.findall(r'page=post&amp;s=view&amp;id=(\d+)', html)))
        log.info(f"[realb] fallback found {len(post_ids)} post ids")

    if not post_ids:
        log.warning("[realb] no post ids found")
        return []

    chosen = random.sample(post_ids, min(amount, len(post_ids)))
    posts = []

    async with aiohttp.ClientSession() as session:
        for post_id in chosen:
            post_url = f"https://realbooru.com/index.php?page=post&s=view&id={post_id}"
            log.info(f"[realb] fetching post {post_id}")
            async with session.get(post_url, headers=BROWSER_HEADERS) as resp:
                log.info(f"[realb] post {post_id} http {resp.status}")
                post_html = await resp.text()

            img_match = re.search(r'id=["\']image["\'][^>]*src=["\']([^"\']+)["\']', post_html)
            if not img_match:
                img_match = re.search(r'src=["\']([^"\']+)["\'][^>]*id=["\']image["\']', post_html)
            vid_match = re.search(r'<source[^>]+src=["\']([^"\']+\.(?:mp4|webm))["\']', post_html)

            if vid_match:
                file_url = vid_match.group(1)
                log.info(f"[realb] post {post_id} video: {file_url}")
                posts.append({"id": post_id, "file_url": file_url, "score": "n/a"})
            elif img_match:
                file_url = img_match.group(1)
                log.info(f"[realb] post {post_id} image: {file_url}")
                posts.append({"id": post_id, "file_url": file_url, "score": "n/a"})
            else:
                log.warning(f"[realb] could not extract file url for post {post_id}")
                log.info(f"[realb] html snippet: {post_html[1000:1500]}")

    log.info(f"[realb] returning {len(posts)} post(s)")
    return posts


async def fetch_nhentai_gallery(tags: str) -> dict | None:
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
        log.warning("[nh] no gallery ids found")
        return None
    gid = random.choice(ids)
    api_url = f"https://nhentai.net/api/gallery/{gid}"
    async with aiohttp.ClientSession() as session:
        async with session.get(api_url, headers=BROWSER_HEADERS) as resp:
            log.info(f"[nh] gallery {gid} http {resp.status}")
            raw = await resp.text()
    try:
        data = json.loads(raw)
    except Exception as e:
        log.error(f"[nh] gallery parse failed: {e}")
        return None
    media_id = data.get("media_id")
    pages = data.get("images", {}).get("pages", [])
    title = (data.get("title") or {}).get("english") or (data.get("title") or {}).get("pretty", "unknown")
    if not pages:
        return None
    return {"id": gid, "media_id": media_id, "title": title, "pages": pages}


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

def build_gel_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    if file_url.endswith((".mp4", ".webm")):
        return None, f"[{tags}] {file_url} (video)"
    embed = discord.Embed(
        title=f"gelbooru / {tags}",
        url=f"https://gelbooru.com/index.php?page=post&s=view&id={post.get('id')}",
        color=0xFF4444,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {post.get('score', 'n/a')} | id: {post.get('id')}")
    return embed, None

def build_xbooru_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    if not file_url:
        image = post.get("image", "")
        directory = post.get("directory", "")
        if image and directory:
            file_url = f"https://img.xbooru.com/images/{directory}/{image}"
    if file_url.endswith((".mp4", ".webm")):
        return None, f"[{tags}] {file_url} (video)"
    embed = discord.Embed(
        title=f"xbooru / {tags}",
        url=f"https://xbooru.com/index.php?page=post&s=view&id={post.get('id')}",
        color=0x9B59B6,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {post.get('score', 'n/a')} | id: {post.get('id')}")
    return embed, None

def build_realbooru_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
    if file_url.endswith((".mp4", ".webm")):
        return None, f"[{tags}] {file_url} (video)"
    embed = discord.Embed(
        title=f"realbooru / {tags}",
        url=f"https://realbooru.com/index.php?page=post&s=view&id={post.get('id')}",
        color=0xFF8800,
    )
    embed.set_image(url=file_url)
    embed.set_footer(text=f"score: {post.get('score', 'n/a')} | id: {post.get('id')}")
    return embed, None

def build_e621_embed(post: dict, tags: str):
    file_url = (post.get("file") or {}).get("url")
    if not file_url:
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
        await interaction.followup.send(f"no results for tags: `{tags}`. try different tags.")
        return
    embeds, video_links = [], []
    for post in posts:
        result = builder_fn(post, tags)
        if not result:
            continue
        embed, video = result
        if embed:
            embeds.append(embed)
        if video:
            video_links.append(video)
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

@bot.tree.command(name="gel", description="fetch random posts from gelbooru (nsfw channels only)")
@app_commands.describe(tags='space-separated tags, e.g. "catgirl anime"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def gel(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    if not getattr(interaction.channel, "nsfw", False):
        await interaction.response.send_message("this command can only be used in nsfw channels.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        posts = await fetch_gelbooru(tags, amount)
        await send_results(interaction, posts, build_gel_embed, tags)
    except Exception as e:
        log.exception(f"[gel] unhandled error: {e}")
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

@bot.tree.command(name="realbooru", description="fetch random posts from realbooru (nsfw channels only)")
@app_commands.describe(tags='space-separated tags, e.g. "blonde"', amount="number of posts 1-10 (default: 1)")
@app_commands.checks.cooldown(1, 5)
async def realbooru(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    if not getattr(interaction.channel, "nsfw", False):
        await interaction.response.send_message("this command can only be used in nsfw channels.", ephemeral=True)
        return
    await interaction.response.defer()
    try:
        posts = await fetch_realbooru(tags, amount)
        await send_results(interaction, posts, build_realbooru_embed, tags)
    except Exception as e:
        log.exception(f"[realb] unhandled error: {e}")
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
        embed = build_page_embed(gallery, 0)
        await interaction.followup.send(embed=embed, view=view)
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

@gel.error
@xb.error
@realbooru.error
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
