"""Validate the P4 release evidence; no service, source or delivery I/O."""

import argparse
import json
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

ROOT = Path(__file__).resolve().parents[1]
REQUIRED = (
    "code_review",
    "new_head_ci",
    "isolated_transport_integration",
    "real_qq_reconnect_recovery",
    "offsite_encrypted_restore_latest_erasure",
    "source_soak",
    "grant_revocation_dependency_closure",
)


def validate(gates):
    if not isinstance(gates, dict):
        raise ValueError("P4 evidence must be an object")
    if gates.get("schema_version") != 1 or gates.get("taskbook") != "CC-NEXT-20261020-P4":
        raise ValueError("Unsupported P4 evidence schema")
    if type(gates.get("production_ready")) is not bool:
        raise ValueError("production_ready must be a boolean")
    blockers = [key for key in REQUIRED if gates.get(key) != "PASS"]
    for key in ("real_qq_private_messages_accepted", "real_diary_distinct_dates_reviewed"):
        count = gates.get(key)
        if type(count) is not int or count < 0:
            raise ValueError(key + " must be a nonnegative integer")
        if count < 20:
            blockers.append(key)
    counters = {
        "real_qq_acceptance": "real_qq_private_messages_accepted",
        "real_diary_reviews": "real_diary_distinct_dates_reviewed",
    }
    for key, counter in counters.items():
        # Synthetic test counts and simulated dates cannot unlock these gates.
        records = gates.get(key, [])
        if not isinstance(records, list) or any(
            not isinstance(row, dict)
            or row.get("kind") != "LIVE_HUMAN_ACCEPTED"
            or any(
                not isinstance(row.get(k), str) or not row[k].strip() for k in ("id", "evidence")
            )
            for row in records
        ):
            raise ValueError(key + " contains invalid human acceptance records")
        distinct = {row["id"] for row in records}
        if len(distinct) != len(records):
            raise ValueError(key + " contains duplicate records")
        if key == "real_diary_reviews":
            try:
                timezone = ZoneInfo(gates["diary_timezone"])
                dates = {date.fromisoformat(row["local_date"]) for row in records}
                if any(row.get("timezone") != timezone.key for row in records):
                    raise ValueError("Diary timezone mismatch")
                if any(day > datetime.now(timezone).date() for day in dates):
                    raise ValueError("Future diary acceptance")
            except (KeyError, TypeError, ZoneInfoNotFoundError) as error:
                raise ValueError("Invalid diary date/timezone") from error
            distinct = dates
        if gates[counter] != len(distinct):
            raise ValueError(counter + " differs from its acceptance records")
        if len(distinct) < 20:
            blockers.append(key)
    if gates.get("signoff") != "GRANTED":
        blockers.append("signoff")
    if gates["production_ready"] and blockers:
        raise ValueError("False production readiness: " + ", ".join(blockers))
    return blockers


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gates", type=Path, default=ROOT / "docs/design-v0.6/p4-gates.json")
    parser.add_argument("--require-production", action="store_true")
    args = parser.parse_args()
    try:
        gates = json.loads(args.gates.read_text("utf-8"))
        blockers = validate(gates)
    except (ValueError, OSError) as error:
        parser.exit(2, str(error) + "\n")
    print(
        json.dumps(
            {
                "valid": True,
                "production_ready": gates["production_ready"],
                "blocked_reasons": blockers,
            },
            ensure_ascii=False,
        )
    )
    if args.require_production and (blockers or not gates["production_ready"]):
        parser.exit(1, "P4 production gates remain blocked\n")


if __name__ == "__main__":
    main()
