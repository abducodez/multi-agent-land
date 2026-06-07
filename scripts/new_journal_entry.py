from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNAL_DIR = ROOT / "docs" / "journal"


TEMPLATE = """# {title}

Date: {timestamp}

## Built

- 

## Decisions

- 

## Learned

- 

## Next

- 
"""


def slugify(value: str) -> str:
    safe = "".join(ch.lower() if ch.isalnum() else "-" for ch in value).strip("-")
    return "-".join(part for part in safe.split("-") if part)[:64] or "entry"


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a dated build journal entry.")
    parser.add_argument("title", help="Short title for the journal entry")
    args = parser.parse_args()

    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now().astimezone()
    path = JOURNAL_DIR / f"{now:%Y-%m-%d}-{slugify(args.title)}.md"
    if path.exists():
        raise SystemExit(f"Entry already exists: {path}")
    path.write_text(TEMPLATE.format(title=args.title, timestamp=now.isoformat(timespec="seconds")), encoding="utf-8")
    print(path.relative_to(ROOT))


if __name__ == "__main__":
    main()

