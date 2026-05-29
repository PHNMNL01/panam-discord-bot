import json
import re
import unicodedata

from panam_phrases import DIRECT_FILE_EDIT_SIGNALS, HUMAN_DOCUMENT_SIGNALS


SUPPORTED_IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".gif")


def normalize_natural_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text.lower())
    without_diacritics = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", without_diacritics).strip(" \t\n\r,.:;!-?")


def contains_natural_signal(text: str, signals: tuple[str, ...]) -> bool:
    normalized = normalize_natural_text(text)
    for signal in signals:
        if re.fullmatch(r"[a-z0-9]{1,3}", signal):
            if re.search(rf"\b{re.escape(signal)}\b", normalized) is not None:
                return True
            continue

        if signal in normalized:
            return True

    return False


def detect_output_format(text: str, default: str = "md") -> str:
    normalized = normalize_natural_text(text)

    if any(signal in normalized for signal in ("xlsx", "excel", "do excelu", "do tabulky")):
        return "xlsx"
    if re.search(r"\bcsv\b", normalized) is not None:
        return "csv"
    if re.search(r"\bjson(?:u|em)?\b", normalized) is not None:
        return "json"
    if any(signal in normalized for signal in ("markdown", "markdownu")):
        return "md"
    if re.search(r"\bmd\b", normalized) is not None:
        return "md"
    if any(signal in normalized for signal in ("txt", "cisty text", "cisteho textu")):
        return "txt"

    return default.lower().strip(".")


def has_explicit_file_subject(text: str) -> bool:
    normalized = normalize_natural_text(text)
    subjects = (
        "soubor",
        "souboru",
        "priloha",
        "prilohu",
        "priloze",
        "dokument",
        "dokumentu",
        "tabulka",
        "tabulce",
        "tabulku",
        "excel",
        "excelu",
        "xlsx",
        "csv",
        "pdf",
        "word",
        "wordu",
        "docx",
        "obrazek",
        "obrazku",
        "screenshot",
        "screenshotu",
        "screen",
        "screenu",
    )
    return any(
        re.search(rf"\b{re.escape(subject)}\b", normalized) is not None
        for subject in subjects
    )


def has_explicit_file_output_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    patterns = (
        r"\bdo\s+(?:souboru|excelu|xlsx|csv|jsonu?|markdownu|txt)\b",
        r"\bjako\s+soubor\b",
        r"\bvrat\s+json\b",
        r"\budelej(?:\s+z\s+toho)?\s+(?:report|checklist|prehled|soubor)\b",
        r"\bvytvor(?:\s+z\s+toho)?\s+(?:report|checklist|prehled|soubor)\b",
        r"\bpriprav(?:\s+mi)?\s+z\s+toho\s+soubor\b",
        r"\bpreved(?:\s+mi)?\s+to\s+do\s+souboru\b",
        r"\buloz\s+to\s+jako\s+soubor\b",
    )
    return any(re.search(pattern, normalized) is not None for pattern in patterns)


def is_meta_router_or_behavior_discussion(text: str) -> bool:
    normalized = normalize_natural_text(text)
    signals = (
        "testuju",
        "test",
        "zkousim",
        "naprogramovane uvazovani",
        "logika",
        "router",
        "rozhodovani",
        "chovani",
        "spravne",
        "spatne",
        "hledala soubor",
        "nehledala soubor",
        "nemela hledat soubor",
        "nemel hledat soubor",
        "proc jsi hledala soubor",
        "proc jsi hledal soubor",
        "file hunter",
    )
    return any(signal in normalized for signal in signals)


