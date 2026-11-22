import json
import sqlite3
from contextlib import closing, contextmanager
from pathlib import Path

from app.errors import TideError
from app.modules.datum.fingerprint import section_fingerprints

from .manifest import canonical


class Journal:
    def __init__(self, path):
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as connection:
            connection.execute('''CREATE TABLE IF NOT EXISTS journals (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, receipt TEXT NOT NULL,
                inputs TEXT NOT NULL, reports TEXT NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS datum_events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT, section TEXT NOT NULL,
                fingerprint TEXT NOT NULL, run_id TEXT NOT NULL, created TEXT NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS lineage (
                run_id TEXT PRIMARY KEY, parent_run_id TEXT NOT NULL)''')
            connection.execute('''CREATE TABLE IF NOT EXISTS permits (
                id TEXT PRIMARY KEY, created TEXT NOT NULL, run_id TEXT NOT NULL,
                vessel TEXT NOT NULL, body TEXT NOT NULL)''')

    @contextmanager
    def connection(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            yield connection

    def publish(self, receipt, sources, reports):
        with self.connection() as connection:
            cursor = connection.execute('INSERT INTO journals VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING', (
                receipt['run_id'], receipt['started_at'], canonical(receipt), canonical(sources), canonical(reports)))
            created = cursor.rowcount == 1
            if created:
                self._register_datum(connection, receipt, reports)
            return created

    @staticmethod
    def _register_datum(connection, receipt, reports):
        """记录断面基准的每次实际变更，供凭据追溯与失效判断。

        只让不早于当前版本的新归档推进基准；补录旧资料不得把基准回退。
        """
        for section, fingerprint in sorted(section_fingerprints(reports['datum']['stations']).items()):
            row = connection.execute('''SELECT fingerprint, created FROM datum_events
                WHERE section=? ORDER BY seq DESC LIMIT 1''', (section,)).fetchone()
            if row is not None and row[0] == fingerprint:
                continue
            if row is not None and receipt['started_at'] < row[1]:
                continue
            connection.execute('INSERT INTO datum_events (section, fingerprint, run_id, created) VALUES (?, ?, ?, ?)',
                               (section, fingerprint, receipt['run_id'], receipt['started_at']))

    def latest_datum(self):
        with self.connection() as connection:
            rows = connection.execute('''SELECT section, fingerprint, run_id, created, seq FROM datum_events
                WHERE seq IN (SELECT MAX(seq) FROM datum_events GROUP BY section)''').fetchall()
        return {section: {'fingerprint': fingerprint, 'run_id': run_id, 'created': created, 'seq': seq}
                for section, fingerprint, run_id, created, seq in rows}

    def stale_sections(self, used):
        """used 为凭据签发时的断面指纹与版本序号；返回已因基准更新而失效的断面。"""
        latest = self.latest_datum()
        stale = {}
        for section, pinned in used.items():
            current = latest.get(section)
            if current is None:
                continue
            changed = (current['seq'] != pinned['seq'] if pinned.get('seq') is not None
                       else current['fingerprint'] != pinned['fingerprint'])
            if changed:
                stale[section] = {'permit_fingerprint': pinned['fingerprint'],
                                  'permit_seq': pinned.get('seq'),
                                  'current_fingerprint': current['fingerprint'],
                                  'current_run_id': current['run_id'],
                                  'current_created': current['created']}
        return stale

    def successors(self, run_id):
        with self.connection() as connection:
            rows = connection.execute('SELECT run_id FROM lineage WHERE parent_run_id=? ORDER BY run_id',
                                      (run_id,)).fetchall()
        return [row[0] for row in rows]

    def descendants(self, run_id):
        """同一补正链上所有后代簿，用于判定旧证据是否仍受支持。"""
        seen, queue = [], [run_id]
        while queue:
            for child in self.successors(queue.pop()):
                if child not in seen:
                    seen.append(child)
                    queue.append(child)
        return seen

    def publish_permit(self, body):
        with self.connection() as connection:
            cursor = connection.execute('INSERT INTO permits VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING',
                                        (body['permit_id'], body['issued_at'], body['run']['run_id'],
                                         body['vessel'], canonical(body)))
            return cursor.rowcount == 1

    def get_permit(self, permit_id):
        with self.connection() as connection:
            row = connection.execute('SELECT body FROM permits WHERE id=?', (permit_id,)).fetchone()
        if row is None:
            raise TideError('NO_PERMIT', '未找到这份放行凭据', 404)
        return json.loads(row[0])

    def permit_ids_for(self, run_id):
        with self.connection() as connection:
            rows = connection.execute('SELECT id FROM permits WHERE run_id=? ORDER BY created, id',
                                      (run_id,)).fetchall()
        return [row[0] for row in rows]

    def link(self, run_id, parent_run_id):
        with self.connection() as connection:
            row = connection.execute('SELECT parent_run_id FROM lineage WHERE run_id=?', (run_id,)).fetchone()
            if row is None:
                connection.execute('INSERT INTO lineage VALUES (?, ?)', (run_id, parent_run_id))
            elif row[0] != parent_run_id:
                raise TideError('PARENT_CHANGED', '补正来源与已归档关系不符', 409)

    def parent(self, run_id):
        with self.connection() as connection:
            row = connection.execute('SELECT parent_run_id FROM lineage WHERE run_id=?', (run_id,)).fetchone()
        return row[0] if row else None

    def get(self, key):
        with self.connection() as connection:
            row = connection.execute('SELECT receipt, inputs, reports FROM journals WHERE id=?', (key,)).fetchone()
        if row is None:
            raise TideError('NO_RUN', '未找到这份运行簿', 404)
        return tuple(json.loads(value) for value in row)

    def list(self):
        with self.connection() as connection:
            rows = connection.execute('SELECT receipt FROM journals ORDER BY created DESC, id LIMIT 40').fetchall()
        return [json.loads(row[0]) for row in rows]
