"""Structural guarantees: the coach cannot reach the wallet, and runtime code never reads labels."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME_PACKAGES = ("rules", "wallet", "coach")


def imports_of(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_coach_has_no_path_to_the_wallet():
    for path in (ROOT / "coach").glob("*.py"):
        assert "wallet" not in {n.split(".")[0] for n in imports_of(path)}, path


def test_rules_engine_is_independent():
    for path in (ROOT / "rules").glob("*.py"):
        assert not {n.split(".")[0] for n in imports_of(path)} & {"wallet", "coach", "data", "eval"}, path


def test_runtime_never_references_labels():
    """Runtime code may not import the data/eval packages or name the label store or fields."""
    for pkg in RUNTIME_PACKAGES:
        for path in (ROOT / pkg).glob("*.py"):
            text = path.read_text(encoding="utf-8")
            assert "generated/labels" not in text and "is_scam" not in text, path
            assert not {n.split(".")[0] for n in imports_of(path)} & {"data", "eval"}, path
