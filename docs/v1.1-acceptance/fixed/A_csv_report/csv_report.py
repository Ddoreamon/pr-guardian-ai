"""Build a per-region sales summary from a CSV export."""

import csv
import json
import sys


def summarize(rows, totals=None):
    """Return {region: total} for the given rows."""
    totals = {} if totals is None else totals
    for row in rows:
        region = row.get("region") or "unknown"
        amount = float(row["amount"] or 0)
        totals[region] = totals.get(region, 0) + amount
    return totals


def main(path):
    with open(path, newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        next(reader)  # skip the header row
        rows = list(reader)

    totals = summarize(rows)
    report = {region: round(value, 2) for region, value in totals.items()}

    print(f"Regions summarized: {len(report)}")
    print("summary written")
    with open("summary.json", "w", encoding="utf-8") as out:
        json.dump(report, out)
    return report


if __name__ == "__main__":
    main(sys.argv[1])
