# Uptime Monitor with Telegram alerts

A small Python script that checks your websites and servers and sends a Telegram message when one goes **down** or comes **back up**. It does not spam you: you get a message only when the status changes.

- Pure Python standard library, nothing to install
- Checks HTTP(S) pages and TCP ports (SSH, databases, game servers)
- Keeps a full history in a local SQLite database
- Secrets (bot token) live in environment variables, never in the code

## Quick start

1. Create a bot with @BotFather in Telegram and copy the token.
2. Send any message to your bot, then open https://api.telegram.org/bot<TOKEN>/getUpdates to find your chat id.
3. Copy the example config and edit it:

```bash
cp config.example.json config.json
```

4. Set the credentials and run:

```bash
export TELEGRAM_BOT_TOKEN="123456:ABC..."
export TELEGRAM_CHAT_ID="123456789"
python3 monitor.py config.json
```

Run one check and exit (good for cron):

```bash
python3 monitor.py config.json --once
```

If the Telegram variables are not set, alerts are printed to the console, which is handy for testing.

## Config

```json
{
  "interval": 60,
  "timeout": 10,
  "targets": [
    { "name": "My website", "url": "https://example.com" },
    { "name": "SSH server", "type": "tcp", "host": "example.com", "port": 22 }
  ]
}
```

| Field | Meaning |
|---|---|
| interval | Seconds between checks (default 60) |
| timeout | Seconds to wait before a target counts as down (default 10) |
| type | http (default) or tcp |

## When do I get a message?

- A target goes from up to down: DOWN
- A target comes back: RECOVERED
- Nothing changes: no message

## Check history

Every check is saved in monitor.db:

```bash
sqlite3 monitor.db "SELECT name, is_up, response_ms, checked_at FROM checks ORDER BY id DESC LIMIT 10;"
```

## Run it in the background on Linux

Example systemd service (/etc/systemd/system/uptime-monitor.service):

```ini
[Unit]
Description=Uptime Monitor

[Service]
WorkingDirectory=/opt/uptime-monitor
Environment=TELEGRAM_BOT_TOKEN=your-token
Environment=TELEGRAM_CHAT_ID=your-chat-id
ExecStart=/usr/bin/python3 monitor.py config.json
Restart=always

[Install]
WantedBy=multi-user.target
```

## License

MIT
