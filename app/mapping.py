"""Loads the mapping file and turns it into resolved, ready-to-run sections."""

import importlib.util
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


class MappingError(RuntimeError):
    pass


@dataclass
class Block:
    key: str
    kind: str                      # one | many | value | static
    sql: Optional[str] = None
    source: str = "facts"
    value: Any = None
    note: Optional[str] = None


@dataclass
class Section:
    id: str
    title: str
    panel: str
    blocks: List[Block] = field(default_factory=list)


@dataclass
class Mapping:
    version: int
    settings: Dict[str, Any]
    sections: List[Section]

    def section(self, section_id: str) -> Optional[Section]:
        return next((s for s in self.sections if s.id == section_id), None)


def _load_raw_mapping(path: Path) -> Dict[str, Any]:
    """Import the mapping file as a Python module and pull out its MAPPING dict.

    Loaded by file path (not `import`), so mounting a different report_mapping.py
    over this path in an offline deployment is picked up with no code change --
    same as the CDR/IPDR query_templates.py modules elsewhere in this codebase.
    A fresh, unregistered module name is used each call so edits are always
    re-read rather than served from a cached import.
    """
    spec = importlib.util.spec_from_file_location(f"report_mapping_{uuid.uuid4().hex}", path)
    if spec is None or spec.loader is None:
        raise MappingError(f"could not load mapping module: {path}")
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise MappingError(f"mapping file '{path}' failed to load: {e}") from e

    raw = getattr(module, "MAPPING", None)
    if not isinstance(raw, dict):
        raise MappingError(f"mapping file '{path}' does not define a MAPPING dict")
    return raw


def load_mapping(path: Path) -> Mapping:
    if not path.exists():
        raise MappingError(f"mapping file not found: {path}")

    raw = _load_raw_mapping(path)

    sections: List[Section] = []
    for s in raw.get("sections") or []:
        blocks = []
        for b in s.get("blocks") or []:
            kind = b.get("kind", "many")
            sql = b.get("sql")
            if kind != "static" and not sql:
                raise MappingError(f"section '{s['id']}' block '{b['key']}' has no sql")
            blocks.append(Block(
                key=b["key"], kind=kind, sql=sql,
                source=b.get("source", "facts"), value=b.get("value"), note=b.get("note"),
            ))
        sections.append(Section(
            id=s["id"], title=s.get("title", s["id"]),
            panel=s.get("panel", "right"), blocks=blocks,
        ))

    if not sections:
        raise MappingError("mapping file defines no sections")

    return Mapping(
        version=int(raw.get("version", 1)),
        settings=raw.get("settings") or {}, sections=sections,
    )
