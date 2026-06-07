from pathlib import Path


def test_journal_scripts_exist() -> None:
    root = Path(__file__).resolve().parents[1]

    assert (root / "scripts" / "new_journal_entry.py").exists()
    assert (root / "scripts" / "snapshot_progress.py").exists()
    assert (root / "docs" / "blog" / "building-in-public.md").exists()

