PANAM_NAME = "panam"

BASIC_PANAM_EMPTY_RESPONSE = "Jsem tady. Co potřebuješ?"

CONTEXT_REFERENCES = {
    "to",
    "toto",
    "tom",
    "tenhle",
    "tohle",
    "tento",
    "tuhle",
    "ten soubor",
    "ten dokument",
    "ta priloha",
    "ta tabulka",
    "ten obrazek",
    "ten screenshot",
    "to pdf",
    "ten word",
    "ten docx",
    "ten excel",
    "ta xlsx",
    "to csv",
    "to txt",
    "ten markdown",
}

NOTE_ADD_TRIGGERS = (
    "přidej poznámku",
    "pridej poznamku",
    "ulož poznámku",
    "uloz poznamku",
    "zapamatuj si",
    "pamatuj si",
    "ulož si",
    "uloz si",
)
NOTE_LIST_TRIGGERS = (
    "ukaž poznámky",
    "ukaz poznamky",
)
NOTE_SEARCH_TRIGGERS = (
    "najdi poznámku",
    "najdi poznamku",
)

TODO_ADD_TRIGGERS = (
    "přidej todo",
    "pridej todo",
    "přidej úkol",
    "pridej ukol",
)
TODO_LIST_TRIGGERS = (
    "ukaž todo",
    "ukaz todo",
    "ukaž úkoly",
    "ukaz ukoly",
)

SUMMARY_TRIGGERS = (
    "shrň mi",
    "shrn mi",
    "shrň",
    "shrn",
    "udělej summary",
    "udelej summary",
)
SUMMARY_CONTEXT_TRIGGERS = (
    "shrň to",
    "shrn to",
    "shrň toto",
    "shrn toto",
)

OPINION_TRIGGERS = (
    "řekni mi",
    "rekni mi",
    "řekni",
    "rekni",
    "odpověz",
    "odpovez",
    "co si myslíš o",
    "co si myslis o",
    "co si myslí panam o",
    "co si mysli panam o",
)
OPINION_CONTEXT_TRIGGERS = (
    "co si o tom myslíš",
    "co si o tom myslis",
    "co si o tom myslí panam",
    "co si o tom mysli panam",
)

IMAGE_ANALYZE_TRIGGERS = (
    "analyzuj obrazek",
    "analyzuj ten obrazek",
    "koukni na obrazek",
    "koukni na tohle",
    "podivej se na obrazek",
    "podivej se na tohle",
    "co je na obrazku",
    "co je na tom obrazku",
    "co vidis",
    "co tam vidis",
    "popis obrazek",
    "popis ten obrazek",
    "vysvetli obrazek",
    "vysvetli ten screenshot",
    "co je na screenshotu",
    "co je na screenu",
    "co je tady za chybu",
    "co je tam za chybu",
)

GENERIC_IMAGE_ANALYZE_TRIGGERS = (
    "analyzuj obrazek",
    "analyzuj ten obrazek",
    "koukni na obrazek",
    "koukni na tohle",
    "podivej se na obrazek",
    "podivej se na tohle",
    "co je na obrazku",
    "co je na tom obrazku",
    "co vidis",
    "co tam vidis",
    "popis obrazek",
    "popis ten obrazek",
    "vysvetli obrazek",
    "vysvetli ten screenshot",
    "co je na screenshotu",
    "co je na screenu",
)

ATTACHMENT_ANALYZE_TRIGGERS = (
    "soubor",
    "souboru",
    "prilohu",
    "priloze",
)
DOCUMENT_ANALYZE_TRIGGERS = (
    "dokument",
    "dokumentu",
)
PDF_ANALYZE_TRIGGERS = ("pdf",)
WORD_ANALYZE_TRIGGERS = (
    "word",
    "wordu",
    "docx",
)
EXCEL_ANALYZE_TRIGGERS = (
    "excel",
    "excelu",
    "xlsx",
    "tabulku",
    "tabulce",
    "tabulka",
)
CSV_ANALYZE_TRIGGERS = ("csv",)
TEXT_FILE_ANALYZE_TRIGGERS = (
    "txt",
    "markdown",
)

ATTACHMENT_ANALYZE_SUBJECTS = (
    ATTACHMENT_ANALYZE_TRIGGERS
    + DOCUMENT_ANALYZE_TRIGGERS
    + PDF_ANALYZE_TRIGGERS
    + WORD_ANALYZE_TRIGGERS
    + EXCEL_ANALYZE_TRIGGERS
    + CSV_ANALYZE_TRIGGERS
    + TEXT_FILE_ANALYZE_TRIGGERS
)

HELP_PATTERNS = (
    r"^(?:help|pomoc|nápověda|prikazy|příkazy)\s*$",
    r"^co\s+(?:umíš|umis|dokážeš|dokazes)\s*\??$",
    r"^(?:ukaž|ukaz)\s+(?:příkazy|prikazy)\s*$",
)
NOTE_ADD_PREVIOUS_PATTERN = r"^zapamatuj\s+si\s+to\s*$"
OPINION_CONTEXT_PATTERNS = (
    r"^co\s+si\s+o\s+tom\s+(?:myslíš|myslis)\s*\??$",
    r"^co\s+si\s+o\s+tom\s+(?:myslí|mysli)\s+panam\s*\??$",
)
SUMMARY_CONTEXT_PATTERN = r"^(?:shrň|shrn)\s+(?:to|toto)\s*$"

NATURAL_INTENT_PATTERNS = (
    ("note_add", r"^(?:přidej|pridej)\s+(?:poznámku|poznamku)\s+(.+)$"),
    ("note_add", r"^(?:ulož|uloz)\s+(?:poznámku|poznamku)\s+(.+)$"),
    ("note_add", r"^zapamatuj\s+si\s+(.+)$"),
    ("note_add", r"^pamatuj\s+si\s+(.+)$"),
    ("note_add", r"^(?:ulož|uloz)\s+si\s+(.+)$"),
    ("todo_add", r"^(?:přidej|pridej)\s+todo\s+(.+)$"),
    ("todo_add", r"^(?:přidej|pridej)\s+(?:úkol|ukol)\s+(.+)$"),
    ("note_search", r"^najdi\s+(?:poznámku|poznamku)\s+(.+)$"),
    ("ask", r"^(?:řekni|rekni)\s+mi\s+(.+)$"),
    ("ask", r"^(?:řekni|rekni)\s+(.+)$"),
    ("ask", r"^(?:odpověz|odpovez)\s+(.+)$"),
    ("ask", r"^co\s+si\s+(?:myslíš|myslis)\s+o\s+(.+)$"),
    ("ask", r"^co\s+si\s+(?:myslí|mysli)\s+panam\s+o\s+(.+)$"),
    ("talk", r"^talk\s+(.+)$"),
    ("talk", r"^pokec\s+(.+)$"),
    ("talk", r"^(?:pokecáme|pokecame)\s+o\s+(.+)$"),
    ("talk", r"^pokecej\s+o\s+(.+)$"),
    ("talk", r"^co\s+si\s+fakt\s+(?:myslíš|myslis)\s+o\s+(.+)$"),
    ("summary", r"^(?:shrň|shrn)\s+mi\s+(.+)$"),
    ("summary", r"^(?:shrň|shrn)\s+(.+)$"),
    ("summary", r"^(?:udělej|udelej)\s+summary\s+(.+)$"),
)
NOTE_LIST_PATTERN = r"^(?:ukaž|ukaz)\s+(?:poznámky|poznamky)\s*$"
TODO_LIST_PATTERN = r"^(?:ukaž|ukaz)\s+(?:todo|úkoly|ukoly)\s*$"
