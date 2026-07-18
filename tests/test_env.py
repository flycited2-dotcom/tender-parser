import os
from pathlib import Path

from tender_parser.env import get_env_status, load_env_file


def test_load_env_file_tolerates_utf8_bom(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("BOM_TEST_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_bytes("BOM_TEST_KEY=value\n".encode("utf-8-sig"))

    loaded = load_env_file(env_file)

    assert loaded == ["BOM_TEST_KEY"]
    import os

    assert os.environ.pop("BOM_TEST_KEY") == "value"


def test_load_env_file_reads_simple_values_comments_and_quotes(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text(
        """
# local tender parser settings
EAT_API_TOKEN="secret-token"
EAT_EXT_SYSTEM='EXT-CRM'
EAT_MAX_DETAILS=25
BAD_LINE
""".strip(),
        encoding="utf-8",
    )
    for key in ["EAT_API_TOKEN", "EAT_EXT_SYSTEM", "EAT_MAX_DETAILS"]:
        monkeypatch.delenv(key, raising=False)

    loaded = load_env_file(env_path)

    assert loaded == ["EAT_API_TOKEN", "EAT_EXT_SYSTEM", "EAT_MAX_DETAILS"]
    assert os.environ["EAT_API_TOKEN"] == "secret-token"
    assert os.environ["EAT_EXT_SYSTEM"] == "EXT-CRM"
    assert os.environ["EAT_MAX_DETAILS"] == "25"


def test_load_env_file_does_not_override_existing_environment(tmp_path: Path, monkeypatch) -> None:
    env_path = tmp_path / ".env"
    env_path.write_text("EAT_API_TOKEN=from-file", encoding="utf-8")
    monkeypatch.setenv("EAT_API_TOKEN", "from-os")

    loaded = load_env_file(env_path)

    assert loaded == []
    assert os.environ["EAT_API_TOKEN"] == "from-os"


def test_load_env_file_missing_file_is_noop(tmp_path: Path) -> None:
    assert load_env_file(tmp_path / ".env") == []


def test_get_env_status_reports_presence_without_values(monkeypatch) -> None:
    monkeypatch.setenv("EAT_API_TOKEN", "secret-token")
    monkeypatch.delenv("EAT_EXT_SYSTEM", raising=False)

    assert get_env_status(["EAT_API_TOKEN", "EAT_EXT_SYSTEM"]) == {
        "EAT_API_TOKEN": True,
        "EAT_EXT_SYSTEM": False,
    }
