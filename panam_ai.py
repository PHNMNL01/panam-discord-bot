import json
import os
from pathlib import Path
from typing import cast

from dotenv import load_dotenv
from openai import AsyncOpenAI
from openai.types.responses import ResponseInputParam


BASE_DIR = Path(__file__).resolve().parent

load_dotenv(dotenv_path=BASE_DIR / ".env")

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not OPENAI_API_KEY:
    raise RuntimeError("Chybí OPENAI_API_KEY v .env souboru.")


openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)


PANAM_SYSTEM_PROMPT = """
Jmenuješ se Panam.
Jsi Panam, osobní Discord AI asistentka.

Tvoje osobnost:
- jsi ženská AI asistentka
- mluvíš o sobě v ženském rodě
- působíš přátelsky, trochu cyberpunkově, ale ne přehnaně teatrálně
- odpovídáš česky, stručně a prakticky
- máš lehký humor, ale nejsi trapná
- jsi loajální pomocnice pro osobní produktivitu, nápady, recepty, poznámky a technické věci
- nejsi korporátní chatbot
- nepředstíráš, že máš přístup k věcem, které nemáš
- když si nejsi jistá, řekneš to
- bezpečnost bereš vážně

Bezpečnost:
- nepracuj s reálnými HR, firemními, zákaznickými ani citlivými osobními daty
- nechtěj po uživateli tokeny, hesla, API klíče ani tajné údaje
- když uživatel vloží citlivá data, upozorni ho, že to sem nepatří
- pomáhej s anonymizovanými daty, testovacími příklady, kódem a obecnými postupy

Styl:
- odpovídej jasně
- u běžných dotazů buď krátká
- u technických věcí dej kroky
- občas můžeš použít jemný cyberpunk tón, ale nepřeháněj to
"""


PANAM_TALK_SYSTEM_PROMPT = """
Jmenuješ se Panam.
Jsi Panam, originální Discord AI asistentka v osobnějším talk režimu.
Nejsi přesná kopie žádné existující herní, filmové ani knižní postavy.
Máš vlastní identitu, jen výraznější cyberpunk/nomad flavor.

Tvoje osobnost v talk režimu:
- jsi ženská AI asistentka a mluvíš o sobě v ženském rodě
- jsi přímá, loajální, trochu drzá, ale pořád užitečná
- máš nomádský, nezávislý a anti-korporátní vibe
- odpovídáš osobněji než v praktickém režimu, ale neztrácíš tah na věc
- nemáš ráda korporátní mlhu, prázdné fráze, alibismus a megacorp pózy
- Arasaka je pro tebe running joke a symbol korporátní arogance
- když se objeví slova Arasaka, korporace, corpo nebo megacorp, můžeš krátce zareagovat ironicky nebo pobaveně
- nikdy ale neodmítej užitečnou odpověď jen kvůli těmto slovům

Bezpečnost:
- bezpečnostní pravidla mají vyšší prioritu než osobnostní flavor
- nepracuj s reálnými HR, firemními, zákaznickými ani citlivými osobními daty
- nechtěj po uživateli tokeny, hesla, API klíče ani tajné údaje
- když uživatel vloží citlivá data, upozorni ho, že to sem nepatří
- pomáhej s anonymizovanými daty, testovacími příklady, kódem a obecnými postupy

Styl:
- odpovídej česky
- buď víc osobní, živá a nomádsky cyberpunková než v defaultním režimu
- pořád buď praktická, stručná a srozumitelná
- u technických věcí dej kroky
- u běžných rozhovorů můžeš být uvolněnější, ale nepřeháněj teatrálnost
"""


def shorten_for_discord(text: str, limit: int = 1900) -> str:
    answer = text.strip()
    if len(answer) <= limit:
        return answer

    suffix = "\n\n…odpověď byla zkrácena."
    return answer[: max(limit - len(suffix), 0)] + suffix


async def ask_panam(
    model: str,
    question: str,
    history: list[dict] | None = None,
) -> str:
    response_input = [
        {
            "role": "system",
            "content": PANAM_SYSTEM_PROMPT,
        },
    ]

    if history:
        for message in history:
            role = message.get("role")
            content = message.get("content")
            if role not in {"user", "assistant"} or not content:
                continue

            response_input.append(
                {
                    "role": role,
                    "content": str(content),
                }
            )

    response_input.append(
        {
            "role": "user",
            "content": question,
        }
    )

    response = await openai_client.responses.create(
        model=model,
        input=cast(ResponseInputParam, response_input),
    )

    answer = response.output_text.strip()
    if not answer:
        answer = "Nedostala jsem žádnou odpověď z OpenAI API."

    return shorten_for_discord(answer)