def has_explicit_file_action_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    if not has_explicit_file_subject(normalized):
        return False

    patterns = (
        r"\b(?:shrn|analyzuj|precti|vysvetli|zkontroluj|projdi|najdi|vytahni|vytez)\b.*\b(?:soubor|souboru|priloh[auye]?|dokument|dokumentu|tabulk[auye]?|excelu?|xlsx|csv|pdf|wordu?|docx|obrazk[ue]?|screenshotu?|screenu?)\b",
        r"\b(?:soubor|souboru|priloh[auye]?|dokument|dokumentu|tabulk[auye]?|excelu?|xlsx|csv|pdf|wordu?|docx|obrazk[ue]?|screenshotu?|screenu?)\b.*\b(?:shrn|analyzuj|precti|vysvetli|zkontroluj|projdi|najdi|vytahni|vytez)\b",
        r"\bco\s+(?:je|obsahuje)\b.*\b(?:soubor|souboru|priloh[auye]?|dokument|dokumentu|tabulk[auye]?|excelu?|xlsx|csv|pdf|wordu?|docx|obrazk[ue]?|screenshotu?|screenu?)\b",
        r"\b(?:v|ve|na)\s+(?:tom|te|teto)?\s*(?:souboru|priloze|dokumentu|tabulce|excelu|pdf|wordu|docx|obrazku|screenshotu|screenu)\b",
    )
    return any(re.search(pattern, normalized) is not None for pattern in patterns)


def has_structured_data_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    structured_output_patterns = (
        r"\bdo\s+(?:excelu|xlsx|csv|jsonu?)\b",
        r"\bvrat\s+json\b",
        r"\bdej(?:\s+\w+){0,4}\s+do\s+(?:excelu|xlsx|csv|jsonu?)\b",
    )
    if any(re.search(pattern, normalized) is not None for pattern in structured_output_patterns):
        return True

    return contains_natural_signal(
        normalized,
        (
            "vytahni radky",
            "vyber sloupce",
            "vytahni hodnoty",
            "strukturovana data",
            "dej to do tabulky",
            "vytez data",
            "vytez z toho data",
            "vytahni data",
            "vytahni z toho data",
            "vytahni jmena",
            "vytahni emaily",
            "jmena a emaily",
        ),
    )


def is_attachment_summary_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    return re.search(r"\b(?:shrn|precti)\b", normalized) is not None


def is_attachment_error_request(text: str) -> bool:
    normalized = normalize_natural_text(text)
    return any(
        phrase in normalized
        for phrase in (
            "chyba",
            "spatne",
            "problem",
            "najdi chybu",
            "co je tam spatne",
            "co je na tom spatne",
        )
    )


def get_attachment_context_question(text: str, attachment_kind: str) -> str:
    if attachment_kind == "image" and is_attachment_error_request(text):
        return "Podivej se na obrazek a rekni, jaka chyba je tam videt. Navrhni kratce dalsi postup."

    if attachment_kind == "document" and is_attachment_summary_request(text):
        return "Shrn tuto prilohu."

    if attachment_kind == "document" and is_attachment_error_request(text):
        return "Najdi v dokumentu mozne chyby nebo problemove casti a strucne je vysvetli."

    return "Analyzuj tuto prilohu a strucne popis, co obsahuje."


def decide_file_response_mode(text: str, extension: str | None = None) -> dict:
    normalized = normalize_natural_text(text)

    if contains_natural_signal(normalized, DIRECT_FILE_EDIT_SIGNALS):
        return {"mode": "unsupported_direct_edit"}

    if has_structured_data_request(normalized):
        return {
            "mode": "structured_data",
            "instruction": text.strip() or "Vytez ze souboru strukturovana data.",
            "output_format": detect_output_format(text, default="json"),
        }

    if has_explicit_file_output_request(normalized):
        output_format = detect_output_format(text, default="md")
        if output_format not in {"md", "txt"}:
            output_format = "md"

        return {
            "mode": "human_document",
            "instruction": text.strip() or "Vytvor z dokumentu prehledny Markdown vystup.",
            "output_format": output_format,
        }

    if contains_natural_signal(normalized, HUMAN_DOCUMENT_SIGNALS):
        output_format = detect_output_format(text, default="md")
        if output_format not in {"md", "txt"}:
            output_format = "md"

        return {
            "mode": "human_document",
            "instruction": text.strip() or "Zpracuj soubor do prehledneho dokumentu.",
            "output_format": output_format,
        }

    attachment_kind = "image" if extension in SUPPORTED_IMAGE_EXTENSIONS else "document"
    return {
        "mode": "chat_answer",
        "question": get_attachment_context_question(text, attachment_kind),
    }


