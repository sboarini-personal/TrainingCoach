"""Pure layers stay pure (ADR-002, ADR-008, docs/architecture.md section 2).

``coach.domain``, ``coach.metrics``, ``coach.planning`` and ``coach.policy`` may import only the
standard library and ``coach``; they never perform I/O, read the clock or use randomness, so
their results are deterministic and reproducible.
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "coach"
PURE_LAYERS = ("domain", "metrics", "planning", "policy")
FORBIDDEN_STDLIB = {
    "http", "io", "logging", "os", "pathlib", "random", "secrets", "shutil", "socket",
    "sqlite3", "subprocess", "tempfile", "threading", "time", "urllib",
}  # fmt: skip
FORBIDDEN_CALLS = {"now", "today", "utcnow"}
PURE_FILES = sorted(path for layer in PURE_LAYERS for path in (SRC / layer).rglob("*.py"))


def _imports(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            modules.add(node.module.split(".")[0])
    return modules


@pytest.mark.parametrize("path", PURE_FILES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_pure_layer_imports_only_stdlib_and_coach(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for module in _imports(tree):
        assert module == "coach" or module in sys.stdlib_module_names, (
            f"third-party import {module}"
        )
        assert module not in FORBIDDEN_STDLIB, f"I/O, clock or randomness module {module}"


@pytest.mark.parametrize("path", PURE_FILES, ids=lambda p: p.relative_to(SRC).as_posix())
def test_pure_layer_never_reads_the_clock(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    calls = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert not calls & FORBIDDEN_CALLS, f"clock read in {path.name}"