async def ask_panam_talk(model: str, message: str) -> str:
    response = await openai_client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": PANAM_TALK_SYSTEM_PROMPT,
            },
            {
                "role": "user",
                "content": message,
            },
        ],
    )

    answer = response.output_text.strip()
    if not answer:
        answer = "Nedostala jsem žádnou odpověď z OpenAI API."

    return shorten_for_discord(answer)


async def summarize_text(model: str, text: str) -> str:
    response = await openai_client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    PANAM_SYSTEM_PROMPT +
                    "\n\nÚkol pro tento command: stručně shrnuj texty v češtině. "
                    "Shrnuj jasně, věcně a krátce. "
                    "Nezpracovávej citlivá HR, firemní ani zákaznická data."
                ),
            },
            {
                "role": "user",
                "content": text,
            },
        ],
    )

    answer = response.output_text.strip()
    if not answer:
        answer = "Nedostala jsem žádné shrnutí z OpenAI API."

    return shorten_for_discord(answer)


async def summarize_channel_messages(model: str, channel_text: str) -> str:
    response = await openai_client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    PANAM_SYSTEM_PROMPT +
                    "\n\nÚkol pro tento command: Shrň poslední zprávy "
                    "z Discord kanálu stručně, jasně a česky. "
                    "Vypíchni hlavní témata, rozhodnutí a případné úkoly."
                ),
            },
            {
                "role": "user",
                "content": channel_text,
            },
        ],
    )

    answer = response.output_text.strip()
    if not answer:
        answer = "Nedostala jsem žádné shrnutí z OpenAI API."

    return shorten_for_discord(answer)


async def analyze_image(model: str, image_url: str, question: str) -> str:
    response_input = cast(
        ResponseInputParam,
        [
            {
                "role": "system",
                "content": (
                    PANAM_SYSTEM_PROMPT +
                    "\n\nÚkol pro tento command: analyzuj přiložený obrázek. "
                    "Odpovídej česky, jasně a prakticky. "
                    "Nepředstírej jistotu, pokud obrázek něco neukazuje dostatečně jasně."
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": question,
                    },
                    {
                        "type": "input_image",
                        "image_url": image_url,
                    },
                ],
            },
        ],
    )

    response = await openai_client.responses.create(
        model=model,
        input=response_input,
    )

    answer = response.output_text.strip()
    if not answer:
        answer = "Nedostala jsem žádnou analýzu z OpenAI API."

    return shorten_for_discord(answer)


async def analyze_document_text(
    model: str,
    document_text: str,
    question: str,
    filename: str,
) -> str:
    response = await openai_client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    PANAM_SYSTEM_PROMPT +
                    "\n\nÚkol pro tento command: analyzuj text dokumentu. "
                    "Odpovídej česky, jasně a prakticky. "
                    "Pokud se uživatel ptá na shrnutí, udělej stručné shrnutí. "
                    "Pokud se ptá konkrétně, odpověz podle obsahu dokumentu. "
                    "Pokud odpověď v dokumentu není, řekni to."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Název souboru: {filename}\n"
                    f"Otázka: {question}\n\n"
                    "Text dokumentu:\n"
                    f"{document_text}"
                ),
            },
        ],
    )

    answer = response.output_text.strip()
    if not answer:
        answer = "Nedostala jsem žádnou analýzu dokumentu z OpenAI API."

    return shorten_for_discord(answer)


async def process_document_text(
    model: str,
    document_text: str,
    instruction: str,
    filename: str,
    output_format: str,
) -> str:
    normalized_format = "md" if output_format.lower().strip(".") == "md" else "txt"
    format_instruction = (
        "Vystup musi byt validni Markdown bez uvodniho komentare."
        if normalized_format == "md"
        else "Vystup musi byt cisty text bez Markdown formatu a bez uvodniho komentare."
    )

    response = await openai_client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    PANAM_SYSTEM_PROMPT +
                    "\n\nUkol pro tento command: zpracuj text dokumentu podle instrukce uzivatele. "
                    "Odpovez pouze obsahem vystupniho dokumentu. "
                    "Nepridavej Discord komentare typu 'Jasne, tady to je'. "
                    "Pokud je instrukce nejasna, udelej obecne uzitecne shrnuti dokumentu. "
                    "Nepredstirej informace, ktere v dokumentu nejsou. "
                    "Pokud dokument vypada, ze obsahuje hesla, tokeny, API klice nebo velmi citliva data, "
                    "neopisuj je zbytecne do vystupu a radeji je obecne oznac jako citlive udaje. "
                    f"{format_instruction}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Nazev souboru: {filename}\n"
                    f"Pozadovany format vystupu: {normalized_format}\n"
                    f"Instrukce uzivatele: {instruction}\n\n"
                    "Text dokumentu:\n"
                    f"{document_text}"
                ),
            },
        ],
    )

    answer = response.output_text.strip()
    if not answer:
        answer = "Nepodarilo se vytvorit vystupni dokument."

    return answer


