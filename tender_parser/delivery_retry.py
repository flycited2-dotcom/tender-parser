"""Retry proven-undelivered Excel uploads, without collecting any tenders."""
import argparse
import json
from pathlib import Path
from tender_parser.env import load_env_file
from tender_parser.notifications import NotificationConfig, TelegramNotifier


def retry_pending(base_dir: Path) -> int:
    load_env_file(base_dir/'.env')
    load_env_file(base_dir/'.env.local')
    notifier = TelegramNotifier(NotificationConfig.from_env())
    failed = 0
    for pending in (base_dir/'exports').rglob('.telegram_pending/*.json'):
        try:
            item = json.loads(pending.read_text(encoding='utf8'))
            path = Path(item['path']).resolve()
            if not path.is_relative_to((base_dir/'exports').resolve()):
                raise ValueError('path outside exports')
            if item['chat_id'] != notifier.config.chat_id:
                raise ValueError('recipient changed')
            stat = path.stat()
            if (stat.st_size, stat.st_mtime_ns) != (item['size'], item['mtime_ns']):
                raise ValueError('report file changed since failed upload')
            result = notifier.send_document(path, caption=item['caption'])
            print(f'{path.name}: {result.status} {result.detail}')
            if result.status == 'sent':
                pending.unlink(missing_ok=True)
            else:
                failed += 1
        except (OSError, ValueError, KeyError):
            failed += 1
            print('Pending delivery could not be processed')
    return 2 if failed else 0


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--base-dir', type=Path, default=Path.cwd())
    raise SystemExit(retry_pending(parser.parse_args().base_dir.resolve()))
