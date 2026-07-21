#!/usr/bin/env python3
"""Render aura-configs/*/config.yaml from their .template files + .env.

AURA has no environment-variable support in its config.yaml, so secrets
can't stay out of the deployed file — instead the committed *.template
files hold ${VAR} placeholders and this script fills them in from the
gitignored .env, writing config.yaml next to each template. The rendered
config.yaml files are gitignored too; deploy them per the runbook.

Usage:  python render-aura-configs.py            # uses ./.env
        python render-aura-configs.py --env path/to/.env
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_env(path: Path) -> dict:
    if not path.is_file():
        sys.exit(f"error: {path} not found — copy .env.example to .env and fill it in")
    env = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", default=ROOT / ".env", type=Path)
    args = parser.parse_args()

    env = load_env(args.env)
    templates = sorted(ROOT.glob("aura-configs/*/config.yaml.template"))
    if not templates:
        sys.exit("error: no aura-configs/*/config.yaml.template files found")

    for template in templates:
        text = template.read_text(encoding="utf-8")
        missing = []

        def sub(match: re.Match) -> str:
            name = match.group(1)
            if name not in env or not env[name]:
                missing.append(name)
                return match.group(0)
            return env[name]

        rendered = re.sub(r"\$\{(\w+)\}", sub, text)
        if missing:
            sys.exit(f"error: {template.parent.name}: missing/empty in .env: {', '.join(missing)}")

        out = template.with_name("config.yaml")
        out.write_text(rendered, encoding="utf-8")
        print(f"rendered {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
