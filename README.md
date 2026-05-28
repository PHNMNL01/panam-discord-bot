# Discord AI Bot PoC

Jednoduchy Discord AI bot v Pythonu.

## Cil prvni verze

- bot se pripoji na Discord server
- ma slash command `/ask`
- dotaz posle do OpenAI API
- odpoved vrati zpet do Discord kanalu
- tokeny a API klice jsou pouze v `.env`

## Konfigurace

Vytvor lokalni `.env` podle `.env.example`:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
DISCORD_GUILD_IDS=
ALLOWED_CHANNEL_IDS=
OPENAI_MODEL=gpt-4.1-mini
```

- `DISCORD_GUILD_IDS`: volitelny seznam ID Discord serveru oddelenych carkou. Kdyz je prazdny, slash commandy se synchronizuji globalne.
- `ALLOWED_CHANNEL_IDS`: volitelny seznam ID kanalu oddelenych carkou. Kdyz je prazdny, `/ask` muze odpovidat ve vsech kanalech.
- `.env` nikdy nedavej do gitu ani do chatu. Obsahuje tokeny a API klice.

## Spusteni na Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

## Struktura

```text
Discord_AI_Bot/
|-- bot.py
|-- requirements.txt
|-- .env
|-- .env.example
|-- .gitignore
`-- README.md
```
