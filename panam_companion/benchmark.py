"""Summarize human timing metadata without accepting provider/queue proxies as audio."""
import argparse
import csv
import json
import math
from pathlib import Path
import statistics

IDS = [f"Q{i:02}" for i in range(1, 21)]


def summarize(rows):
    first = [row for row in rows if row.get("attempt") == "1"]
    if sorted(row.get("id", "") for row in first) != IDS:
        raise ValueError("Exactly one first-attempt row per preregistered ID is required")
    latencies, upper = [], []
    successes = 0
    failures = 0
    pending = 0
    unverified = 0
    for row in first:
        status = row.get("status")
        if status == "NOT_TESTED":
            pending += 1
        elif status == "FAIL":
            failures += 1
            latencies.append(math.inf)
            upper.append(math.inf)
        elif status in {"PASS", "NOT_VERIFIED"}:
            successes += status == "PASS"
            try:
                value = float(row.get("audible_seconds", ""))
                uncertainty = float(row.get("uncertainty_seconds", ""))
                if (not math.isfinite(value) or not math.isfinite(uncertainty) or value < 0 or
                        uncertainty < .3 or row.get("method") not in {"human_stopwatch", "human_monotonic_panel"} or status != "PASS"):
                    raise ValueError
                latencies.append(value)
                upper.append(value + uncertainty)
            except ValueError:
                unverified += 1
        else:
            raise ValueError("Invalid trial status")
    def metrics(values):
        if not values:
            return {"median": None, "p95": None, "max": None}
        values = sorted(values)
        result = {"median": statistics.median(values), "p95": values[math.ceil(.95 * len(values)) - 1],
                  "max": max(values)}
        return {key: value if math.isfinite(value) else "infinity (failed trial)" for key, value in result.items()}
    measured, conservative = metrics(latencies), metrics(upper)
    result = "NOT VERIFIED"
    if failures:
        result = "FAIL"
    elif not pending and not unverified:
        if measured["median"] > 3 or measured["p95"] > 8:
            result = "FAIL"
        elif conservative["median"] <= 3 and conservative["p95"] <= 8:
            result = "PASS (human-reported; not independently observed)"
    return {"status": result, "first_trials": 20, "successes": successes, "failures": failures,
            "not_tested": pending, "unverified_timing": unverified, "timed_or_failed_n": len(latencies),
            "retakes": len(rows) - 20, "metrics_seconds": measured,
            "uncertainty_upper_seconds": conservative, "p95_method": "nearest rank ceil(0.95*n)"}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    path = args.file.resolve()
    if not path.is_relative_to(root) or path.suffix != ".csv" or path.stat().st_size > 65536:
        parser.error("Use a timing CSV inside panam_companion, at most 64 KiB.")
    with path.open(encoding="utf-8-sig", newline="") as file:
        print(json.dumps(summarize(list(csv.DictReader(file))), ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
