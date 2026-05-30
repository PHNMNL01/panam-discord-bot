# Panam Discord Bot

Panam je osobni Discord AI asistentka v Pythonu. Umi odpovidat na dotazy, shrnovat texty a zpravy v kanalu, vest jednoduche poznamky a todo listy, analyzovat obrazky a pracovat s dokumentovymi prilohami.

Projekt je zatim prakticky PoC, ale uz ma oddelenou AI vrstvu, prirozene fraze, kratkou pamet a izolovanou file-job pipeline pro docasne zpracovani souboru.

## Co Panam umi

- odpovidat pres OpenAI API
- shrnovat vlozeny text nebo posledni zpravy v kanalu
- hledat v poslednich zpravach kanalu
- ukladat a hledat lokalni poznamky
- spravovat jednoduchy todo list
- analyzovat obrazky a dokumenty
- cist TXT, MD, CSV, PDF, DOCX a XLSX
- vytvaret lidske vystupni dokumenty jako MD, TXT nebo DOCX
- tezit strukturovana data do JSON, CSV, Markdownu nebo XLSX
- reagovat na prirozene fraze typu `Panam shrn to`
- navazovat na posledni zpracovany soubor pres bezpecne kratke RAM shrnuti
- poznat odkaz na posledni soubor podle nazvu nebo casti nazvu, napr. `requirements`

## Zakladni principy

- Slash commandy jsou presne nastroje.
- Prirozene fraze jsou jen router, ktery vybere existujici nastroj.
- Natural parser sam neupravuje soubory.
- Puvodni Discord priloha se nikdy neupravuje.
- Prace se soubory probiha v docasnem file jobu: `input/`, `work/`, `output/`, `job.json`.
- Obsah souboru, cele dotazy a vystupy se neloguji.
- Kratke file contexty jsou jen v RAM a po restartu zmizi.

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
```

Promenne:

- `DISCORD_BOT_TOKEN` - token Discord bota.
- `OPENAI_API_KEY` - OpenAI API klic.
- `DISCORD_GUILD_IDS` - volitelny seznam ID serveru oddelenych carkou. Kdyz je prazdny, slash commandy se synchronizuji globalne.
- `ALLOWED_CHANNEL_IDS` - volitelny seznam ID kanalu oddelenych carkou. Kdyz je prazdny, bot muze odpovidat ve vsech kanalech.
- `OPENAI_MODEL` - model pouzivany pro OpenAI volani.
- `OPENAI_PROMPT_ID` - volitelny OpenAI Prompt Management prompt pro beznou osobnost Panam v `ask_panam`.
- `OPENAI_PROMPT_VERSION` - volitelna verze promptu z OpenAI Prompt Managementu.

`.env` nikdy nedavej do gitu ani do chatu.

### OpenAI Prompt Management

Bezny chat Panam muze pouzivat ulozeny prompt z OpenAI Prompt Managementu.

Kdyz je v `.env` nastavene `OPENAI_PROMPT_ID`, funkce `ask_panam` pouzije tento ulozeny prompt a nepridava duplicitne lokalni `PANAM_SYSTEM_PROMPT`. Historie konverzace a aktualni dotaz uzivatele zustavaji v `input`.

Kdyz `OPENAI_PROMPT_ID` nastavene neni, Panam pouzije lokalni `PANAM_SYSTEM_PROMPT` jako fallback.

Priklad:

```env
OPENAI_PROMPT_ID=pmpt_6a15c43bf29c8195a98170b28e5b645404f3503dee0cb7be
OPENAI_PROMPT_VERSION=3
```

Prompt Management se zatim pouziva primarne pro default chat osobnost Panam. Rezim `/panam_talk`, sumarizace, analyza souboru, file processing, extrakce dat a AI classifier maji dal vlastni lokalni task-specific instrukce, aby se nerozbilo jejich presne chovani.

Slozka `panam_prompts/` slouzi jen jako verzovana dokumentacni zaloha promptu z Prompt Managementu. Runtime zdroj pravdy je stale `.env`.

## Spusteni na Windows

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python bot.py
```

## Slash Commandy

Zakladni:

- `/ping` - overi, ze je bot online.
- `/help` - zobrazi napovedu.
- `/memory_clear` - vymaze kratkou konverzacni pamet, last file context a posledni router decision pro aktualni kanal.

