from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tender_parser")
    parser.add_argument("command", nargs="?", default="run", choices=["run"])
    parser.add_argument("--base-dir", default=".", help="Project directory for data and exports")
    parser.add_argument("--dry-run", action="store_true", help="Create directories and exit")
    return parser


def ensure_dirs(base_dir: Path) -> tuple[Path, Path]:
    data_dir = base_dir / "data"
    exports_dir = base_dir / "exports"
    data_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)
    return data_dir, exports_dir


def run(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    ensure_dirs(base_dir)
    if args.dry_run:
        return 0
    print("Парсер еще не подключен к источникам. Следующий шаг плана добавит логику.")
    return 0


def main() -> int:
    return run()
