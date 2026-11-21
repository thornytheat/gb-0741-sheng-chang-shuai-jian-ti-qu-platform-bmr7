import json

from app.errors import Problem

COMPARISON_META = 'id, before_run_id, after_run_id, reference_runs, created_at, elapsed_ms, digest'
PUBLICATION_META = 'seq, id, comparison_id, comparison_digest, scope, created_at, digest'


def _comparison_manifest(row):
    values = {key: row[key] for key in COMPARISON_META.split(', ')}
    values['references'] = json.loads(values.pop('reference_runs'))
    return values


def insert_comparison(connection, manifest, evidence):
    cursor = connection.execute(
        f'INSERT INTO comparisons ({COMPARISON_META}, evidence) VALUES (?, ?, ?, ?, ?, ?, ?, ?) '
        'ON CONFLICT(id) DO NOTHING',
        (manifest['id'], manifest['before_run_id'], manifest['after_run_id'],
         json.dumps(manifest['references'], sort_keys=True, ensure_ascii=False, separators=(',', ':')),
         manifest['created_at'], manifest['elapsed_ms'], manifest['digest'], evidence))
    return cursor.rowcount == 1


def read_comparison(connection, comparison_id):
    row = connection.execute(f'SELECT {COMPARISON_META}, evidence FROM comparisons WHERE id=?',
                             (comparison_id,)).fetchone()
    if row is None:
        raise Problem('COMPARISON_MISSING', '改造差异不存在', 404)
    return {'manifest': _comparison_manifest(row), 'result': json.loads(row['evidence'])}


def comparisons(connection, limit=50):
    rows = connection.execute(f'SELECT {COMPARISON_META} FROM comparisons ORDER BY created_at DESC, id LIMIT ?',
                              (limit,))
    return [_comparison_manifest(row) for row in rows]


def insert_publication(connection, publication_id, digest, comparison_id, comparison_digest, scope,
                       created_at, evidence):
    cursor = connection.execute(
        'INSERT INTO publications (id, comparison_id, comparison_digest, scope, created_at, digest, evidence) '
        'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO NOTHING',
        (publication_id, comparison_id, comparison_digest, scope, created_at, digest, evidence))
    return cursor.rowcount == 1


def _publication_manifest(row):
    return {key: row[key] for key in PUBLICATION_META.split(', ')}


def read_publication(connection, publication_id):
    row = connection.execute(f'SELECT {PUBLICATION_META}, evidence FROM publications WHERE id=?',
                             (publication_id,)).fetchone()
    if row is None:
        raise Problem('PUBLICATION_MISSING', '发布快照不存在', 404)
    return {'manifest': _publication_manifest(row), 'publication': json.loads(row['evidence'])}


def latest_publication(connection):
    row = connection.execute(f'SELECT {PUBLICATION_META}, evidence FROM publications '
                             'ORDER BY seq DESC LIMIT 1').fetchone()
    return None if row is None else {'manifest': _publication_manifest(row),
                                     'publication': json.loads(row['evidence'])}


def publication_history(connection, limit=50):
    rows = connection.execute(f'SELECT {PUBLICATION_META}, evidence FROM publications ORDER BY seq DESC LIMIT ?',
                              (limit,))
    return [{**_publication_manifest(row), 'summary': json.loads(row['evidence'])['summary']} for row in rows]


def declare_basis(connection, run_id, basis, note, declared_at):
    connection.execute(
        'INSERT INTO basis_declarations (run_id, basis, note, declared_at) VALUES (?, ?, ?, ?) '
        'ON CONFLICT(run_id) DO UPDATE SET basis=excluded.basis, note=excluded.note, '
        'declared_at=excluded.declared_at', (run_id, basis, note, declared_at))


def declared_basis(connection, run_id):
    row = connection.execute('SELECT basis, note, declared_at FROM basis_declarations WHERE run_id=?',
                             (run_id,)).fetchone()
    return None if row is None else dict(row)
