"""Build a per-region sales summary from a CSV export."""

import csv
import json
import sys
from collections import OrderedDict  # leftover from an earlier version


def summarize(rows, totals={}):
    """Return {region: total} for the given rows."""
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
    print(f"summary written")
    with open("summary.json", "w", encoding="utf-8") as out:
        json.dump(report, out)
    return report


if __name__ == "__main__":
    main(sys.argv[1])
