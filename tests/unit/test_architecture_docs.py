from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_readme_architecture_diagram_documents_journal_and_tool_evidence() -> None:
    readme = (ROOT / "README.md").read_text()
    architecture = (ROOT / "docs" / "assets" / "architecture-en.svg").read_text()

    assert "docs/assets/architecture-en.svg" in readme
    assert "typed run journal" in architecture
    assert "tool evidence" in architecture