AI a zpravy:

- `/ask` - polozi otazku AI.
- `/panam_talk` - osobnejsi talk rezim Panam.
- `/summary` - shrne vlozeny text.
- `/channel_summary` - shrne posledni zpravy aktualniho kanalu.
- `/search_messages` - vyhleda text v poslednich zpravach aktualniho kanalu.

Poznamky a todo:

- `/note_add` - ulozi poznamku.
- `/note_list` - zobrazi posledni poznamky.
- `/note_search` - vyhleda v poznamkach.
- `/todo_add` - prida ukol.
- `/todo_list` - zobrazi aktivni ukoly.
- `/todo_done` - oznaci ukol jako hotovy.

Soubory:

- `/analyze` - analyzuje obrazek nebo dokument a odpovi do Discord chatu.
- `/read_file` - precte dokument a odpovi na otazku k obsahu.
- `/process_file` - vytvori novy lidsky citelny MD, TXT nebo DOCX vystup.
- `/extract_data` - vytvori strukturovana data jako JSON, CSV, Markdown nebo XLSX.
- `/file_job_test` - technicky test file-job pipeline.

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
- PDF
- DOCX
- XLSX

Limit velikosti prilohy je 20 MB.

Poznamky k formatum:

- PDF musi obsahovat extrahovatelny text. Skenovane PDF bez OCR zatim nemusi fungovat.
- DOCX cte bezny text a tabulky.
- XLSX cte hodnoty bunek z vice listu. Necte makra, grafy, kontingencni tabulky ani slozite formatovani.
- U XLSX vzorcu se ctou vypoctene hodnoty, pokud jsou v souboru ulozene.

Limity pro XLSX vstup:

- maximalne 10 listu
- maximalne 500 radku na list
- maximalne 50 sloupcu na list

## Prace Se Soubory

### Rychla odpoved do chatu

Pouzij `/analyze` nebo `/read_file`, kdyz chces odpoved primo do Discord zpravy.

Priklady:

```text
/analyze file:screenshot.png question:Co je tady za chybu?
/analyze file:report.pdf question:Shrn mi hlavni body.
/read_file file:export.xlsx question:Najdi mozne chyby v tabulce.
```

`/analyze` umi pouzit i posledni podporovanou prilohu v aktualnim kanalu, pokud neni soubor prilozeny primo v commandu.

### Lidsky vystupni dokument

Pouzij `/process_file`, kdyz chces z dokumentu vytvorit novy citelny soubor pro cloveka.

Vystupy:

- `md`
- `txt`
- `docx`

Priklady:

```text
/process_file file:requirements.txt instruction:Vysvetli knihovny lidsky output_format:md
/process_file file:sample.pdf instruction:Udelej z toho kratky checklist output_format:md
/process_file file:notes.docx instruction:Prepis to do cisteho textu output_format:txt
/process_file file:report.pdf instruction:Udelej z toho Word dokument output_format:docx
```

### Strukturovana data

Pouzij `/extract_data`, kdyz chces vytahnout data do strojove nebo tabulkove podoby.

Vystupy:

- `json` - validuje se pres JSON parser
- `csv` - musi byt neprazdne CSV s hlavickou
- `md` - Markdown vhodny pro tabulky, seznamy a prehledy
- `xlsx` - novy jednoduchy Excel soubor

Priklady:

```text
/extract_data file:requirements.txt instruction:Vytahni knihovny a verze output_format:json
/extract_data file:export.xlsx instruction:Vytahni radky, kde je stav Chyba output_format:csv
/extract_data file:export.csv instruction:Vytahni jmeno, email a datum output_format:xlsx
```

Pro `output_format=xlsx` AI nevytvari Excel primo. AI vrati strukturovana JSON data, Python je zvaliduje a vytvori z nich soubor pojmenovany podle puvodni prilohy, napr. `export_by_Panam.xlsx`.

Zakladni XLSX vystup v1:

- tucne hlavicky
- autofilter
- zmrazeny prvni radek
- automaticke sirky sloupcu
- zalamovani dlouheho textu

XLSX vystup zatim neresi barvy, slozite styly, vice listu, grafy, makra, vzorce ani upravu puvodniho XLSX.

## Prirozene Ovladani

Panam reaguje na prirozene fraze jen kdyz je oslovena, typicky `Panam ...` nebo mentionem bota.

