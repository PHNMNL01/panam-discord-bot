import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class PanamTestDefinition:
    name: str
    label: str
    test_file: str


@dataclass
class PanamTestResult:
    name: str
    label: str
    command: list[str]
    returncode: int | None
    passed: bool
    duration_seconds: float
    stdout: str
    stderr: str
    timed_out: bool = False


TESTS: dict[str, PanamTestDefinition] = {
    "web_router": PanamTestDefinition(
        name="web_router",
        label="Panam Web Router",
        test_file="panam_web_smoke_test.py",
    ),
    "web_flask": PanamTestDefinition(
        name="web_flask",
        label="Panam Web Flask",
        test_file="panam_web_flask_smoke_test.py",
    ),
    "dock": PanamTestDefinition(
        name="dock",
        label="Panam Dock",
        test_file="panam_dock_smoke_test.py",
    ),
    "router": PanamTestDefinition(
        name="router",
        label="Panam Router",
        test_file="router_smoke_test.py",
    ),
    "docx_transform": PanamTestDefinition(
        name="docx_transform",
        label="DOCX Transform",
        test_file="scripts/docx_transform_smoke_test.py",
    ),
    "spreadsheet_transform": PanamTestDefinition(
        name="spreadsheet_transform",
        label="Spreadsheet Transform",
        test_file="scripts/spreadsheet_transform_smoke_test.py",
    ),
    "docx": PanamTestDefinition(
        name="docx",
        label="DOCX",
        test_file="scripts/docx_smoke_test.py",
    ),
}


def _get_test_definition(test_name: str) -> PanamTestDefinition:
    try:
        return TESTS[test_name]
    except KeyError as exc:
        allowed = ", ".join(sorted(TESTS))
        raise ValueError(f"Unknown smoke test '{test_name}'. Allowed tests: {allowed}") from exc


def get_available_tests() -> list[dict]:
    return [
        {
            "name": definition.name,
            "label": definition.label,
            "test_file": definition.test_file,
        }
        for definition in TESTS.values()
    ]


def run_smoke_test(test_name: str) -> PanamTestResult:
    definition = _get_test_definition(test_name)
    test_path = BASE_DIR / definition.test_file
    command = [sys.executable, definition.test_file]
    start_time = time.perf_counter()

    if not test_path.is_file():
        duration = time.perf_counter() - start_time
        return PanamTestResult(
            name=definition.name,
            label=definition.label,
            command=command,
            returncode=None,
            passed=False,
            duration_seconds=duration,
            stdout="",
            stderr=f"Smoke test file not found: {test_path}",
        )

    try:
        result = subprocess.run(
            command,
            cwd=str(BASE_DIR),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        duration = time.perf_counter() - start_time
        return PanamTestResult(
            name=definition.name,
            label=definition.label,
            command=command,
            returncode=None,
            passed=False,
            duration_seconds=duration,
            stdout=exc.stdout or "",
            stderr=exc.stderr or "Smoke test timed out after 60 seconds.",
            timed_out=True,
        )

    duration = time.perf_counter() - start_time
    return PanamTestResult(
        name=definition.name,
        label=definition.label,
        command=command,
        returncode=result.returncode,
        passed=result.returncode == 0,
        duration_seconds=duration,
        stdout=result.stdout,
        stderr=result.stderr,
    )


def run_all_smoke_tests() -> list[PanamTestResult]:
    return [run_smoke_test(definition.name) for definition in TESTS.values()]
