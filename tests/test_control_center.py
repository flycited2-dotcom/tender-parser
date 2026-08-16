from __future__ import annotations

import threading
from http.server import ThreadingHTTPServer
from pathlib import Path

import requests

from tender_parser.control_center import _handler_for
from tender_parser.tender_case import initialize_case


def test_control_center_renders_existing_case_and_accepts_upload(tmp_path: Path) -> None:
    case_dir = tmp_path / "cases" / "existing"
    initialize_case(case_dir, case_id="existing", title="Поставка МФУ")
    server = ThreadingHTTPServer(("127.0.0.1", 0), _handler_for(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    try:
        page = requests.get(url, timeout=5)
        assert page.status_code == 200
        assert "Тендерный агент" in page.text
        assert "Поставка МФУ" in page.text

        response = requests.post(
            f"{url}/api/cases",
            data={"tender_number": "0174500001126009999", "title": "Комплектующие", "law": "44-FZ"},
            files={"documents": ("ТЗ.txt", b"technical specification")},
            timeout=5,
        )
        assert response.status_code == 201
        created = response.json()["case_id"]
        assert created == "0174500001126009999"
        assert (tmp_path / "cases" / created / "documents" / "ТЗ.txt").exists()
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
