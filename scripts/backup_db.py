"""Consistent SQLite backup: python scripts/backup_db.py /data/bot.db /backup/bot.db."""
import argparse
import sqlite3
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path)
parser.add_argument('destination', type=Path)
args = parser.parse_args()
if not args.source.is_file() or args.destination.exists():
    parser.error('Source must exist; destination must be a new file.')
args.destination.parent.mkdir(parents=True, exist_ok=True)
with sqlite3.connect(f'file:{args.source.resolve()}?mode=ro', uri=True) as src:
    with sqlite3.connect(args.destination) as dst:
        src.backup(dst)
        assert dst.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
print('Backup verified:', args.destination)
