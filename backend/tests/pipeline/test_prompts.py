from pathlib import Path

import pytest

from app.pipeline.prompts import load_prompt


def _write(directory: Path, name: str, body: str) -> None:
    (directory / name).write_text(body)


def test_latest_version_and_render(tmp_path: Path) -> None:
    _write(tmp_path, "demo.v1.md", "old\n=== USER ===\nold {{x}}")
    _write(tmp_path, "demo.v2.md", "You are helpful.\n=== USER ===\nHello {{name}}, {{name}}!")
    _write(tmp_path, "demo.v10.md", "newest\n=== USER ===\n{{a}}")
    assert load_prompt("demo", directory=tmp_path).version == "v10"
    p = load_prompt("demo", "v2", directory=tmp_path)
    assert p.system == "You are helpful."
    assert p.render(name="Ana") == "Hello Ana, Ana!"


def test_missing_variable_is_an_error(tmp_path: Path) -> None:
    _write(tmp_path, "demo.v1.md", "s\n=== USER ===\n{{a}} {{b}}")
    with pytest.raises(KeyError):
        load_prompt("demo", "v1", directory=tmp_path).render(a="1")


def test_missing_separator_and_unknown_prompt(tmp_path: Path) -> None:
    _write(tmp_path, "bad.v1.md", "no separator here")
    with pytest.raises(ValueError):
        load_prompt("bad", "v1", directory=tmp_path)
    with pytest.raises(FileNotFoundError):
        load_prompt("nothing", directory=tmp_path)
