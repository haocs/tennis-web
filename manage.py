#!/usr/bin/env python3
"""Administrative state backups; no media or originals are copied."""
import argparse
import os
from pathlib import Path
import sqlite3


def backup(source, destination):
    source, destination=Path(source),Path(destination)
    if not source.is_file():
        raise ValueError('Website database does not exist')
    if destination.exists():
        raise ValueError('Refusing to overwrite an existing backup')
    destination.parent.mkdir(parents=True,exist_ok=True)
    try:
        with sqlite3.connect(f'{source.as_uri()}?mode=ro',uri=True) as original:
            with sqlite3.connect(destination) as target:
                original.backup(target)
                if target.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('Backup failed integrity check')
    except Exception:
        destination.unlink(missing_ok=True)
        raise


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('command',choices=['backup'])
    p.add_argument('destination')
    p.add_argument('--data-root',default=os.environ.get('TENNIS_DATA_ROOT','/data'))
    args=p.parse_args()
    backup((Path(args.data_root)/'website.sqlite3').resolve(),Path(args.destination).resolve())
    print('Website state backup created and verified')


if __name__=='__main__':main()
