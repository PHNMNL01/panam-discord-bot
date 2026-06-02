# Panam Discord Bot

Panam je osobni Discord AI asistentka v Pythonu. Umi odpovidat na dotazy, shrnovat texty a zpravy v kanalu, vest poznamky a todo list, analyzovat prilohy a bezpecne vytvaret nove vystupni soubory z dokumentu.

Projekt je po poslednim refactoru rozdeleny na tenky entrypoint, Discord adapter vrstvu, ciste core moduly, AI vrstvu a izolovanou file-job pipeline.

## Aktualni Stav Architektury

`bot.py` je uz jen entrypoint:

```python
from panam_discord_runner import run_discord_bot


if __name__ == "__main__":
    run_discord_bot()
```

Discord runner, slash command dekoratory a `DiscordAIBot` jsou v `panam_discord_runner.py`. Import runneru bota nespousti a token se kontroluje az pri zavolani `run_discord_bot()`.


## Hlavni Principy

- Slash commandy jsou presne nastroje s validaci vstupu.
- Natural zpravy jsou router nad stejnymi nastroji, ne druha paralelni implementace.
- Puvodni Discord priloha se nikdy neupravuje.
- Vystupni soubory vznikaji vzdy jako nove soubory.
- File-job pipeline bezi v docasnem `runtime/jobs/<job_id>/`.
- Obsah priloh, vystupu, tokeny a citlive hodnoty se neloguji.
- `last_file_context`, `file_summary` a `last_router_decision` jsou jen kratka RAM pamet podle kanalu.

## Co Panam Umi

- odpovidat pres OpenAI API
- pracovat v osobnejsim `/panam_talk` rezimu
- shrnovat text nebo posledni zpravy v kanalu
- hledat v poslednich zpravach kanalu
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

- `DISCORD_BOT_TOKEN` - token Discord bota. Kontroluje se az pri `run_discord_bot()`.
- `OPENAI_API_KEY` - OpenAI API klic.
- `DISCORD_GUILD_IDS` - volitelny seznam guild ID oddelenych carkou. Kdyz je prazdny, slash commandy se synchronizuji globalne.
- `ALLOWED_CHANNEL_IDS` - volitelny seznam channel ID oddelenych carkou. Kdyz je prazdny, bot muze odpovidat vsude.
- `OPENAI_MODEL` - model pro OpenAI volani, fallback je `gpt-5.4-mini`.
- `OPENAI_PROMPT_ID` a `OPENAI_PROMPT_VERSION` - volitelny OpenAI Prompt Management prompt pro beznou osobnost Panam.
- `PANAM_DOCK_ADMIN_TOKEN` - volitelny admin token pro Panam Dock POST akce.
- `PANAM_DOCK_PORT` - port Panam Docku, default `5051`.
- `PANAM_WEB_HOST` - host Panam Webu, default `127.0.0.1`.
- `PANAM_WEB_PORT` - port Panam Webu, default `5050`.
- `PANAM_WEB_DEBUG` - zapina Flask debug pri rucnim spusteni webu, default `1`.
- `PANAM_WEB_USE_RELOADER` - zapina Flask reloader pri rucnim spusteni webu, default `1`.

`.env` nikdy nedavej do gitu ani do chatu.

## Spusteni

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

`bot.py` pouze zavola `run_discord_bot()` z `panam_discord_runner.py`.

Panam Web:

```powershell
python panam_web_app.py
```

Web bezi defaultne na `http://127.0.0.1:5050`, ma route `/health`, jednoduchou chat route `/api/chat` a mazani web pameti pres `/api/clear`.

Panam Dock:

```powershell
python panam_dock_app.py
```

Dock bezi defaultne na `http://127.0.0.1:5051`. Slouzi jen jako lokalni admin panel pro status/start/stop povolenych sluzeb `bot.py` a `panam_web_app.py`.

## Slash Commandy

Zaklad:

- `/ping` - overi, ze je bot online.
- `/help` - zobrazi napovedu.
- `/memory_clear` - vymaze kratkou konverzacni pamet, last file context a last router decision pro aktualni kanal.

