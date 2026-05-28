# PANAM Discord AI Bot PoC

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

## Panam commandy

### Slash commandy

- `/ask` – položí otázku AI.
- `/summary` – shrne vložený text.
- `/channel_summary` – shrne poslední zprávy aktuálního kanálu.
- `/search_messages` – vyhledá text v posledních zprávách aktuálního kanálu.
- `/note_add` – uloží poznámku.
- `/note_list` – zobrazí poslední poznámky.
- `/note_search` – vyhledá v poznámkách.
- `/todo_add` – přidá úkol.
- `/todo_list` – zobrazí aktivní úkoly.
- `/todo_done` – označí úkol jako hotový.
- `/ping` – ověří, že je bot online.
- `/help` – zobrazí nápovědu.

### Přirozené ovládání Panam

Panam reaguje jen v povolených kanálech a jen když je oslovená.

#### Poznámky

- `Panam přidej poznámku <text>`
- `Panam ulož poznámku <text>`
- `Panam zapamatuj si <text>`
- `Panam pamatuj si <text>`
- `Panam ulož si <text>`
- `Panam zapamatuj si to` – uloží předchozí vhodnou zprávu jako poznámku.
- `Panam ukaž poznámky`
- `Panam najdi poznámku <text>`

#### Todo

- `Panam přidej todo <text>`
- `Panam přidej úkol <text>`
- `Panam ukaž todo`
- `Panam ukaž úkoly`

#### AI otázky

- `Panam řekni mi <dotaz>`
- `Panam rekni mi <dotaz>`
- `Panam řekni <dotaz>`
- `Panam rekni <dotaz>`
- `Panam odpověz <dotaz>`
- `Panam odpovez <dotaz>`
- `Panam co si myslíš o <text>`
- `Panam co si myslis o <text>`
- `Co si myslí Panam o <text>`
- `Co si mysli Panam o <text>`
- `Panam co si o tom myslíš?` – vyjádří se k předchozí vhodné zprávě.
- `Panam co si o tom myslis?`
- `Co si o tom myslí Panam?`
- `Co si o tom mysli Panam?`

#### Shrnutí

- `Panam shrň <text>`
- `Panam shrn <text>`
- `Panam shrň mi <text>`
- `Panam shrn mi <text>`
- `Panam udělej summary <text>`
- `Panam udelej summary <text>`
- `Panam shrň toto` – shrne předchozí vhodnou zprávu.
- `Panam shrn toto`
- `Panam shrň to`
- `Panam shrn to`

### Bezpečnost

- Panam funguje jen v kanálech uvedených v `ALLOWED_CHANNEL_IDS`.
- Panam čte historii kanálu jen při explicitním commandu.
- Panam neukládá historii kanálu automaticky.
- Do Panam nezadávej hesla, tokeny, API klíče, HR data, zákaznická data ani jiné citlivé údaje.
- `.env`, `notes.json` a `todos.json` nepatří do gitu.

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
