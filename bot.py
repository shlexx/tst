import os
import random
import asyncio
import aiohttp
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import discord
from discord import app_commands

TOKEN = os.environ["DISCORD_TOKEN"]
CLIENT_ID = int(os.environ["CLIENT_ID"])
PORT = int(os.environ.get("PORT", 3000))

# ── Keep-alive server (for UptimeRobot / Render) ──────────────────────────────

class KeepAlive(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is alive!")
    def log_message(self, *args):
        pass  # suppress access logs

def run_keep_alive():
    HTTPServer(("0.0.0.0", PORT), KeepAlive).serve_forever()

# ── Discord client ────────────────────────────────────────────────────────────

class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        print("✅ Slash commands synced globally.")

    async def on_ready(self):
        print(f"🤖 Logged in as {self.user}")

bot = Bot()

# ── Fetch helpers ─────────────────────────────────────────────────────────────

async def fetch_r34(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    pid = random.randint(0, 19)
    url = (
        f"https://api.rule34.xxx/index.php"
        f"?page=dapi&s=post&q=index&json=1&limit=100&pid={pid}&tags={encoded}"
    )
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers={"User-Agent": "DiscordBot/1.0"}) as resp:
            data = await resp.json(content_type=None)
    if not isinstance(data, list) or not data:
        return []
    return random.sample(data, min(amount, len(data)))

async def fetch_e621(tags: str, amount: int) -> list:
    encoded = "+".join(tags.strip().split())
    page = random.randint(1, 20)
    url = f"https://e621.net/posts.json?tags={encoded}&limit=100&page={page}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            url, headers={"User-Agent": "DiscordBot/1.0 (by anonymous)"}
        ) as resp:
            data = await resp.json(content_type=None)
    posts = data.get("posts", [])
    if not posts:
        return []
    return random.sample(posts, min(amount, len(posts)))

# ── Embed builders ────────────────────────────────────────────────────────────

def build_r34_embed(post: dict, tags: str):
    file_url = post.get("file_url", "")
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
    if not file_url:
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
        await interaction.followup.send(
            f"no results for tags: `{tags}`. try different tags."
        )
        return

    embeds, video_links = [], []
    for post in posts:
        embed, video = builder_fn(post, tags)
        if embed:
            embeds.append(embed)
        if video:
            video_links.append(video)

    if not embeds and not video_links:
        await interaction.followup.send("could not build any embeds for those posts.")
        return

    # Send in chunks of 10 (Discord limit)
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
@app_commands.describe(tags='space-separated tags, e.g. "catgirl anime"', amount="number of posts 1–10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def r34(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    await interaction.response.defer()
    try:
        posts = await fetch_r34(tags, amount)
        await send_results(interaction, posts, build_r34_embed, tags)
    except Exception as e:
        print(f"r34 error: {e}")
        await interaction.followup.send("an error occurred. Please try again.")

@bot.tree.command(name="e621", description="fetch random posts from e621")
@app_commands.describe(tags='space-separated tags, e.g. "dragon solo"', amount="number of posts 1–10 (default: 1)")
@app_commands.checks.cooldown(1, 3)
async def e621(interaction: discord.Interaction, tags: str, amount: app_commands.Range[int, 1, 10] = 1):
    await interaction.response.defer()
    try:
        posts = await fetch_e621(tags, amount)
        await send_results(interaction, posts, build_e621_embed, tags)
    except Exception as e:
        print(f"e621 error: {e}")
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
    print(f"🌐 Keep-alive server started on port {PORT}")
    bot.run(TOKEN)
