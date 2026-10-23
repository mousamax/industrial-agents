"""Getting partner documents (PDFs, drawings, spreadsheets as CSV, text) in front of an agent.

Two ways, pick per agent:
- `as_user_content(path)`: hand the file to the model directly. Claude reads PDFs and images
  natively, so don't write a PDF parser: `await agent.run(["Check this spec", as_user_content(p)])`.
- `data_files`: a toolset that lets the agent browse `data/` itself, for "look through the drop" tasks.
  `Agent(..., toolsets=[data_files])`
"""

from __future__ import annotations

import mimetypes
from pathlib import Path

from pydantic_ai import BinaryContent, FunctionToolset

from app.settings import get_settings

_TEXT_SUFFIXES = {".txt", ".md", ".csv", ".json", ".yaml", ".yml", ".xml", ".html"}


def as_user_content(path: str | Path) -> str | BinaryContent:
    path = Path(path)
    if path.suffix.lower() in _TEXT_SUFFIXES:
        return f'<document name="{path.name}">\n{path.read_text()}\n</document>'
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return BinaryContent(data=path.read_bytes(), media_type=media_type, identifier=path.name)


def _safe(relative: str) -> Path:
    root = get_settings().data_dir.resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise ValueError("path must stay inside data/")
    return path


data_files = FunctionToolset()


@data_files.tool_plain
def list_data_files(folder: str = ".") -> list[str]:
    """List files under data/ (recursive), relative paths."""
    root = get_settings().data_dir.resolve()
    return sorted(str(p.relative_to(root)) for p in _safe(folder).rglob("*") if p.is_file())


@data_files.tool_plain
def read_data_file(path: str, max_chars: int = 20_000) -> str:
    """Read a text file under data/ (path as returned by list_data_files)."""
    return _safe(path).read_text(errors="replace")[:max_chars]
