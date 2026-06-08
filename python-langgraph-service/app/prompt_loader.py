import functools
from pathlib import Path

BASE_PROMPT_DIR = Path(__file__).parent / "prompts"


@functools.lru_cache(maxsize=2)
def _read_prompt(name: str) -> str:
    """
    Return the contents of ``prompts/<name>.md``.
    The LRU cache ensures the file is read only once per process.
    """
    path = BASE_PROMPT_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")
