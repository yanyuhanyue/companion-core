"""Project one trial-limits TOML into numeric fields for new window drafts."""

import argparse
import json
import tomllib
from pathlib import Path

from companion.trial_limits import TrialLimits


def load_limits(path):
    value = tomllib.loads(Path(path).read_text("utf-8"))
    if set(value) != {"trial_limits"}:
        raise ValueError("one trial_limits table required")
    return TrialLimits.model_validate(value["trial_limits"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("profile", type=Path)
    args = parser.parse_args()
    try:
        limits = load_limits(args.profile)
    except (OSError, ValueError):
        parser.exit(2, "INVALID_TRIAL_LIMITS\n")
    print(json.dumps(limits.project(), indent=2))


if __name__ == "__main__":
    main()