### Bezne dotazy

```text
Panam <dotaz>
Panam rekni mi <dotaz>
Panam rekni <dotaz>
Panam odpovez <dotaz>
Panam co si myslis o <text>
Co si mysli Panam o <text>
Panam co si o tom myslis?
```

Dotazy typu `co si o tom myslis?` pouziji predchozi vhodnou zpravu jako kontext.

### Shrnuti textu

```text
Panam shrn <text>
Panam shrn mi <text>
Panam udelej summary <text>
Panam shrn to
Panam shrn toto
```

Kdyz je k dispozici priloha, `shrn to` se vztahuje k prilohovemu kontextu. Jinak se pouzije predchozi vhodna textova zprava.

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

### Natural File Router

Kdyz uzivatel oslovi Panam a prilozi podporovany soubor, nebo odkazuje na posledni podporovanou prilohu, Panam rozhodne jeden ze tri rezimu.

#### 1. chat_answer

Bezpecny default. Vysledek je normalni odpoved do Discord chatu.

Priklady:

```text
Panam co je v tom souboru?
Panam shrn to
Panam najdi chyby v te tabulce
Panam vysvetli mi ten dokument
Panam co obsahuje to PDF?
Panam zpracuj ten soubor
```

#### 2. human_document

Panam pouzije stejnou pipeline jako `/process_file` a vytvori novy MD, TXT nebo DOCX soubor.

Priklady:

```text
Panam udelej z toho report
Panam udelej z toho checklist
Panam vytvor z toho prehled
Panam zpracuj to do markdownu
Panam prepis to do cisteho textu
Panam dej mi to do Wordu
Panam udelej z toho DOCX
Panam priprav z toho navod
```

Format se urcuje z textu:

- `markdown`, `md`, `report`, `checklist`, `prehled`, `navod` -> typicky `md`
- `txt`, `cisty text` -> `txt`
- `word`, `wordu`, `word dokument`, `docx` -> `docx`

#### 3. structured_data

Panam pouzije stejnou pipeline jako `/extract_data` a vytvori JSON, CSV, Markdown nebo XLSX.

Priklady:

```text
Panam vytahni z toho data do Excelu
Panam vytahni radky, kde je stav chyba
Panam dej to do CSV
Panam vrat JSON
Panam vytahni jmena a emaily
Panam vyber sloupce jmeno, email a datum
```

Format se urcuje z textu:

- `excel`, `xlsx`, `do tabulky` -> `xlsx`
- `csv` -> `csv`
- `json` -> `json`
- obecna `tabulka`, `tabulku`, `tabulkove`, `udelej z toho tabulku` -> typicky `xlsx`
- `markdown tabulka` -> `md`
- `soubor csv` -> `csv`
- jinak default pro strukturovana data je `json`

Priorita routeru:

1. Explicitni datovy format nebo data -> `structured_data`.
2. Report, checklist, navod, prehled nebo novy textovy soubor -> `human_document`.
3. Jinak odpoved do chatu -> `chat_answer`.

Tvrda pravidla maji vzdy prednost pred AI classifierem:

- primy pozadavek na upravu puvodniho souboru -> `unsupported_direct_edit`
- `do Excelu`, `xlsx`, `csv`, `json` -> `structured_data`
- `report`, `checklist`, `prehled`, `do souboru`, `markdown`, `txt`, `word`, `docx` -> `human_document`
- explicitni souborovy subjekt jako `soubor`, `priloha`, `dokument`, `tabulka`, `PDF`, `Excel`, `DOCX` -> file router

Obecne vety bez jasneho souboroveho subjektu, napr. `Panam co je na tom spatne?`, `Panam co je tam spatne?` nebo `Panam najdi chybu`, se samy od sebe nesnazi brat posledni prilohu. Nejdou automaticky do file routeru, pokud neni priloha primo u aktualni zpravy nebo pokud neni jasny vystupni souborovy pozadavek.

Meta rozhovor o routeru a chovani Panam take nespousti file router jen kvuli slovu `soubor`. Napr. `Panam jen testuju, ze jsi nehledala soubor` nebo `Panam proc jsi hledala soubor?` jde do bezne konverzace.

Priklady rozhodovani:

