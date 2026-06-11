# Panam

Panam je osobni AI asistentka v Pythonu. Projekt uz neni jen Discord bot: jadro Panam je sdilena aplikační vrstva, nad kterou bezi Discord adapter, lokalni Web appka a lokalni Deck pro spravu procesu.

Panam umi odpovidat pres OpenAI API, vest kratkou konverzacni pamet, pracovat s poznamkami a todo listem, shrnovat texty, analyzovat prilohy a bezpecne vytvaret nove vystupni soubory z dokumentu.

## Aktualni Stav

Projekt je rozdeleny na nekolik vrstev:

- `panam_core.py` - sdilene use-cases bez Discordu a Flasku.
- `panam_ai.py` - OpenAI volani, shrnovani, analyza a classifier.
- `panam_command_router.py` - textovy parser prikazu pro adaptery.
- Discord adapter - `bot.py` a `panam_discord_*`.
- Web adapter - `panam_web_app.py`, `panam_web_adapter.py`, `web/`.
- Deck - `panam_dock_app.py`, `panam_process_manager.py`, `web_admin/`.
- File pipeline - cteni, analyza a tvorba novych souboru.

`bot.py` je jen tenky entrypoint:

```python
from panam_discord_runner import run_discord_bot


if __name__ == "__main__":
    run_discord_bot()
```

Import Discord runneru bota nespousti. Token se kontroluje az pri `run_discord_bot()`.

## Co Panam Umi

- odpovidat na dotazy pres OpenAI API
- osobnejsi `/panam_talk` rezim
- shrnovat vlozeny text nebo posledni zpravy v kanalu
- hledat v poslednich Discord zprávach
- ukladat a hledat poznamky
- spravovat jednoduchy todo list
- analyzovat obrazky a dokumenty
- cist TXT, MD, CSV, JSON, PDF, DOCX a XLSX
- vytvaret nove MD, TXT nebo DOCX dokumenty
- tezit strukturovana data do JSON, CSV, Markdownu nebo XLSX
- bezpecne transformovat DOCX a XLSX do noveho vystupniho souboru
- reagovat na prirozene fraze typu `Panam shrn to` nebo `Panam udelej z toho tabulku`

## Konfigurace

Vytvor lokalni `.env` podle `.env.example`:

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
OPENAI_API_KEY=your_openai_api_key_here
DISCORD_GUILD_IDS=
ALLOWED_CHANNEL_IDS=
OPENAI_MODEL=gpt-5.4-mini
OPENAI_PROMPT_ID=
OPENAI_PROMPT_VERSION=
PANAM_DOCK_ADMIN_TOKEN=
PANAM_DOCK_PORT=5051
PANAM_WEB_HOST=127.0.0.1
PANAM_WEB_PORT=5050
PANAM_WEB_DEBUG=1
PANAM_WEB_USE_RELOADER=1
```

Promenne:

- `DISCORD_BOT_TOKEN` - token Discord bota. Kontroluje se az pri startu Discord adapteru.
- `OPENAI_API_KEY` - OpenAI API klic.
- `DISCORD_GUILD_IDS` - volitelny seznam guild ID oddelenych carkou. Prazdna hodnota znamena globalni sync slash commandu.
- `ALLOWED_CHANNEL_IDS` - volitelny seznam channel ID oddelenych carkou. Prazdna hodnota znamena, ze bot muze odpovidat vsude.
- `OPENAI_MODEL` - model pro OpenAI volani, fallback je `gpt-5.4-mini`.
- `OPENAI_PROMPT_ID` a `OPENAI_PROMPT_VERSION` - volitelny OpenAI Prompt Management prompt.
- `PANAM_DOCK_ADMIN_TOKEN` - volitelny token pro Deck POST akce.
- `PANAM_DOCK_PORT` - port Decku, default `5051`.
- `PANAM_WEB_HOST` - host Panam Webu, default `127.0.0.1`.
- `PANAM_WEB_PORT` - port Panam Webu, default `5050`.
- `PANAM_WEB_DEBUG` - Flask debug pro rucni spusteni webu, default `1`.
- `PANAM_WEB_USE_RELOADER` - Flask reloader pro rucni spusteni webu, default `1`.

`.env` nikdy nedavej do gitu ani do chatu.

## Instalace

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Spusteni

Discord adapter:

```powershell
python bot.py
```

Panam Web:

```powershell
python panam_web_app.py
```

Web bezi defaultne na `http://127.0.0.1:5050`.

