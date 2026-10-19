<div class="image-container" align="center">
  <img width="256" height="256" alt="ytbeats" src="https://github.com/user-attachments/assets/742345e9-af49-4293-8504-aca7f9ad87dd" />
</div>

# YTBeats: Phone-Hosted Discord Audio Bot

A Discord audio bot that runs entirely on an Android phone inside [Termux](https://termux.dev) and is managed from a Linux laptop over USB. It plays audio from YouTube links or plain search text, with a per-server queue.

Built as a low-cost, no-VPS hosting experiment: an old phone as the server.

## Features

- Slash commands: `/play`, `/skip`, `/queue`, `/pause`, `/resume`, `/stop`
- Accepts YouTube URLs **or** plain search text
- Per-server queue that advances automatically
- Runs 24/7 on a phone: no cloud server, no monthly cost
- Managed over SSH tunneled through USB (no Wi-Fi address needed)

## How it works

```
Laptop                               Phone (Termux)
  ssh / scp --- adb forward 8022 --->  sshd
                   (USB cable)         bot.py (discord.py)
                                         |-- yt-dlp  -> YouTube (audio stream URL)
                                         |-- ffmpeg  -> decodes the stream
                                         '-- libopus + PyNaCl + davey -> Discord voice
```

| Component | Role |
|---|---|
| `discord.py` | Discord API, slash commands, voice |
| `yt-dlp` | Resolves a URL or search to a direct audio stream |
| `ffmpeg` | Streams and decodes audio |
| `PyNaCl`, `libopus`, `davey` | Voice encryption and codec (`davey` is required by discord.py 2.7+) |
| `nodejs` | JavaScript runtime that yt-dlp needs for YouTube |
| `tmux`, `sshd` | Session management and remote access |

## Requirements

- Android phone with USB debugging and [Termux from F-Droid](https://f-droid.org/packages/com.termux/) (not the Play Store build)
- Linux laptop with `adb`, `scrcpy`, `ssh`, `scp`
- A Discord bot token ([Developer Portal](https://discord.com/developers/applications))
- A **spare** Google account for YouTube cookies (never your main account)

## Quick start


**1. Prepare the phone.** Enable USB debugging (and, on Xiaomi, *USB debugging (Security settings)* plus a reboot). Set Termux to **No restrictions** under battery settings.

**2. Start SSH in Termux** (type it in the phone, or mirror it with `scrcpy`):

```bash
pkg update -y && pkg install -y openssh
passwd
sshd
whoami
```

**3. Connect from the laptop:**

```bash
adb forward tcp:8022 tcp:8022
ssh -p 8022 <termux-user>@localhost
```

**4. Install dependencies** (in the SSH session):

```bash
pkg upgrade -y && pkg install -y python ffmpeg libsodium libopus libffi clang make binutils nodejs tmux
SODIUM_INSTALL=system pip install pynacl
pip install -U discord.py yt-dlp
```

**5. Build `davey`** (Rust; takes 10 to 20 minutes, so keep the phone plugged in):

```bash
pkg install -y rust
export ANDROID_API_LEVEL=$(getprop ro.build.version.sdk)
pip install maturin
pip install -U --no-build-isolation davey
python -c "import discord, nacl, yt_dlp, davey; print('ok')"
```

**6. Save the token and create the start script:**

```bash
mkdir -p ~/musicbot
read -rs -p "Paste token: " T; echo; echo "export DISCORD_TOKEN='$T'" > ~/musicbot/env.sh; chmod 600 ~/musicbot/env.sh; unset T

cat > ~/musicbot/start.sh <<'EOF'
#!/data/data/com.termux/files/usr/bin/bash
termux-wake-lock
source ~/musicbot/env.sh
cd ~/musicbot
exec python bot.py
EOF
chmod +x ~/musicbot/start.sh
```

**7. Add YouTube cookies** (from a throwaway account, exported in a private window that you then close):

```bash
scp -P 8022 cookies.txt <termux-user>@localhost:musicbot/
ssh -p 8022 <termux-user>@localhost "chmod 600 ~/musicbot/cookies.txt"
```

**8. Deploy and run** (from the laptop, in this repo's folder):

```bash
scp -P 8022 bot.py <termux-user>@localhost:musicbot/
ssh -p 8022 <termux-user>@localhost "setsid nohup ~/musicbot/start.sh > ~/musicbot/bot.log 2>&1 < /dev/null &"
ssh -p 8022 <termux-user>@localhost "tail -5 ~/musicbot/bot.log"
```

The log should show `connected to Gateway`. Join a voice channel and run `/play <song or link>`.

## Commands

| Command | Description |
|---|---|
| `/play <query>` | Play a YouTube URL or search text; queues if something is playing |
| `/skip` | Skip to the next song |
| `/queue` | Show the next 10 queued songs |
| `/pause`, `/resume` | Pause or resume playback |
| `/stop` | Clear the queue and leave the channel |

## Operation

| Task | Command (over SSH) |
|---|---|
| Logs | `tail -30 ~/musicbot/bot.log` |
| Stop | `pkill -f '[b]ot.py'` |
| Restart | `pkill -f '[b]ot.py'; sleep 2; setsid nohup ~/musicbot/start.sh > ~/musicbot/bot.log 2>&1 < /dev/null &` |
| Fix broken playback | `pip install -U yt-dlp`, then restart |
| Expired cookies | Re-export `cookies.txt` and `scp` it over |
| After a phone reboot | Open Termux, run `sshd`, start the bot |

## Common problems

| Symptom | Fix |
|---|---|
| scrcpy ignores clicks | Enable *USB debugging (Security settings)* on Xiaomi and reboot |
| `Connection refused` on 8022 | Rerun `adb forward tcp:8022 tcp:8022` |
| `Connection closed by 127.0.0.1` | `sshd` is down; run `sshd` in Termux |
| `davey library needed in order to use voice` | Build `davey` (step 5) |
| `Sign in to confirm you're not a bot` | Add cookies (step 7) and make sure Node is installed |
| `HTTP Error 429` | YouTube is rate-limiting your IP; stop retrying and wait |
| `/play` says "application did not respond" | The bot isn't running; check the log |

## Limitations

- Availability depends on the phone: keep it plugged in, on Wi-Fi, with Termux unrestricted. A reboot needs a manual `sshd` and bot start.
- The queue is in memory and is lost on restart. Stream URLs expire after a few hours, so very long queues can fail late.
- Playlists are not supported (one song per command), and the bot does not auto-leave when idle.
- YouTube changes often; keep `yt-dlp` up to date.

## Security

- Never commit `env.sh`, `cookies.txt`, or `bot.log`. See `.gitignore`.
- Use a spare Google account for cookies; automated use can get an account flagged.
- If the token leaks, reset it immediately in the Developer Portal.

## Disclaimer

Streaming YouTube audio through a bot violates YouTube's Terms of Service. This project is intended for personal, educational use on a small private server. You are responsible for how you use it.

