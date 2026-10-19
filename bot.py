
import asyncio
import os

import discord
import yt_dlp
from discord import app_commands

# Read the bot token from the environment (set by env.sh via start.sh).
# Raises KeyError at startup if DISCORD_TOKEN is missing, which is intended:
# failing early is clearer than a login error later.
TOKEN = os.environ["DISCORD_TOKEN"]

# Voice audio must be encoded with Opus. discord.py normally finds libopus
# automatically, but that lookup fails on Termux, so load it by explicit path.
# $PREFIX is Termux's install root (/data/data/com.termux/files/usr).
if not discord.opus.is_loaded():
    prefix = os.environ.get("PREFIX", "/data/data/com.termux/files/usr")
    for name in (os.path.join(prefix, "lib", "libopus.so"), "libopus.so", "opus"):
        try:
            discord.opus.load_opus(name)
            break  # stop at the first name that loads
        except OSError:
            continue  # this path failed, try the next one

# Options passed to yt-dlp (the YouTube extractor).
YDL_OPTS = {
    # Pick the best audio-only stream; fall back to the best overall format.
    "format": "bestaudio/best",
    # If given a playlist link, resolve only the single video, not the whole list.
    "noplaylist": True,
    # Logged-in cookies from a throwaway account. Without them YouTube often
    # answers "Sign in to confirm you're not a bot". Re-export when they expire.
    "cookiefile": os.path.expanduser("~/musicbot/cookies.txt"),
    # Suppress yt-dlp's progress and info output (errors are still raised).
    "quiet": True,
    # Anything that isn't a URL is treated as a YouTube search; the first result is used.
    "default_search": "ytsearch",
    # Recent yt-dlp must run a JavaScript runtime to solve YouTube's challenges.
    # Only deno is enabled by default, so enable Node explicitly (installed via pkg).
    "js_runtimes": {"node": {}},
}

# Options passed to ffmpeg when it opens the stream URL.
FFMPEG_OPTS = {
    # Reconnect automatically if the connection drops mid-song, instead of
    # ending the song early. Waits up to 5 seconds between attempts.
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    # -vn drops any video track; only audio is needed.
    "options": "-vn",
}

# Per-server queues, kept in memory: {guild_id: [(title, stream_url), ...]}.
# Lost on restart. Stream URLs expire after a few hours, so songs that wait
# in a very long queue may fail to play when their turn comes.
queues: dict[int, list[tuple[str, str]]] = {}


class MusicBot(discord.Client):
    def __init__(self):
        # Default intents are enough: slash commands need no privileged
        # intents (no "message content" or "members" toggle in the portal).
        super().__init__(intents=discord.Intents.default())
        # Registry that holds the slash commands defined below.
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        # Runs once before the bot connects. Uploads the slash commands to
        # Discord (global sync). New or changed commands can take up to a
        # minute to appear in the client; Ctrl+R in Discord forces a refresh.
        await self.tree.sync()


client = MusicBot()
tree = client.tree  # shorthand used by the @tree.command decorators below


def extract(query: str) -> tuple[str, str]:
    """Resolve a URL or search text to (title, direct audio stream URL).

    This is a blocking network call that can take several seconds, so it must
    never run on the event loop directly (see run_in_executor in /play).
    """
    with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
        # download=False: fetch metadata and the stream URL, don't save a file.
        info = ydl.extract_info(query, download=False)
        # A search returns a playlist-like object with an "entries" list.
        # Take the first result. A direct video URL has no "entries".
        if "entries" in info:
            info = info["entries"][0]
        return info["title"], info["url"]


def play_next(guild: discord.Guild, vc: discord.VoiceClient):
    """Start the next queued song, if any.

    Chaining: vc.play() calls the `after` callback when the song finishes,
    is skipped, or errors, and that callback calls play_next() again. This is
    what makes the queue advance without any loop. The callback runs on
    discord.py's audio thread, not the event loop.
    """
    q = queues.get(guild.id, [])
    # Stop if the queue is empty or the bot was disconnected (e.g. after /stop).
    if not q or not vc.is_connected():
        return
    title, stream = q.pop(0)  # take the oldest song
    # Launches an ffmpeg subprocess that reads the stream and outputs raw PCM
    # audio, which discord.py encodes to Opus and sends to Discord.
    source = discord.FFmpegPCMAudio(stream, **FFMPEG_OPTS)
    # `err` is ignored on purpose: if ffmpeg fails, the bot silently moves on
    # to the next song instead of getting stuck.
    vc.play(source, after=lambda err: play_next(guild, vc))