Panam Deck:

```powershell
python panam_dock_app.py
```

Deck bezi defaultne na `http://127.0.0.1:5051`.

## Panam Web

`panam_web_app.py` je lokalni Flask appka pro webovy chat s Panam. Pouziva:

- `web/templates/index.html`
- `web/static/panam.css`
- `web/static/panam_avatar.png`
- `panam_web_adapter.py`

Routy:

- `GET /` - webove UI.
- `GET /health` - health check, vraci `{"status":"ok","service":"panam-web"}`.
- `POST /api/chat` - posle zpravu do web adapteru.
- `POST /api/clear` - vymaze webovou konverzacni pamet.

Web adapter pouziva sdileny command router a `panam_core.py`. Web zatim pracuje s internim web user/channel ID a nepouziva Discord.

Pri rucnim spusteni muze byt Flask debug a reloader zapnuty. Kdyz Web spousti Deck, nastavuje se `PANAM_STARTED_BY_DOCK=1`, `PANAM_WEB_DEBUG=0` a `PANAM_WEB_USE_RELOADER=0`, aby se na Windows nededily Werkzeug reloader socket/env hodnoty.

## Panam Deck

`panam_dock_app.py` je lokalni control panel pro spravu procesu Panam. Je oddeleny od Panam Webu, aby z nej slo zapnout a vypnout i samotny Web.

Deck:

- bezi pouze na `127.0.0.1`
- default port je `5051`
- neni urceny pro verejne vystaveni
- umi spravovat jen allowlistovane sluzby `bot` a `web`
- ma samostatne stranky `Dashboard`, `Smoke Tests` a `Logs`
- neumi spoustet libovolne prikazy
- neimportuje Discord

Povolene sluzby:

- `bot` -> `bot.py`
- `web` -> `panam_web_app.py`

Admin token:

- pokud `PANAM_DOCK_ADMIN_TOKEN` existuje, POST akce musi poslat header `X-Panam-Dock-Token`
- pokud token neexistuje, POST akce jsou povolene jen pro lokalni requesty z `127.0.0.1`
- token se neloguje

Procesy spravuje `panam_process_manager.py`:

- startuje procesy pres aktualni Python interpreter
- pouziva `subprocess.Popen`
- uklada PID soubory do `runtime/pids/`
- zapisuje process logy do `logs/panam_bot_process.log` a `logs/panam_web_process.log`
- pro Panam Web overuje stav pres `http://127.0.0.1:5050/health`, ne jen podle PID
- pri startu child procesu odstranuje Werkzeug/Flask reloader env promenne `WERKZEUG_RUN_MAIN`, `WERKZEUG_SERVER_FD`, `FLASK_RUN_FROM_CLI`, `WERKZEUG_DEBUG_PIN`
- na Windows pri stopu pouziva `taskkill /PID <pid> /T /F`

UI rozlisuje `RUNNING`, `STOPPED` a `ERROR`. Pokud proces existuje, ale web health check selze, Deck to ukaze jako error/detail misto falesneho running stavu.

Dashboard `/` spravuje sluzby a jejich stav.

Stranka `/tests` spousti smoke testy pro lokalni vyvoj a rychlou kontrolu po pullu nebo commitu. Testy jsou pevne allowlistovane v `panam_test_runner.py`, nejde spustit libovolny prikaz. Vysledky ukazuji `PASS`/`FAIL`, duration, return code, stdout a stderr. Deck uklada poslednich 50 zaznamu test historie do `runtime/dock/test_history.json`.

Allowlist V1:

- `web_router` -> `panam_web_smoke_test.py`
- `web_flask` -> `panam_web_flask_smoke_test.py`
- `dock` -> `panam_dock_smoke_test.py`
- `router` -> `scripts/router_smoke_test.py`
- `docx` -> `scripts/docx_smoke_test.py`
- `docx_transform` -> `scripts/docx_transform_smoke_test.py`
- `spreadsheet_transform` -> `scripts/spreadsheet_transform_smoke_test.py`

Stranka `/logs` cte pouze allowlistovane logy pres `panam_log_reader.py`. Log viewer nikdy nebere cestu z requestu, pouze nazvy z allowlistu:

- `main` -> `logs/panam.log`
- `bot_process` -> `logs/panam_bot_process.log`
- `web_process` -> `logs/panam_web_process.log`
- `dock_process` -> `logs/panam_dock_process.log`

## Discord Adapter

Discord cast zustava plnohodnotny adapter nad sdilenymi moduly. Slash commandy a natural zpravy deleguji do core/file pipeline a Discord helperu.

Zakladni slash commandy:

- `/ping`
- `/help`
- `/memory_clear`
- `/ask`
- `/panam_talk`
- `/summary`
- `/channel_summary`
- `/search_messages`
- `/note_add`
- `/note_list`
- `/note_search`
- `/todo_add`
- `/todo_list`
- `/todo_done`
- `/analyze`
- `/read_file`
- `/process_file`
- `/extract_data`
- `/transform_docx`
- `/transform_excel`
- `/file_job_test`
- `/voice_join`
- `/voice_say`
- `/voice_leave`
- `/ask_voice`

### Panam Voice Speak v1

Voice v1 je pouze Discord output kanal. Panam se umi pripojit do voice kanalu,
prehrat kratky TTS text a odpojit se. Nepridava nahravani, STT, wake phrase ani
listen command.

Manual smoke test:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
ffmpeg -version
python bot.py
```

V Discordu:

1. Pripoj se do voice kanalu.
2. Spust `/voice_join`.
3. Spust `/voice_say text:Ahoj, jsem Panam`.
4. Spust `/ask_voice Jak se jmenujes a co umis?`.
5. Over, ze odpoved prijde textem do Discord chatu i hlasem ve voice.
6. Spust `/voice_leave`.

Definition of Done:

- bot se pripoji do tveho aktualniho voice kanalu
- `/voice_say ahoj` slysitelne prehraje audio
- `/ask_voice Jak se jmenujes a co umis?` odpovi textem i hlasem
- `/voice_leave` bota odpoji
- existujici textove prikazy porad funguji
- `/voice_join`, `/voice_say` a `/voice_leave` porad funguji
- zadne poslouchani nebylo pridane
- v logu nejsou tokeny, raw TTS texty ani citlivy obsah

Natural zpravy funguji, kdyz je Panam oslovena, napr.:

```text
Panam ahoj
Panam shrn to
Panam co si o tom myslis?
Panam pridej poznamku zavolat ucetni
Panam ukaz todo
Panam udelej z toho tabulku
Panam dej mi to do Excelu
Panam udelej z toho DOCX
Panam oprav ten Word dokument
Panam odstran prazdne radky z te tabulky
```

## File Pipeline

File-job pipeline je v `panam_discord_file_jobs.py` a `panam_files.py`.

Kazdy job bezi v docasne slozce:

```text
runtime/jobs/<job_id>/
|-- input/
|-- work/
|-- output/
`-- job.json
```

Pipeline:

1. ulozi docasnou kopii prilohy do `input/`
2. extrahuje text do `work/`
3. zavola AI nebo deterministickou transformaci
4. vytvori novy vystup v `output/`
5. odesle vysledek adapterem
6. zapise metadata do `job.json`
7. po dokonceni smaze job folder

Puvodni priloha se nikdy neupravuje. Vystupy vznikaji jako nove soubory.

Podporovane vstupy:

- TXT
- MD
- CSV
- JSON
- PDF
- DOCX
- XLSX
- PNG
- JPG / JPEG
- WEBP
- GIF

Limit velikosti prilohy je 20 MB.

## Modulova Mapa

Core a AI:

- `panam_core.py` - sdilene use-cases.
- `panam_ai.py` - OpenAI volani.
- `panam_memory.py` - kratka konverzacni pamet.
- `panam_notes.py` - poznamky.
- `panam_todos.py` - todo list.
- `panam_command_router.py` - textovy command parser.
- `panam_phrases.py` - fraze, regexy a signaly.

Discord:

- `bot.py` - entrypoint.
- `panam_discord_runner.py` - Discord client, slash command dekoratory a eventy.
- `panam_discord_voice.py` - Discord voice speak v1 output adapter.
- `panam_discord_*` - command adaptery, helpery, natural flow a file-job obsluha.

Web:

- `panam_web_app.py` - Flask Web app.
- `panam_web_adapter.py` - Web adapter do core.
- `web/templates/index.html` - Web UI.
- `web/static/` - Web CSS a assety.

Deck:

- `panam_dock_app.py` - Flask Deck app.
- `panam_process_manager.py` - allowlist subprocess manager.
- `panam_test_runner.py` - allowlist smoke test runner.
- `panam_log_reader.py` - allowlist log viewer helper.
- `web_admin/templates/dock.html` - Deck UI.
- `web_admin/templates/tests.html` - Smoke Tests UI.
- `web_admin/templates/logs.html` - Logs UI.
- `web_admin/static/dock.css` - Deck styl.

Soubory:

- `panam_files.py` - job slozky a metadata.
- `panam_text_extraction.py` - extrakce textu.
- `panam_file_context.py` - last file context a summary.
- `panam_file_responses.py` - texty pro file odpovedi.
- `panam_docx.py` - tvorba DOCX.
- `panam_docx_transform.py` - DOCX transformace.
- `panam_excel.py` - tvorba XLSX.
- `panam_spreadsheet.py` - XLSX transformace.
- `panam_router.py` - file routing rozhodovani.

## Testy

Zakladni smoke testy:

```powershell
python -m py_compile bot.py panam_core.py panam_web_app.py panam_dock_app.py panam_process_manager.py
python panam_web_smoke_test.py
python panam_web_flask_smoke_test.py
python panam_log_reader_smoke_test.py
python panam_test_runner_smoke_test.py
python panam_dock_smoke_test.py
python scripts/router_smoke_test.py
python scripts/discord_runner_smoke_test.py
python scripts/discord_basic_commands_smoke_test.py
python scripts/discord_message_router_smoke_test.py
```

Deck smoke test nestartuje realny `bot.py` ani `panam_web_app.py`. Kontroluje importy, `/health`, `/api/status`, `/api/tests`, stavova pole sluzeb a sanitizaci child env pro Panam Web.

`panam_test_runner_smoke_test.py` kontroluje allowlist, sanitizaci test env a muze spustit kratky router smoke test, pokud existuje.

Docx/XLSX testy v tomto workspace typicky pouzivaji `.venv`, protoze systemovy Python nemusi mit `docx`:

```powershell
.\.venv\Scripts\python.exe scripts\text_extraction_smoke_test.py
.\.venv\Scripts\python.exe scripts\discord_file_jobs_smoke_test.py
.\.venv\Scripts\python.exe scripts\docx_transform_smoke_test.py
.\.venv\Scripts\python.exe scripts\spreadsheet_transform_smoke_test.py
```

## Bezpecnost

- `.env` nikdy necommituj.
- Tokeny a citlive hodnoty se neloguji.
- Deck nesmi byt vystaven verejne.
- Deck umi spoustet jen pevne povolene sluzby `bot` a `web`.
- Puvodni prilohy se nikdy neupravuji.
- Vystupni soubory vznikaji jako nove soubory.
- Obsah priloh, vystupu a cele dotazy se nema logovat.
- `ALLOWED_CHANNEL_IDS` muze omezit Discord kanaly, kde bot odpovida.

## Lokalni Data

Do gitu patri zdrojove moduly, dokumentace, testy, `.env.example`, `requirements.txt` a `docs/`.

Do gitu nepatri:

- `.env`
- `.venv/`
- `notes.json`
- `todos.json`
- `logs/`
- `runtime/`
- `__pycache__/`
- `*.pyc`
