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