```text
Panam najdi chyby v te tabulce
-> chat_answer

Panam najdi chyby v te tabulce a dej je do Excelu
-> structured_data, xlsx

Panam najdi chyby v te tabulce a udelej report
-> human_document, md

Panam vytahni jmena a emaily do JSONu
-> structured_data, json

Panam udelej z toho tabulku
-> structured_data, xlsx

Panam udelej z toho soubor csv
-> structured_data, csv

Panam prepis to do cisteho textu
-> human_document, txt

Panam dej mi to do Wordu
-> human_document, docx
```

Panam zatim primo neupravuje puvodni Excel ani puvodni Discord prilohu. Kdyz uzivatel napise napr. `Panam uprav ten Excel`, Panam odpovi, ze umi vytvorit novy XLSX, CSV, Markdown, TXT nebo DOCX vystup.

#### Odkaz na posledni soubor podle nazvu

Kdyz existuje `last_file_context`, Panam umi poznat odkaz na posledni soubor podle celeho nazvu nebo casti nazvu. Match je case-insensitive, bez diakritiky a tolerantni k pripone.

Priklad pro `source_filename=requirements.txt`:

```text
Panam ten soubor requirements
Panam vrat se k requirements
Panam co bylo v requirements?
Panam jake knihovny byly v requirements?
Panam udelej z requirements tabulku
Panam requirements do csv
Panam ten requirements dej do Excelu
```

Kratke nebo obecne tokeny se nepouzivaji jako dukaz match, napr. `to`, `tom`, `ten`, `md`, `txt`, `csv`, `pdf`, `xlsx`, `soubor`, `dokument`, `tabulka`.

Pokud dotaz podle nazvu souboru jen navazuje na obsah a existuje bezpecne `file_summary`, Panam odpovi podle nej. Pokud uzivatel zada vystupni soubor nebo format, vyhraje file router:

```text
Panam co bylo v requirements?
-> odpoved podle file_summary, pokud existuje

Panam jake knihovny byly v requirements?
-> odpoved podle file_summary, pokud existuje

Panam udelej z requirements tabulku
-> structured_data, xlsx

Panam requirements do csv
-> structured_data, csv

Panam ten requirements dej do Excelu
-> structured_data, xlsx

Panam udelej z requirements soubor
-> human_document, md
```

Meta vety o chovani Panam/routeru stale zustavaji bezna konverzace, i kdyz obsahuji nazev souboru. Napr. `Panam proc jsi hledala requirements soubor?` nebo `Panam nemela jsi hledat requirements` nespousti file router.

### Last File Context

Panam si v RAM pamatuje posledni souborovy kontext pro kazdy kanal. Uklada se po uspesnem zpracovani souboru pres natural request i pres slash commandy `/analyze`, `/read_file`, `/process_file` a `/extract_data`.

Ukladaji se jen bezpecna metadata:

- `source_filename`
- `source_extension`
- `last_output_filename`
- `last_mode`
- `updated_at`
- `file_summary` - volitelne kratke bezpecne shrnuti, max 800 znaku

`file_summary` vznikne po uspesnem `chat_answer` nad souborem, napr. pres natural dotaz, `/analyze` nebo `/read_file`. Pouziva se jen pro navazujici dotazy typu:

```text
Panam co tam bylo?
Panam co v tom bylo?
Panam jake knihovny tam byly?
Panam k cemu ten soubor byl?
Panam shrn to jeste kratceji
Panam vysvetli to jednoduseji
```

Panam u odpovedi podle `file_summary` nepredstira, ze zna cely raw obsah souboru. Pokud ze shrnuti nejde odpovedet, ma si rict o soubor nebo upresneni.

Bezpecnost `file_summary`:

- neuklada se raw extracted text
- neuklada se cely obsah souboru
- neukladaji se vystupy file jobu
- max delka je 800 znaku
- pokud text obsahuje signaly jako `password`, `token`, `api key`, `secret`, `heslo`, `rodne cislo`, `bankovni ucet` nebo `osobni udaje`, summary se neulozi
- `file_summary` se neloguje

Po `/process_file` nebo `/extract_data` muze `file_summary` zustat zachovane pro stejny soubor, pokud uz existovalo. Neuklada se obsah souboru, obsah vystupu ani cely dotaz uzivatele. Context je jen pomocna stopa pro dalsi rozhodovani a po restartu bota zmizi.

### Last Router Decision

