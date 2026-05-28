# Discord AI Bot PoC

Jednoduchy Discord AI bot v Pythonu.

Panam je osobni Discord AI asistentka pro zabavu, poznamky, todo list, shrnuti textu, praci se zpravami v kanalu a analyzu obrazku.

## Cil prvni verze

- bot se pripoji na Discord server
- ma slash command `/ask`
- dotaz posle do OpenAI API
- odpoved vrati zpet do Discord kanalu
- tokeny a API klice jsou pouze v `.env`
- bot umi pracovat jen v povolenych kanalech, pokud jsou nastavene
- OpenAI logika je oddelena do `panam_ai.py`

## Konfigurace

Vytvor lokalni `.env` podle `.env.example`:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
DISCORD_GUILD_IDS=
ALLOWED_CHANNEL_IDS=
OPENAI_MODEL=gpt-5-mini
```

- `DISCORD_GUILD_IDS`: volitelny seznam ID Discord serveru oddelenych carkou. Kdyz je prazdny, slash commandy se synchronizuji globalne.
- `ALLOWED_CHANNEL_IDS`: volitelny seznam ID kanalu oddelenych carkou. Kdyz je prazdny, bot muze odpovidat ve vsech kanalech.
- `OPENAI_MODEL`: model pouzivany pro odpovedi pres OpenAI API.
- `.env` nikdy nedavej do gitu ani do chatu. Obsahuje tokeny a API klice.

## Panam commandy

### Slash commandy

- `/ask` - polozi otazku AI.
- `/summary` - shrne vlozeny text.
- `/channel_summary` - shrne posledni zpravy aktualniho kanalu.
- `/search_messages` - vyhleda text v poslednich zpravach aktualniho kanalu.
- `/note_add` - ulozi poznamku.
- `/note_list` - zobrazi posledni poznamky.
- `/note_search` - vyhleda v poznamkach.
- `/todo_add` - prida ukol.
- `/todo_list` - zobrazi aktivni ukoly.
- `/todo_done` - oznaci ukol jako hotovy.
- `/analyze` - analyzuje prilozeny obrazek nebo dokument, pripadne podporovanou prilohu z predchozi zpravy.
- `/read_file` - precte TXT, MD, CSV, PDF, DOCX nebo XLSX prilohu a odpovi na otazku k dokumentu.
- `/ping` - overi, ze je bot online.
- `/help` - zobrazi napovedu.

## Analyze priloh

Command `/analyze` slouzi k analyze obrazku a dokumentu.

Umi:

- analyzovat obrazek prilozeny primo v commandu
- analyzovat dokument prilozeny primo v commandu
- pokud soubor neni prilozeny primo v commandu, pokusi se najit podporovanou prilohu v predchozi vhodne zprave v aktualnim kanalu
- odpovedet na otazku k obrazku nebo dokumentu
- popsat screenshot, meme, fotku nebo vizualni obsah

Podporovane formaty:

- PNG
- JPG
- JPEG
- WEBP
- GIF
- TXT
- MD
- CSV
- PDF
- DOCX
- XLSX

Limit velikosti prilohy:

- maximalne 20 MB

Priklad s obrazkem primo v commandu:

```text
/analyze file: screenshot.png question: Co je tady za chybu?
```

Priklad s obrazkem ve zprave nad commandem:

```text
/analyze question: Co je na obrazku?
```

PDF musi obsahovat extrahovatelny text. Skenovane PDF nebo obrazkove PDF bez OCR zatim nemusi fungovat.

XLSX cte hodnoty bunek z listu, ne makra ani slozite formatovani.

## Čtení dokumentů

Command `/read_file` slouzi ke cteni zakladnich dokumentovych priloh.

Podporovane formaty:

- TXT
- MD
- CSV
- PDF
- DOCX
- XLSX

Limit velikosti dokumentu:

- maximalne 20 MB

PDF musi obsahovat extrahovatelny text. Skenovane PDF nebo obrazkove PDF bez OCR zatim nemusi fungovat.

DOCX cte bezny text a tabulky.

XLSX cte hodnoty z listu, ne makra ani slozite formatovani.

## Prirozene ovladani Panam

Panam reaguje jen v povolenych kanalech a jen kdyz je oslovena.

### Poznamky

```text
Panam pridej poznamku <text>
Panam uloz poznamku <text>
Panam zapamatuj si <text>
Panam pamatuj si <text>
Panam uloz si <text>
Panam zapamatuj si to
Panam ukaz poznamky
Panam najdi poznamku <text>
```

`Panam zapamatuj si to` ulozi predchozi vhodnou zpravu jako poznamku.

### Todo

```text
Panam pridej todo <text>
Panam pridej ukol <text>
Panam ukaz todo
Panam ukaz ukoly
```

### AI otazky

```text
Panam rekni mi <dotaz>
Panam řekni mi <dotaz>
Panam rekni <dotaz>
Panam řekni <dotaz>
Panam odpovez <dotaz>
Panam odpověz <dotaz>
Panam co si myslis o <text>
Panam co si myslíš o <text>
Co si mysli Panam o <text>
Co si myslí Panam o <text>
Panam co si o tom myslis?
Panam co si o tom myslíš?
Co si o tom mysli Panam?
Co si o tom myslí Panam?
```

Dotazy typu `Panam co si o tom myslis?` pouziji predchozi vhodnou zpravu jako kontext.

### Shrnuti

```text
Panam shrn <text>
Panam shrň <text>
Panam shrn mi <text>
Panam shrň mi <text>
Panam udelej summary <text>
Panam udělej summary <text>
Panam shrn toto
Panam shrň toto
Panam shrn to
Panam shrň to
```

Dotazy typu `Panam shrn toto` nebo `Panam shrn to` pouziji predchozi vhodnou zpravu jako kontext.

### Analyze pres prirozenou frazi

Pokud je tato funkce v botovi zapnuta, Panam umi reagovat i na prirozene fraze pro analyzu obrazku a podporovanych priloh.

Priklady:

```text
Panam analyzuj obrazek
Panam analyzuj obrázek
Panam analyzuj ten obrazek
Panam analyzuj ten obrázek
Panam koukni na obrazek
Panam koukni na obrázek
Panam koukni na tohle
Panam podivej se na obrazek
Panam podívej se na obrázek
Panam podivej se na tohle
Panam podívej se na tohle
Panam co je na obrazku?
Panam co je na obrázku?
Panam co je na tom obrazku?
Panam co je na tom obrázku?
Panam co vidis?
Panam co vidíš?
Panam co tam vidis?
Panam co tam vidíš?
Panam popis obrazek
Panam popiš obrázek
Panam popis ten obrazek
Panam popiš ten obrázek
Panam vysvetli obrazek
Panam vysvětli obrázek
Panam vysvetli ten screenshot
Panam vysvětli ten screenshot
Panam co je na screenshotu?
Panam co je na screenu?
Panam co je tady za chybu?
Panam co je tam za chybu?
Panam analyzuj soubor
Panam co je v souboru?
Panam co obsahuje ten soubor?
Panam precti soubor
Panam shrn ten soubor
Panam analyzuj dokument
Panam co je v dokumentu?
Panam precti PDF
Panam shrn PDF
Panam analyzuj Word
Panam co je ve Wordu?
Panam analyzuj DOCX
Panam analyzuj Excel
Panam co je v Excelu?
Panam analyzuj tabulku
Panam co je v tabulce?
Panam shrn CSV
Panam analyzuj markdown
Panam co je v priloze?
Panam analyzuj prilohu
```

Panam se pokusi pouzit podporovanou prilohu z aktualni nebo predchozi vhodne zpravy.

## Bezpecnost

- Panam funguje jen v kanalech uvedenych v `ALLOWED_CHANNEL_IDS`, pokud jsou nastavene.
- Panam cte historii kanalu jen pri explicitnim commandu nebo pri osloveni podporovanou frazi.
- Panam neuklada historii kanalu automaticky.
- Do Panam nezadavej hesla, tokeny, API klice, HR data, zakaznicka data ani jina citliva data.
- `.env`, `notes.json` a `todos.json` nepatri do gitu.

## Spusteni na Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

## Struktura

```text
panam-discord-bot/
|-- bot.py
|-- panam_ai.py
|-- requirements.txt
|-- .env
|-- .env.example
|-- .gitignore
|-- README.md
|-- notes.json
`-- todos.json
```

### Soubory

- `bot.py` - Discord cast bota, commandy, prace s kanaly a zpravami.
- `panam_ai.py` - OpenAI cast, systemovy prompt Panam, AI odpovedi, shrnuti a analyza obrazku.
- `requirements.txt` - Python zavislosti.
- `.env.example` - sablona konfigurace.
- `.env` - lokalni konfigurace s tokeny a klici. Nepatri do gitu.
- `notes.json` - lokalni poznamky. Nepatri do gitu.
- `todos.json` - lokalni todo list. Nepatri do gitu.
- `.gitignore` - pravidla pro soubory, ktere Git nema sledovat.

## Git

Do gitu patri:

- `bot.py`
- `panam_ai.py`
- `requirements.txt`
- `README.md`
- `.env.example`
- `.gitignore`

Do gitu nepatri:

- `.env`
- `.venv/`
- `notes.json`
- `todos.json`
- `__pycache__/`
- `*.pyc`
- `*.log`