AI a zpravy:

- `/ask` - polozi otazku AI.
- `/panam_talk` - osobnejsi talk rezim.
- `/summary` - shrne vlozeny text.
- `/channel_summary` - shrne posledni zpravy aktualniho kanalu.
- `/search_messages` - vyhleda text v poslednich zpravach aktualniho kanalu.

Poznamky a todo:

- `/note_add`
- `/note_list`
- `/note_search`
- `/todo_add`
- `/todo_list`
- `/todo_done`

Soubory:

- `/analyze` - analyzuje obrazek nebo dokument a odpovi do Discord chatu.
- `/read_file` - precte dokument a odpovi na otazku k obsahu.
- `/process_file` - vytvori novy lidsky citelny MD, TXT nebo DOCX vystup.
- `/extract_data` - vytvori strukturovana data jako JSON, CSV, Markdown nebo XLSX.
- `/transform_docx` - vytvori novy cisty DOCX z puvodniho DOCX.
- `/transform_excel` - vytvori novy upraveny XLSX z puvodniho XLSX.
- `/file_job_test` - technicky test file-job pipeline.

## Natural Ovládání

Panam reaguje na prirozene zpravy, kdyz je oslovena, napr. `Panam ...` nebo mentionem bota.

Priklady:

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

Natural flow bezi pres `panam_discord_message_router.py`, helpery pro text a historii, `panam_discord_natural_intents.py`, `panam_router.py` a podle vysledku bud odpovi do chatu, nebo spusti stejnou file-job pipeline jako slash command.

## Natural File Router

Router umi rozhodnout hlavni rezimy:

- `chat_answer` - odpoved primo do Discord chatu.
- `human_document` - novy MD, TXT nebo DOCX soubor pres `/process_file` pipeline.
- `structured_data` - JSON, CSV, Markdown nebo XLSX pres `/extract_data` pipeline.
- `docx_transform` - bezpecna DOCX transformace.
- `spreadsheet_transform` - deterministicka XLSX transformace.
- `unsupported_direct_edit` - Panam odmitne primou editaci puvodni prilohy.

Formaty:

- `excel`, `xlsx`, obecna `tabulka` -> typicky `xlsx`
- `markdown tabulka` -> `md`
- `csv` -> `csv`
- `json` -> `json`
- `word`, `docx` -> `docx`
- `txt`, `cisty text` -> `txt`
- `report`, `checklist`, `prehled`, `navod` -> typicky `md`

Panam si umi pamatovat posledni souborovy kontext podle kanalu a poznat odkazy na soubor podle nazvu, napr. `requirements do csv` nebo `co bylo v requirements?`.

## Panam Web

`panam_web_app.py` je samostatna Flask web appka pro lokalni webovy chat s Panam. Pouziva template `web/templates/index.html`, staticke soubory ve `web/static/` a adapter `panam_web_adapter.py`, ktery vola sdileny `panam_core.py` bez Discord zavislosti.

Routy:

- `GET /` - webove UI.
- `GET /health` - vraci `{"status":"ok","service":"panam-web"}`.
- `POST /api/chat` - posle zpravu do web adapteru.
- `POST /api/clear` - smaze webovou konverzacni pamet.

Pri rucnim spusteni ma web defaultne Flask debug a reloader zapnuty:

```powershell
python panam_web_app.py
```

Dock spousti web jako subprocess s `PANAM_STARTED_BY_DOCK=1`, `PANAM_WEB_DEBUG=0` a `PANAM_WEB_USE_RELOADER=0`. To je dulezite hlavne na Windows, aby se nededily Werkzeug reloader socket/env hodnoty a PID soubor odpovidal skutecnemu spravovanemu procesu.

## Panam Dock

`panam_dock_app.py` je samostatny lokalni admin panel pro spravu procesu Panam. Bezi pouze na `127.0.0.1` a je oddeleny od Panam Webu, aby mohl zapinat a vypinat i `panam_web_app.py`.

Spousti se pres:

```powershell
python panam_dock_app.py
```