Panam si v RAM pamatuje i posledni rozhodnuti routeru/classifieru podle kanalu. Je to pouze diagnosticky a kontextovy signal, ne dlouhodoba pamet.

Uklada se:

- `target`
- `mode`
- `output_format`
- `classifier_used`
- `confidence`
- `updated_at`

Neuklada se cely dotaz uzivatele, obsah souboru, obsah konverzace ani `reason` z classifieru.

`/memory_clear` maze kratkou konverzacni pamet, `last_file_context` vcetne `file_summary` a `last_router_decision` pro aktualni kanal.

### AI Intent Classifier

Pro nejasne natural dotazy ma Panam maly AI classifier. Pouziva se jen jako fallback, kdyz tvrda pravidla nerozhodla.

Typicke dotazy:

```text
Panam shrn to
Panam vysvetli to
Panam co je na tom spatne?
Panam co dal?
Panam udelej s tim neco pouzitelneho
Panam priprav mi to nejak rozumne
```

Classifier muze zvolit:

- `conversation` - navazuje na beznou konverzaci nebo predchozi odpoved Panam
- `current_attachment` - pouzit aktualne prilozeny soubor
- `last_file_context` - pouzit posledni znamy file context v kanalu
- `none` - neni jasne

Kdyz confidence vyjde nizko nebo validace selze, Panam spadne zpet na beznou konverzaci. Classifier nesmi vymyslet souborovy kontext, kdyz neni aktualni priloha ani `last_file_context`.

Classifier dostava jen bezpecna metadata: jestli existuje aktualni priloha, jeji nazev a priponu, last file context metadata, informaci o bezpecnem `file_summary` a kratky sanitizovany konverzacni kontext. Obsah souboru se classifieru neposila.

Debug log classifieru je omezeny na bezpecna pole:

- `classifier_used`
- `target`
- `mode`
- `output_format`
- `confidence`
- `classifier_status`

Neloguji se cele dotazy, `reason`, obsah souboru ani obsah konverzace.

### Router Test Cases

Priklady pro ladeni routeru jsou v `docs/router_test_cases.md`. Soubor slouzi jako living spec pro typicke a problemove vety, napr.:

- `Panam udelej z toho tabulku`
- `Panam udelej z toho soubor csv`
- `Panam co bylo v requirements?`
- `Panam requirements do csv`
- `Panam ten requirements dej do Excelu`
- `Panam jen testuju, ze jsi nehledala soubor`
- `Panam co je na tom spatne?`
- `Panam co je spatne v tom souboru?`

Lokalni smoke test routeru bez Discordu a OpenAI:

```powershell
python scripts/router_smoke_test.py
```

## File Jobs

File-job pipeline je izolovane docasne zpracovani souboru v `panam_files.py`.

Kazdy job ma strukturu:

