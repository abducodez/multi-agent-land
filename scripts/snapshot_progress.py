from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
JOURNAL_DIR = ROOT / "docs" / "journal"
BLOG_PATH = ROOT / "docs" / "blog" / "building-in-public.md"


def main() -> None:
    entries = sorted(path for path in JOURNAL_DIR.glob("*.md") if path.is_file())
    lines = [
        "# Building Multi-Agent Land in Public",
        "",
        "This living technical blog is generated from `docs/journal/`.",
        "",
        "## Throughline",
        "",
        "We are building a tiny multi-agent theater for the Thousand Token Wood hackathon: event-sourced, small-model friendly, Gradio-first, and intentionally whimsical.",
        "",
        "## Entries",
        "",
    ]
    if not entries:
        lines.append("No journal entries yet.")
    for path in entries:
        title = path.read_text(encoding="utf-8").splitlines()[0].removeprefix("# ").strip()
        rel = path.relative_to(ROOT)
        lines.append(f"- [{title}](../journal/{path.name})")
        lines.append(f"  Source: `{rel}`")
    BLOG_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(BLOG_PATH.relative_to(ROOT))


if __name__ == "__main__":
    main()

