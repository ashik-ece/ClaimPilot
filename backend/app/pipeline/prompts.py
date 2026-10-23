"""Versioned prompt registry.

Prompts live in `app/pipeline/prompts/<name>.v<N>.md`. A file has a system section, a line
`=== USER ===`, then a user template with `{{variable}}` placeholders. Files are immutable
once released: change a prompt by adding the next version, so audit events and eval results
always point at the exact text that ran.
"""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).parent / "prompts"
_SPLIT = "=== USER ==="
_VAR = re.compile(r"\{\{(\w+)\}\}")


@dataclass(frozen=True)
class Prompt:
    name: str
    version: str
    system: str
    user_template: str

    @property
    def variables(self) -> set[str]:
        return set(_VAR.findall(self.user_template))

    def render(self, **values: str) -> str:
        missing = self.variables - values.keys()
        if missing:
            raise KeyError(
                f"prompt {self.name}@{self.version} missing variables: {sorted(missing)}"
            )
        return _VAR.sub(lambda m: str(values[m.group(1)]), self.user_template)


def _parse(name: str, version: str, text: str) -> Prompt:
    system, sep, user = text.partition(_SPLIT)
    if not sep:
        raise ValueError(f"prompt {name}@{version} is missing the {_SPLIT!r} separator")
    return Prompt(name, version, system.strip(), user.strip())


@cache
def load_prompt(name: str, version: str | None = None, *, directory: Path = PROMPT_DIR) -> Prompt:
    """Load a prompt; with no version, the highest-numbered one."""
    if version is None:
        found = sorted(
            (int(m.group(1)), p)
            for p in directory.glob(f"{name}.v*.md")
            if (m := re.fullmatch(rf"{re.escape(name)}\.v(\d+)\.md", p.name))
        )
        if not found:
            raise FileNotFoundError(f"no prompt named {name!r} in {directory}")
        number, path = found[-1]
        version = f"v{number}"
    else:
        path = directory / f"{name}.{version}.md"
    return _parse(name, version, path.read_text())
