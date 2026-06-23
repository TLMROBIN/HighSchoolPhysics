"""CLI for HighSchoolPhysics runtime readiness checks."""

import argparse
import json

from .runtime import CAPABILITY_DEFINITIONS, check_runtime_capabilities


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--capability",
        choices=[item["id"] for item in CAPABILITY_DEFINITIONS],
    )
    parser.add_argument("--smoke", action="store_true")
    args = parser.parse_args(argv)
    definitions = CAPABILITY_DEFINITIONS
    if args.capability:
        definitions = [
            item for item in definitions if item["id"] == args.capability
        ]
    capabilities = check_runtime_capabilities(definitions)
    payload = {
        "capabilities": capabilities,
        "smoke_requested": bool(args.smoke),
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for item in capabilities:
            print(
                "%s\t%s\t%s"
                % (item["capability_id"], item["status"], item["detail"])
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