def is_ambiguous_context_request(text: str) -> bool:
    if has_explicit_file_output_request(text) or has_explicit_file_action_request(text):
        return False

    normalized = normalize_natural_text(text)
    patterns = (
        r"^shrn\s+(?:to|toto|tomuhle|tohle)$",
        r"^vysvetli\s+(?:to|toto|tohle)$",
        r"^co\s+je\s+na\s+tom\s+spatne\??$",
        r"^co\s+je\s+tam\s+spatne\??$",
        r"^co\s+dal\??$",
        r"^udelej\s+s\s+tim\s+neco.*$",
        r"^priprav(?:\s+mi)?\s+to\s+nejak.*$",
        r"^prehod(?:\s+mi)?\s+to\s+do\s+lepsi\s+podoby.*$",
        r"^potrebuju\s+z\s+toho\s+neco\s+vytahnout.*$",
        r"^(?:koukni|mrkni)\s+na\s+to\s+a\s+neco\s+s\s+tim\s+udelej.*$",
    )
    return any(re.match(pattern, normalized) is not None for pattern in patterns)


def fallback_ai_file_intent() -> dict:
    return {
        "target": "conversation",
        "mode": "chat_answer",
        "output_format": None,
        "question": None,
        "instruction": None,
        "confidence": 0.0,
        "reason": "fallback",
    }


def validate_ai_file_intent(
    raw_intent,
    has_current_attachment: bool,
    has_last_file_context: bool,
) -> dict:
    if isinstance(raw_intent, str):
        try:
            intent = json.loads(raw_intent)
        except json.JSONDecodeError:
            return fallback_ai_file_intent()
    elif isinstance(raw_intent, dict):
        intent = raw_intent
    else:
        return fallback_ai_file_intent()

    allowed_targets = {"conversation", "current_attachment", "last_file_context", "none"}
    allowed_modes = {"chat_answer", "human_document", "structured_data", "unsupported_direct_edit"}
    allowed_formats = {"md", "txt", "json", "csv", "xlsx", None}

    target = intent.get("target")
    mode = intent.get("mode")
    output_format = intent.get("output_format")
    if output_format == "null":
        output_format = None
    confidence = intent.get("confidence")

    if target not in allowed_targets or mode not in allowed_modes:
        return fallback_ai_file_intent()
    if output_format not in allowed_formats:
        return fallback_ai_file_intent()
    if not isinstance(confidence, (int, float)):
        return fallback_ai_file_intent()

    confidence = max(0.0, min(float(confidence), 1.0))
    if confidence < 0.65:
        return fallback_ai_file_intent()

    if target == "current_attachment" and not has_current_attachment:
        return fallback_ai_file_intent()
    if target == "last_file_context" and not has_last_file_context:
        return fallback_ai_file_intent()

    if mode == "human_document":
        if output_format not in {"md", "txt", None}:
            return fallback_ai_file_intent()
        output_format = output_format or "md"
    elif mode == "structured_data":
        if output_format not in {"json", "csv", "md", "xlsx", None}:
            return fallback_ai_file_intent()
        output_format = output_format or "json"
    elif mode == "chat_answer":
        output_format = None
    elif mode == "unsupported_direct_edit":
        output_format = None

    question = intent.get("question")
    instruction = intent.get("instruction")
    reason = intent.get("reason")

    return {
        "target": target,
        "mode": mode,
        "output_format": output_format,
        "question": question if isinstance(question, str) and question.strip() else None,
        "instruction": instruction if isinstance(instruction, str) and instruction.strip() else None,
        "confidence": confidence,
        "reason": reason if isinstance(reason, str) else "",
    }
