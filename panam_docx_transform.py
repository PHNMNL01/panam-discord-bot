import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import panam_docx


DOCX_TRANSFORM_FALLBACK_MESSAGE = (
    "Tohle je moc volná úprava. Původní DOCX neupravuju a obsah si nedomýšlím. "
    "Umím bezpečně vytvořit nový DOCX třeba: opravit překlepy, zestručnit text, "
    "převést do formálního tónu, udělat strukturovanou verzi s nadpisy nebo vytvořit checklist."
)

SUPPORTED_OPERATION_SUMMARIES = {
    "proofread": "oprava překlepů a stylistiky",
    "summarize": "zestručnění textu",
    "formalize": "převod do formálního tónu",
    "simplify": "převod do jednoduššího a srozumitelnějšího tónu",
    "structure": "vytvoření strukturované verze s nadpisy",
    "checklist": "vytvoření checklistu z dokumentu",
    "clean": "vytvoření čisté verze bez zbytečných poznámek",
}

UNSAFE_DOCX_TRANSFORM_SIGNALS = (
    "vylepsi to podle sebe",
    "vylepsit podle sebe",
    "neco tam dopln",
    "dopln podle sebe",
    "udelej to lepsi",
    "vymysli chybejici casti",
    "neco vymysli",
    "dopis podle sebe",
    "domysli",
    "rozsir to o nove informace",
)

SENSITIVE_DOCX_TRANSFORM_SIGNALS = (
    "heslo",
    "password",
    "passwd",
    "token",
    "api key",
    "apikey",
    "secret",
    "hr data",
    "zakaznicka data",
    "zákaznická data",
    "citliva data",
    "citlivá data",
)


class DocxTransformUserError(Exception):
    pass


@dataclass(frozen=True)
class DocxTransformResult:
    output_path: Path
    operation_summary: str
    paragraph_count_before: int | None
    paragraph_count_after: int | None


def normalize_instruction(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(text or "").casefold())
    without_marks = "".join(
        char for char in normalized if not unicodedata.combining(char)
    )
    without_punctuation = re.sub(r"[^\w\s]+", " ", without_marks)
    return re.sub(r"\s+", " ", without_punctuation).strip()


def contains_sensitive_docx_transform_signal(text: str) -> bool:
    normalized = normalize_instruction(text)
    return any(signal in normalized for signal in SENSITIVE_DOCX_TRANSFORM_SIGNALS)


def _has_any(normalized: str, signals: tuple[str, ...]) -> bool:
    return any(signal in normalized for signal in signals)


def detect_docx_transform_operation(instruction: str) -> dict:
    normalized = normalize_instruction(instruction)
    if not normalized:
        raise DocxTransformUserError(DOCX_TRANSFORM_FALLBACK_MESSAGE)

    if _has_any(normalized, UNSAFE_DOCX_TRANSFORM_SIGNALS):
        raise DocxTransformUserError(DOCX_TRANSFORM_FALLBACK_MESSAGE)

    operation = None
    if re.search(r"\b(?:oprav|opravit|oprava)\b.*\b(?:preklepy|preklepu|stylistiku|gramatiku|pravopis|docx|word|dokument)\b", normalized):
        operation = "proofread"
    elif re.search(r"\b(?:uces|uceš)\b.*\b(?:text|dokument|docx|word)?\b", normalized):
        operation = "proofread"
    elif re.search(r"\b(?:zestrucni|zestrucneni|zkrac|zkrat|zkratit|udel[ae]j kratsi)\b", normalized):
        operation = "summarize"
    elif re.search(r"\b(?:formalni|formalniho|formalnim|formalne)\b", normalized):
        operation = "formalize"
    elif re.search(r"\b(?:jednodussi\w*|srozumitelnejsi\w*|jednoduseji|jednoduse|zjednodus\w*)\b", normalized):
        operation = "simplify"
    elif re.search(r"\b(?:strukturovan[ay]|nadpisy|s nadpisy)\b", normalized):
        operation = "structure"
    elif re.search(r"\b(?:checklist|kontrolni seznam|seznam ukolu)\b", normalized):
        operation = "checklist"
    elif re.search(r"\b(?:cist\w*|bez zbytecnych poznamek|odstran poznamky|smaz poznamky)\b", normalized):
        operation = "clean"

    if operation is None:
        raise DocxTransformUserError(DOCX_TRANSFORM_FALLBACK_MESSAGE)

    return {
        "operation": operation,
        "operation_summary": SUPPORTED_OPERATION_SUMMARIES[operation],
    }


def count_text_paragraphs(text: str) -> int:
    return len([line for line in str(text or "").splitlines() if line.strip()])


def create_docx_transform_from_text(
    source_text: str,
    transformed_text: str,
    output_path: Path,
    operation_summary: str,
) -> DocxTransformResult:
    panam_docx.create_docx_from_text(transformed_text, output_path)
    return DocxTransformResult(
        output_path=output_path,
        operation_summary=operation_summary or "provedena podporovaná DOCX transformace",
        paragraph_count_before=count_text_paragraphs(source_text),
        paragraph_count_after=count_text_paragraphs(transformed_text),
    )