```text
runtime/jobs/<job_id>/
|-- input/
|-- work/
|-- output/
`-- job.json
```

Co pipeline dela:

- ulozi docasnou kopii Discord prilohy do `input/`
- extrahuje text do `work/`
- vytvori novy vystup v `output/`
- odesle vystup zpet do Discordu
- zapise metadata jobu do `job.json`
- po dokonceni smaze cely job folder

`job.json` obsahuje jen metadata, napr. job id, stav, akci, nazvy souboru, pripony a velikosti. Neobsahuje text dokumentu ani vystup.

Test:

```text
/file_job_test file:<dokument> output_format:md
```

Lokalni smoke test DOCX helperu:

```powershell
python scripts/docx_smoke_test.py
```

## Kratka Pamet

- Panam si v RAM pamatuje poslednich nekolik beznych konverzacnich zprav v kanalu.
- Pamet se pouziva pro `Panam <dotaz>`.
- Panam si v RAM pamatuje posledni souborovy kontext v kanalu.
- Souborovy kontext muze obsahovat kratke bezpecne `file_summary`.
- Panam si v RAM pamatuje posledni router/classifier rozhodnuti v kanalu.
- Po restartu bota se smaze.
- Pamet lze smazat pres `/memory_clear`.
- Poznamky a todo jsou samostatne funkce a ukladaji se do `notes.json` a `todos.json`.

## Bezpecnost

- Panam funguje jen v kanalech uvedenych v `ALLOWED_CHANNEL_IDS`, pokud jsou nastavene.
- Historii kanalu cte jen pri explicitnim commandu nebo pri osloveni podporovanou frazi.
- Puvodni Discord priloha se nikdy neupravuje.
- File job pracuje jen s docasnou kopii souboru.
- Panam neni urcena pro hesla, tokeny, API klice, HR data, zakaznicka data ani jina citliva data.
- `file_summary` se neuklada, pokud obsahuje podezrele citlive signaly.
- `.env`, `notes.json`, `todos.json`, `logs/` a `runtime/` nepatri do gitu.

## Logovani

Panam zapisuje provozni logy do `logs/panam.log` a soucasne vypisuje do konzole.

Loguje se:

- start bota a `on_ready`
- slash commandy a rozpoznane natural akce
- stav akce: started, success, error, denied
- u priloh jen bezpecna metadata: nazev souboru, pripona, velikost a typ
- u file jobu metadata jako `job_id`, format vystupu a stav

Neloguje se:

- obsah `.env`
- tokeny a API klice
- obsah priloh
- cele uzivatelske dotazy
- obsah vystupu
- `file_summary`
- `reason` z AI classifieru

Log se rotuje pri velikosti 1 MB a uchovava 5 zaloznich souboru.

## Struktura Projektu

```text
panam-discord-bot/
|-- bot.py
|-- panam_ai.py
|-- panam_docx.py
|-- panam_excel.py
|-- panam_file_context.py
|-- panam_files.py
|-- panam_memory.py
|-- panam_phrases.py
|-- panam_router.py
|-- panam_prompts/
|-- scripts/
|-- requirements.txt
|-- .env.example
|-- .gitignore
|-- README.md
|-- docs/
|-- notes.json
|-- todos.json
|-- logs/
`-- runtime/
```

Soubory:

- `bot.py` - Discord logika, slash commandy, natural message handling a napojeni pipeline.
- `panam_ai.py` - OpenAI volani, system prompt, analyza, shrnuti, file AI funkce a AI intent classifier.
- `panam_docx.py` - tvorba jednoducheho DOCX vystupu z textu.
- `panam_phrases.py` - prirozene fraze, signaly a intent patterny.
- `panam_router.py` - ciste router/helper funkce pro natural file rozhodovani.
- `panam_prompts/` - verzovana dokumentacni zaloha promptu z OpenAI Prompt Managementu.
- `panam_prompts/readme_panam_promts.md` - poznamky ke slozce s prompt zalohami.
- `panam_prompts/panam_personality_v3.md` - sablona/zaloha promptu Panam Personality v3.
- `panam_file_context.py` - RAM last-file context, file summary a posledni router decision podle kanalu.
- `panam_files.py` - izolovana file-job pipeline.
- `panam_excel.py` - tvorba jednoducheho XLSX vystupu ze strukturovanych dat.
- `panam_memory.py` - kratka RAM konverzacni pamet podle kanalu.
- `requirements.txt` - Python zavislosti.
- `docs/router_test_cases.md` - testovaci priklady pro natural router a classifier.
- `scripts/docx_smoke_test.py` - lokalni manualni test DOCX helperu.
- `scripts/router_smoke_test.py` - lokalni manualni test natural file routeru.
- `.env.example` - sablona konfigurace.
- `.env` - lokalni konfigurace s tokeny a klici, nepatri do gitu.
- `notes.json` - lokalni poznamky, nepatri do gitu.
- `todos.json` - lokalni todo list, nepatri do gitu.
- `logs/` - lokalni provozni logy, nepatri do gitu.
- `runtime/` - docasne file joby, nepatri do gitu.

## Git

Do gitu patri hlavne:

- `bot.py`
- `panam_ai.py`
- `panam_docx.py`
- `panam_excel.py`
- `panam_file_context.py`
- `panam_files.py`
- `panam_memory.py`
- `panam_phrases.py`
- `panam_router.py`
- `panam_prompts/readme_panam_promts.md`
- `panam_prompts/panam_personality_v3.md`
- `scripts/docx_smoke_test.py`
- `scripts/router_smoke_test.py`
- `requirements.txt`
- `README.md`
- `docs/router_test_cases.md`
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
- `logs/`
- `runtime/`

Do `panam_prompts/` patri jen prompt texty, metadata a poznamky. Nepatri tam tokeny, API klice, hesla ani citliva data.
