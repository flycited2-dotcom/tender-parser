from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from tender_parser.documents import SUPPORTED_SUFFIXES, _read_document_text
from tender_parser.text import normalize_text


DEFAULT_MAX_DOCUMENT_CHARS = 80_000
MAX_SEARCH_RESULTS = 50
TEXT_CACHE_DIR = ".cache"


@dataclass(frozen=True)
class KnowledgeDocument:
    document_id: str
    title: str
    relative_path: str
    section: str
    suffix: str
    size_bytes: int
    modified_ns: int
    sha256: str
    text: str


class TenderKnowledgeBase:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    @classmethod
    def from_environment(cls) -> "TenderKnowledgeBase":
        configured = os.getenv("TENDER_KNOWLEDGE_BASE", "").strip()
        if configured:
            return cls(Path(configured))
        return cls(Path(__file__).resolve().parents[1] / "knowledge_base")

    def status(self) -> dict[str, object]:
        documents = self.list_documents()
        sections: dict[str, int] = {}
        for document in documents:
            sections[document.section] = sections.get(document.section, 0) + 1
        return {
            "root": str(self.root),
            "exists": self.root.exists(),
            "document_count": len(documents),
            "sections": sections,
            "supported_suffixes": sorted(SUPPORTED_SUFFIXES),
        }

    def list_documents(self, section: str = "") -> list[KnowledgeDocument]:
        normalized_section = self._normalize_section(section)
        if not self.root.exists():
            return []
        documents: list[KnowledgeDocument] = []
        for path in sorted(self.root.rglob("*")):
            relative_path = path.relative_to(self.root)
            if TEXT_CACHE_DIR in relative_path.parts:
                continue
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            relative = relative_path.as_posix()
            if normalized_section and not relative.lower().startswith(normalized_section.lower().rstrip("/") + "/"):
                continue
            stat = path.stat()
            text = _cached_document_text(str(path), stat.st_mtime_ns, stat.st_size, str(self.root))
            documents.append(
                KnowledgeDocument(
                    document_id=_document_id(relative),
                    title=path.stem,
                    relative_path=relative,
                    section=relative.split("/", 1)[0] if "/" in relative else "root",
                    suffix=path.suffix.lower(),
                    size_bytes=stat.st_size,
                    modified_ns=stat.st_mtime_ns,
                    sha256=_sha256(path),
                    text=text,
                )
            )
        return documents

    def search(self, query: str, *, section: str = "", limit: int = 10) -> list[dict[str, object]]:
        normalized_query = normalize_text(query)
        if not normalized_query:
            raise ValueError("Поисковый запрос не может быть пустым")
        safe_limit = max(1, min(int(limit), MAX_SEARCH_RESULTS))
        tokens = [token for token in normalized_query.split() if len(token) >= 2]
        results: list[tuple[int, KnowledgeDocument, str]] = []
        for document in self.list_documents(section):
            searchable = normalize_text(f"{document.title} {document.relative_path} {document.text}")
            phrase_hits = searchable.count(normalized_query)
            token_hits = sum(searchable.count(token) for token in tokens)
            if phrase_hits == 0 and token_hits == 0:
                continue
            score = phrase_hits * 100 + token_hits
            results.append((score, document, _snippet(document.text, tokens or [normalized_query])))
        results.sort(key=lambda value: (-value[0], value[1].relative_path.lower()))
        return [
            {
                "id": document.document_id,
                "title": document.title,
                "path": document.relative_path,
                "section": document.section,
                "score": score,
                "snippet": snippet,
                "sha256": document.sha256,
            }
            for score, document, snippet in results[:safe_limit]
        ]

    def fetch(self, document_id: str, *, max_chars: int = DEFAULT_MAX_DOCUMENT_CHARS) -> dict[str, object]:
        safe_max_chars = max(1_000, min(int(max_chars), 500_000))
        for document in self.list_documents():
            if document.document_id != document_id:
                continue
            text = document.text
            return {
                "id": document.document_id,
                "title": document.title,
                "path": document.relative_path,
                "section": document.section,
                "suffix": document.suffix,
                "size_bytes": document.size_bytes,
                "sha256": document.sha256,
                "text": text[:safe_max_chars],
                "truncated": len(text) > safe_max_chars,
                "total_chars": len(text),
            }
        raise FileNotFoundError(f"Документ не найден: {document_id}")

    def _normalize_section(self, section: str) -> str:
        value = section.strip().replace("\\", "/").strip("/")
        if not value:
            return ""
        candidate = (self.root / value).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("Раздел выходит за пределы базы знаний") from exc
        return value


@lru_cache(maxsize=512)
def _cached_document_text(path: str, modified_ns: int, size_bytes: int, root: str) -> str:
    cache_key = hashlib.sha256(
        f"{Path(path).resolve()}\0{modified_ns}\0{size_bytes}".encode("utf-8")
    ).hexdigest()
    cache_path = Path(root) / TEXT_CACHE_DIR / "text" / f"{cache_key}.txt"
    try:
        if cache_path.is_file():
            return cache_path.read_text(encoding="utf-8")
    except OSError:
        pass

    try:
        text = _read_document_text(Path(path))
    except Exception as exc:
        text = f"[Не удалось извлечь текст: {exc.__class__.__name__}]"

    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = cache_path.with_name(f"{cache_path.name}.{os.getpid()}.tmp")
        temporary.write_text(text, encoding="utf-8")
        temporary.replace(cache_path)
    except OSError:
        pass
    return text


def _document_id(relative_path: str) -> str:
    digest = hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]
    return f"kb-{digest}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snippet(text: str, tokens: list[str], radius: int = 240) -> str:
    normalized = normalize_text(text)
    positions = [normalized.find(token) for token in tokens]
    positions = [position for position in positions if position >= 0]
    if not positions:
        return " ".join(text.split())[: radius * 2]
    position = min(positions)
    start = max(0, position - radius)
    end = min(len(text), position + radius)
    return " ".join(text[start:end].split())
