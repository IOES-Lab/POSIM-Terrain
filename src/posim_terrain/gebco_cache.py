"""Index GEBCO subset metadata; callers still verify candidate data and axes."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import urllib.parse
import uuid


def _window(url):
    if not isinstance(url, str) or '.ascii?' not in url:
        return (None,) * 5
    source = url.split('.ascii?', 1)[0]
    query = urllib.parse.unquote(urllib.parse.urlsplit(url).query)
    match = re.fullmatch(r'elevation\[(\d+):1:(\d+)\]\[(\d+):1:(\d+)\]', query)
    if match:
        a, b, c, d = map(int, match.groups())
        if 0 <= a <= b < 43200 and 0 <= c <= d < 86400:
            return source, a, b, c, d
    return (None,) * 5


def _lookup(paths, index, source, window):
    with closing(sqlite3.connect(index, timeout=5)) as db:
        db.execute('CREATE TABLE IF NOT EXISTS metadata '
                   '(name TEXT PRIMARY KEY, bytes INTEGER, modified_ns INTEGER, '
                   'source TEXT, row_start INTEGER, row_end INTEGER, col_start INTEGER, col_end INTEGER)')
        db.commit()
        known = {name: (size, stamp) for name, size, stamp in db.execute(
            'SELECT name, bytes, modified_ns FROM metadata')}
        updates = []
        names = set()
        for path in paths:
            names.add(path.name)
            stat = path.stat()
            signature = stat.st_size, stat.st_mtime_ns
            if known.get(path.name) == signature:
                continue
            try:
                value = json.loads(path.read_text())
                bounds = _window(value.get('url') if isinstance(value, dict) else None)
            except (ValueError, OSError):
                bounds = (None,) * 5
            updates.append((path.name, *signature, *bounds))
        with db:
            db.executemany('INSERT INTO metadata VALUES (?,?,?,?,?,?,?,?) '
                           'ON CONFLICT(name) DO UPDATE SET bytes=excluded.bytes, '
                           'modified_ns=excluded.modified_ns, source=excluded.source, '
                           'row_start=excluded.row_start, row_end=excluded.row_end, '
                           'col_start=excluded.col_start, col_end=excluded.col_end', updates)
            db.executemany('DELETE FROM metadata WHERE name=?',
                           [(name,) for name in known.keys() - names])
        a, b, c, d = map(int, window)
        matches = {row[0] for row in db.execute(
            'SELECT name FROM metadata WHERE source=? AND row_start<=? AND row_end>=? '
            'AND col_start<=? AND col_end>=?', (source, a, b, c, d))}
    # Preserve directory order for the caller's existing area/tie ordering.
    # Only names from the current directory listing can become candidates.
    return [path for path in paths if path.name in matches]


def metadata_candidates(root, source, window, *, index_path=None):
    """Refresh changed sidecars and return potential covering subsets.

    The derived index stores URL bounds and metadata file size/mtime. It stores
    no samples, hashes or approvals. The caller re-reads selected metadata and
    retains its existing source URL, raw SHA-256 and coordinate-axis checks.
    """
    root = Path(root)
    paths = list(root.glob('gebco-2026-*.ascii.json'))
    index = Path(index_path) if index_path is not None else root / 'gebco-cache-index-v1.sqlite'
    if index.is_symlink():
        return paths
    try:
        return _lookup(paths, index, source, window)
    except sqlite3.DatabaseError as error:
        # An invalid derived index can be rebuilt without replacing source data.
        # Leave a busy database/journal to its owner and use the verified scan.
        if (getattr(error, 'sqlite_errorcode', None) in (getattr(sqlite3, 'SQLITE_CORRUPT', 11), getattr(sqlite3, 'SQLITE_NOTADB', 26))
                and index.is_file() and not any(Path(str(index) + suffix).exists()
                                               for suffix in ('-journal', '-wal', '-shm'))):
            try:
                index.replace(index.with_name(index.name + '.rejected-' + uuid.uuid4().hex))
                return _lookup(paths, index, source, window)
            except (OSError, sqlite3.Error):
                pass
    except OSError:
        pass
    return paths
