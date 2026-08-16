from pathlib import Path

import pytest

import tender_parser.knowledge_base as knowledge_module
from tender_parser.knowledge_base import TenderKnowledgeBase


def test_knowledge_base_lists_searches_and_fetches_documents(tmp_path: Path) -> None:
    root = tmp_path / "knowledge_base"
    legal = root / "legal" / "44-fz"
    legal.mkdir(parents=True)
    document = legal / "article-33.txt"
    document.write_text("Статья 33. Описание объекта закупки и характеристики товара.", encoding="utf-8")

    knowledge = TenderKnowledgeBase(root)

    status = knowledge.status()
    assert status["document_count"] == 1
    assert status["sections"] == {"legal": 1}

    results = knowledge.search("описание объекта")
    assert len(results) == 1
    assert results[0]["path"] == "legal/44-fz/article-33.txt"

    fetched = knowledge.fetch(str(results[0]["id"]))
    assert "Статья 33" in str(fetched["text"])
    assert fetched["truncated"] is False


def test_knowledge_base_limits_section_to_root(tmp_path: Path) -> None:
    knowledge = TenderKnowledgeBase(tmp_path / "knowledge_base")

    with pytest.raises(ValueError, match="за пределы"):
        knowledge.list_documents("../secret")


def test_knowledge_base_rejects_empty_search(tmp_path: Path) -> None:
    knowledge = TenderKnowledgeBase(tmp_path / "knowledge_base")

    with pytest.raises(ValueError, match="пустым"):
        knowledge.search("   ")


def test_knowledge_base_reuses_persistent_text_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "knowledge_base"
    root.mkdir()
    document = root / "large.pdf"
    document.write_bytes(b"not-a-real-pdf")
    calls = 0

    def fake_read(path: Path) -> str:
        nonlocal calls
        calls += 1
        return f"cached text from {path.name}"

    monkeypatch.setattr(knowledge_module, "_read_document_text", fake_read)
    knowledge_module._cached_document_text.cache_clear()
    assert TenderKnowledgeBase(root).status()["document_count"] == 1
    assert calls == 1

    knowledge_module._cached_document_text.cache_clear()
    assert TenderKnowledgeBase(root).status()["document_count"] == 1
    assert calls == 1
    assert not any(document.relative_path.startswith(".cache/") for document in TenderKnowledgeBase(root).list_documents())