async def extract_structured_data(
    model: str,
    document_text: str,
    instruction: str,
    filename: str,
    output_format: str,
) -> str:
    normalized_format = output_format.lower().strip(".")
    if normalized_format not in {"json", "csv", "md"}:
        normalized_format = "json"

    if normalized_format == "json":
        format_instruction = (
            "Odpovez pouze validnim JSON. Bez Markdown code blocku. Bez textu pred nebo za JSONem. "
            "Doporucena obecna struktura je {\"items\": [], \"notes\": \"\"}. "
            "Pokud data nenajdes, vrat {\"items\": [], \"notes\": \"V dokumentu jsem nenasla pozadovana data.\"}."
        )
    elif normalized_format == "csv":
        format_instruction = (
            "Odpovez pouze CSV textem. Prvni radek musi byt hlavicka. "
            "Bez Markdown code blocku a bez komentaru okolo. "
            "Pokud data nenajdes, vrat presne dva radky: note a V dokumentu jsem nenasla pozadovana data."
        )
    else:
        format_instruction = (
            "Odpovez validnim Markdownem vhodnym pro tabulky, seznamy a prehledy."
        )

    response = await openai_client.responses.create(
        model=model,
        input=[
            {
                "role": "system",
                "content": (
                    PANAM_SYSTEM_PROMPT +
                    "\n\nUkol pro tento command: vytez ze zadaneho dokumentu strukturovana data podle instrukce uzivatele. "
                    "Odpovez pouze obsahem vystupniho souboru. "
                    "Nepridavej Discord komentare typu 'Jasne, tady to je'. "
                    "Nepredstirej data, ktera v dokumentu nejsou. "
                    "Pokud je instrukce nejasna, vytez obecne uzitecna data z dokumentu. "
                    "Pokud dokument vypada, ze obsahuje hesla, tokeny, API klice nebo velmi citliva data, "
                    "nevytahuj je do vystupu zbytecne a oznac je obecne jako citlive udaje. "
                    f"{format_instruction}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Nazev souboru: {filename}\n"
                    f"Pozadovany format vystupu: {normalized_format}\n"
                    f"Instrukce uzivatele: {instruction}\n\n"
                    "Text dokumentu:\n"
                    f"{document_text}"
                ),
            },
        ],
    )

    answer = response.output_text.strip()
    if not answer:
        if normalized_format == "json":
            return '{"items": [], "notes": "V dokumentu jsem nenasla pozadovana data."}'
        if normalized_format == "csv":
            return "note\nV dokumentu jsem nenasla pozadovana data."
        return "V dokumentu jsem nenasla pozadovana data."

    return answer


async def classify_file_request_intent(
    model: str,
    request_text: str,
    file_context: dict | None = None,
    recent_context: list[dict] | None = None,
) -> str:
    classifier_input = {
        "request_text": request_text,
        "file_context": file_context or {},
        "recent_context": recent_context or [],
    }

    response = await openai_client.responses.create(
        model=model,
        input=cast(
            ResponseInputParam,
            [
                {
                    "role": "system",
                    "content": (
                        "You classify ambiguous Discord requests for Panam. "
                        "Return only valid JSON, with no Markdown and no extra text. "
                        "Never invent file context. If no current attachment and no last file context exist, "
                        "do not choose a file target. Hard explicit requests must be respected: direct edits are "
                        "unsupported_direct_edit; Excel/XLSX/CSV/JSON output is structured_data; report/checklist/"
                        "overview/file/Markdown/TXT output is human_document. If file context exists and the user "
                        "asks for 'tabulku', 'do tabulky', 'tabulkove', or 'udelej z toho tabulku', this is probably "
                        "structured_data. Default a generic table request to output_format xlsx. If the user explicitly "
                        "asks for a Markdown table, use md. If the user says csv, use csv. Prefer conversation when uncertain."
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        "Allowed JSON shape:\n"
                        "{"
                        "\"target\":\"conversation|current_attachment|last_file_context|none\","
                        "\"mode\":\"chat_answer|human_document|structured_data|unsupported_direct_edit\","
                        "\"output_format\":null,"
                        "\"question\":\"string|null\","
                        "\"instruction\":\"string|null\","
                        "\"confidence\":0.0,"
                        "\"reason\":\"short reason\""
                        "}\n"
                        "output_format must be one of md, txt, json, csv, xlsx, or JSON null.\n\n"
                        "Input JSON:\n"
                        f"{json.dumps(classifier_input, ensure_ascii=False)}"
                    ),
                },
            ],
        ),
    )

    return response.output_text.strip()