Dock bezi na `http://127.0.0.1:5051` a umi zobrazit status, spustit a zastavit pouze povolene sluzby:

- `bot` -> `bot.py`
- `web` -> `panam_web_app.py`

Dock neni urceny pro verejne vystaveni.

Admin token nastav v `.env` pres `PANAM_DOCK_ADMIN_TOKEN`. Pokud token neni nastaveny, POST akce jsou povolene jen pro lokalni requesty z `127.0.0.1`. Port lze zmenit pres `PANAM_DOCK_PORT`.

Procesy spravuje `panam_process_manager.py`:

- validuje service name proti pevnemu allowlistu `bot` a `web`
- pouziva `subprocess.Popen`
- uklada PID soubory do `runtime/pids/`
- zapisuje process logy do `logs/panam_bot_process.log` a `logs/panam_web_process.log`
- u webu overuje skutecny stav pres `http://127.0.0.1:5050/health`, ne jen podle PID
- pri startu child procesu sanitizuje Flask/Werkzeug reloader env promenne (`WERKZEUG_RUN_MAIN`, `WERKZEUG_SERVER_FD`, `FLASK_RUN_FROM_CLI`, `WERKZEUG_DEBUG_PIN`)
- na Windows pouziva `taskkill /PID <pid> /T /F` pro stop celeho stromu procesu

UI zobrazuje `RUNNING`, `STOPPED` nebo `ERROR` vcetne `status_detail`, aby web s zivym procesem, ale nefunkcnim health endpointem nevypadal jako zdrave bezici sluzba.

`panam_test_runner.py` obsahuje bezpecny allowlist smoke testu. Nespousti libovolne prikazy, ale jen pevne pojmenovane test soubory pres aktualni Python interpreter.

## Podporovane Prilohy

Obrazky:

- PNG
- JPG / JPEG
- WEBP
- GIF

Dokumenty:

- TXT
- MD
- CSV
- JSON
- PDF
- DOCX
- XLSX

Limit velikosti prilohy je 20 MB.

Poznamky:

- PDF musi obsahovat extrahovatelny text.
- DOCX cte text a tabulky.
- XLSX cte hodnoty bunek, ne makra ani slozite formatovani.
- JSON se cte jako plain UTF-8 text a muze jit do structured-data pipeline.

## File Jobs

File-job pipeline je v `panam_discord_file_jobs.py` a `panam_files.py`.

Kazdy job ma strukturu:

```text
runtime/jobs/<job_id>/
|-- input/
|-- work/
|-- output/
`-- job.json
```

Pipeline:

1. ulozi docasnou kopii Discord prilohy do `input/`
2. extrahuje text do `work/`
3. zavola AI nebo deterministickou transformaci
4. vytvori novy vystup v `output/`
5. odesle vysledek do Discordu
6. zapise metadata do `job.json`
7. po dokonceni smaze job folder

`job.json` obsahuje metadata, ne obsah dokumentu ani vystupu.

## Bezpecnost

- Puvodni Discord priloha se nikdy neupravuje.
- Vystupy jsou nove soubory.
- Obsah souboru, cele dotazy a vystupy se neloguji.
- `file_summary` se neuklada, pokud obsahuje signaly citlivych dat.
- `ALLOWED_CHANNEL_IDS` muze omezit kanaly, kde bot odpovida.
- `.env`, `notes.json`, `todos.json`, `logs/` a `runtime/` nepatri do gitu.

## Modulova Mapa

Entry a runner:

- `bot.py` - tenky entrypoint.
- `panam_discord_runner.py` - Discord client, slash command dekoratory, `on_message`, env config a `run_discord_bot()`.

Discord command adaptery:

- `panam_discord_basic_commands.py` - `/ping`, `/help`.
- `panam_discord_memory_command.py` - `/memory_clear`.
- `panam_discord_core_commands.py` - `/ask`, `/summary`, `/panam_talk`, notes, todos.
- `panam_discord_channel_commands.py` - `/search_messages`, `/channel_summary`.
- `panam_discord_analyze_command.py` - `/analyze`.
- `panam_discord_read_file.py` - `/read_file`.
- `panam_discord_file_commands.py` - file command validace a delegace do file jobs.
- `panam_discord_file_job_test.py` - `/file_job_test`.

Discord helpery:

- `panam_discord_context.py` - logovani, statusy, safe context.
- `panam_discord_responses.py` - chunkovani a odesilani odpovedi.
- `panam_discord_attachments.py` - typy a metadata priloh.
- `panam_discord_history.py` - hledani poslednich priloh.
- `panam_discord_text_history.py` - hledani poslednich textovych zprav.
- `panam_discord_message_helpers.py` - normalizace a extrakce Panam requestu.
- `panam_discord_attachment_analysis.py` - analyza obrazku/dokumentu do chatu.
- `panam_discord_channel_tools.py` - cteni historie kanalu pro search/summary.

Natural flow:

- `panam_discord_message_router.py` - hlavni natural message orchestrace.
- `panam_discord_natural_intents.py` - natural intent parser.
- `panam_discord_natural_file_orchestrator.py` - prirozene file requesty a AI classifier fallback.
- `panam_phrases.py` - fraze, regexy a signaly.
- `panam_router.py` - ciste file routing rozhodovani.

Core a data:

- `panam_core.py` - core use-cases bez Discordu.
- `panam_ai.py` - OpenAI volani, analyza, sumarizace, classifier.
- `panam_memory.py` - kratka RAM konverzacni pamet.
- `panam_notes.py` - poznamky.
- `panam_todos.py` - todo list.
- `panam_file_context.py` - last file context, file summary a last router decision.

File pipeline:

- `panam_discord_file_jobs.py` - Discord file-job orchestrace.
- `panam_files.py` - job slozky a metadata.
- `panam_text_extraction.py` - extrakce textu z TXT/MD/CSV/JSON/PDF/DOCX/XLSX.
- `panam_file_responses.py` - texty pro file-job odpovedi.
- `panam_docx.py` - tvorba DOCX vystupu.
- `panam_docx_transform.py` - podporovane DOCX transformace.
- `panam_excel.py` - tvorba XLSX z dat.
- `panam_spreadsheet.py` - deterministicke XLSX transformace.

Web a dock:

- `panam_web_app.py` - Flask web UI a API pro lokalni Panam Web.
- `panam_web_adapter.py` - adapter mezi web chatem a sdilenym `panam_core.py`.
- `panam_command_router.py` - sdileny textovy command parser pro adaptery.
- `panam_dock_app.py` - lokalni Flask admin dock pro spravu procesu.
- `panam_process_manager.py` - allowlist subprocess manager pro `bot.py` a `panam_web_app.py`.
- `panam_test_runner.py` - allowlist runner smoke testu bez libovolnych prikazu.
- `web/templates/index.html` a `web/static/` - Panam Web UI.
- `web_admin/templates/dock.html` a `web_admin/static/` - Panam Dock UI.

## Testy

Zakladni smoke testy:

```powershell
python -m py_compile bot.py panam_discord_runner.py
python scripts/bot_entrypoint_smoke_test.py
python scripts/discord_runner_smoke_test.py
python scripts/discord_basic_commands_smoke_test.py
python scripts/discord_message_router_smoke_test.py
python scripts/router_smoke_test.py
python panam_web_smoke_test.py
python panam_web_flask_smoke_test.py
python panam_dock_smoke_test.py
```

Dock smoke test nestartuje realny `bot.py` ani `panam_web_app.py`. Kontroluje importy, `/health`, `/api/status`, stavova pole sluzeb a sanitizaci child env pro Panam Web.

Docx/XLSX testy v tomto workspace typicky pouzivaji `.venv`, protoze systemovy Python nemusi mit `docx`:

```powershell
.\.venv\Scripts\python.exe scripts\text_extraction_smoke_test.py
.\.venv\Scripts\python.exe scripts\discord_file_jobs_smoke_test.py
.\.venv\Scripts\python.exe scripts\docx_transform_smoke_test.py
.\.venv\Scripts\python.exe scripts\spreadsheet_transform_smoke_test.py
```

## Git a Lokální Data

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

