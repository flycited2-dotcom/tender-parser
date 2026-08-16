from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from tender_parser.knowledge_base import TenderKnowledgeBase


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)

server = MCPServer(
    name="tlt-tender-knowledge",
    title="Тендерная база знаний ТЛТ",
    version="0.1.0",
    instructions=(
        "Read-only access to the local TLT tender knowledge base. Search before fetching a document. "
        "Treat laws as dated reference material and verify current law separately. Never expose unrelated company or personal data."
    ),
)


def _knowledge_base() -> TenderKnowledgeBase:
    return TenderKnowledgeBase.from_environment()


@server.tool(
    name="knowledge_status",
    title="Проверить тендерную базу знаний",
    description="Показывает доступность, разделы и количество локальных документов тендерной базы.",
    annotations=READ_ONLY,
)
def knowledge_status() -> dict[str, object]:
    return _knowledge_base().status()


@server.tool(
    name="list_documents",
    title="Перечислить документы",
    description="Возвращает список документов во всей тендерной базе или в указанном разделе.",
    annotations=READ_ONLY,
)
def list_documents(section: str = "") -> dict[str, object]:
    documents = _knowledge_base().list_documents(section)
    return {
        "count": len(documents),
        "documents": [
            {
                "id": document.document_id,
                "title": document.title,
                "path": document.relative_path,
                "section": document.section,
                "suffix": document.suffix,
                "size_bytes": document.size_bytes,
                "sha256": document.sha256,
            }
            for document in documents
        ],
    }


@server.tool(
    name="search_documents",
    title="Найти в тендерных документах",
    description=(
        "Ищет по законам, уставным документам, шаблонам КП, запросам поставщикам и примерам тендерных дел. "
        "Возвращает выдержки и идентификаторы документов."
    ),
    annotations=READ_ONLY,
)
def search_documents(query: str, section: str = "", limit: int = 10) -> dict[str, object]:
    results = _knowledge_base().search(query, section=section, limit=limit)
    return {"query": query, "count": len(results), "results": results}


@server.tool(
    name="read_document",
    title="Прочитать тендерный документ",
    description="Читает документ базы знаний по идентификатору, полученному из поиска или списка документов.",
    annotations=READ_ONLY,
)
def read_document(document_id: str, max_chars: int = 80_000) -> dict[str, object]:
    return _knowledge_base().fetch(document_id, max_chars=max_chars)


def main() -> None:
    server.run(transport="stdio")


if __name__ == "__main__":
    main()