@tree.command(name="play", description="Play a YouTube URL or search YouTube")
async def play(inter: discord.Interaction, query: str):
    # The user must be in a voice channel so the bot knows where to join.
    # ephemeral=True makes the error visible only to that user.
    if not inter.user.voice or not inter.user.voice.channel:
        return await inter.response.send_message("Join a voice channel first.", ephemeral=True)

    # Discord requires a reply within 3 seconds, but yt-dlp can take longer.
    # defer() sends "thinking..." now and allows a follow-up for up to 15 minutes.
    await inter.response.defer()

    # Run the blocking extract() in a worker thread so the bot keeps handling
    # other events (heartbeats, other servers' commands) while it works.
    loop = asyncio.get_running_loop()
    try:
        title, stream = await loop.run_in_executor(None, extract, query)
    except Exception as e:
        # Common causes: "Sign in to confirm you're not a bot" (cookies),
        # HTTP 429 (rate limit), or a private/unavailable video.
        # Truncated to 300 chars because yt-dlp errors can be very long.
        return await inter.followup.send(f"Couldn't get that: {str(e)[:300]}")

    # Reuse the existing voice connection in this server, or join the
    # caller's channel if the bot isn't connected yet.
    vc = inter.guild.voice_client or await inter.user.voice.channel.connect()

    # Add to this server's queue (creating the list on first use).
    queues.setdefault(inter.guild.id, []).append((title, stream))

    if vc.is_playing() or vc.is_paused():
        # Something is already playing; the song waits its turn.
        await inter.followup.send(f"Queued: **{title}**")
    else:
        # Idle: start playback now. The song is already in the queue,
        # so play_next() pops it right back out.
        play_next(inter.guild, vc)
        await inter.followup.send(f"Now playing: **{title}**")


@tree.command(name="skip", description="Skip the current song")
async def skip(inter: discord.Interaction):
    vc = inter.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        # Stopping ends the current source, which fires the `after` callback
        # in play_next(), which starts the next song (or does nothing if empty).
        vc.stop()
        await inter.response.send_message("Skipped.")
    else:
        await inter.response.send_message("Nothing is playing.", ephemeral=True)


@tree.command(name="pause", description="Pause playback")
async def pause(inter: discord.Interaction):
    vc = inter.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()  # holds the stream in place; /resume continues from here
        await inter.response.send_message("Paused.")
    else:
        await inter.response.send_message("Nothing is playing.", ephemeral=True)


@tree.command(name="resume", description="Resume playback")
async def resume(inter: discord.Interaction):
    vc = inter.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await inter.response.send_message("Resumed.")
    else:
        await inter.response.send_message("Nothing is paused.", ephemeral=True)


@tree.command(name="queue", description="Show the queue")
async def show_queue(inter: discord.Interaction):
    q = queues.get(inter.guild.id, [])
    if not q:
        return await inter.response.send_message("The queue is empty.")
    # Shows up to 10 upcoming songs (the one playing now is not in the list,
    # because play_next() removes a song from the queue when it starts).
    lines = [f"{i + 1}. {t}" for i, (t, _) in enumerate(q[:10])]
    more = f"\n...and {len(q) - 10} more" if len(q) > 10 else ""
    await inter.response.send_message("\n".join(lines) + more)


@tree.command(name="stop", description="Stop, clear the queue and leave")
async def stop(inter: discord.Interaction):
    # Clear the queue first, so the `after` callback fired by the disconnect
    # finds nothing to play next.
    queues[inter.guild.id] = []
    vc = inter.guild.voice_client
    if vc:
        await vc.disconnect()  # also stops the current song
    await inter.response.send_message("Stopped.")


# Logs in and blocks until the process is killed. discord.py handles
# reconnects automatically. Log output goes to stderr, which start.sh
# redirects into ~/musicbot/bot.log.
client.run(TOKEN)
