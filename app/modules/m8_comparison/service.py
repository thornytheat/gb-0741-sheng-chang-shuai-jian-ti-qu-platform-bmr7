import sqlite3
from datetime import datetime, timezone
from time import perf_counter_ns

from app.config import COMPARISON_VERSION
from app.errors import Problem
from app.evidence import canonical, sha256
from app.modules.m7_archive.service import record as archived_run
from app.modules.m8_comparison import repository
from app.modules.m8_comparison.comparison import band_row


def signature(archive):
    manifest = archive['manifest']
    return {'run_id': manifest['id'], 'source_id': manifest['source_id'],
            'version': manifest['version'], 'basis': manifest['basis'], 'digest': manifest['digest']}


def _load(store, run_id):
    archive = archived_run(store, run_id)
    with store.connect() as connection:
        archive['declaration'] = repository.declared_basis(connection, run_id)
    return archive


def _band_rows(archive):
    return {row['band_hz']: row for row in archive['result']['reconcile']['bands']}


def _plan(before_run_id, baseline):
    overrides = {}
    for key, value in (baseline or {}).items():
        try:
            band = int(str(key))
        except ValueError as error:
            raise Problem('BASELINE_INVALID', f'频带 {key!r} 不是有效中心频率') from error
        if not isinstance(value, str) or not value:
            raise Problem('BASELINE_INVALID', f'频带 {key!r} 的基线来源无效')
        overrides[band] = value
    if not before_run_id and not overrides:
        raise Problem('CHOOSE_BASELINE', '请选择改造前基线，或为频带指定基线')
    return before_run_id, overrides


def _references(before_run_id, overrides):
    ids = {run_id for run_id in (before_run_id, *overrides.values()) if run_id}
    return ids


def build(store, after_run_id, before_run_id=None, baseline=None):
    before_run_id, overrides = _plan(before_run_id, baseline)
    after = _load(store, after_run_id)
    archives = {}
    for run_id in _references(before_run_id, overrides):
        archives[run_id] = _load(store, run_id)
    after_bands = _band_rows(after)
    declared = set(after_bands)
    for archive in archives.values():
        declared.update(_band_rows(archive))
    bands = []
    for band in sorted(declared):
        before_id = overrides.get(band, before_run_id)
        before_archive = archives.get(before_id)
        bands.append(band_row(band, before_archive, after, before_archive and _band_rows(before_archive).get(band),
                              after_bands.get(band), before_id != before_run_id))
    references = [signature(archives[run_id]) for run_id in sorted(archives)]
    references.append(signature(after))
    references.sort(key=lambda item: item['run_id'])
    comparable = [row for row in bands if row['status'] in {'COMPARABLE', 'PARTIAL'}]
    not_comparable = [row for row in bands if row['status'] == 'NOT_COMPARABLE']
    counts = {}
    for row in bands:
        for item in row['reasons']:
            counts[item['code']] = counts.get(item['code'], 0) + 1
    result = {'rule_version': COMPARISON_VERSION, 'after_run_id': after_run_id,
              'before_run_id': before_run_id,
              'overrides': {str(band): run_id for band, run_id in sorted(overrides.items())},
              'references': references, 'bands': bands,
              'summary': {'bands': len(bands), 'comparable': len(comparable),
                          'not_comparable': len(not_comparable),
                          'confirmed_metrics': sum(item['status'] == 'CONFIRMED'
                                                   for row in bands for item in row['metrics']),
                          'deferred_metrics': sum(item['status'] == 'DEFERRED'
                                                  for row in bands for item in row['metrics']),
                          'reason_counts': counts,
                          'whole_hall_ready': bool(bands) and not not_comparable
                          and all(row['status'] == 'COMPARABLE' for row in bands)}}
    identity = {'rule_version': COMPARISON_VERSION, 'after_run_id': after_run_id,
                'before_run_id': before_run_id,
                'overrides': result['overrides'], 'references': references}
    return result, identity


def create(store, after_run_id, before_run_id=None, baseline=None):
    start = perf_counter_ns()
    result, identity = build(store, after_run_id, before_run_id, baseline)
    payload = canonical(result)
    manifest = {'id': sha256(canonical(identity)), 'after_run_id': after_run_id,
                'before_run_id': before_run_id or '',
                'references': identity['references'],
                'created_at': datetime.now(timezone.utc).isoformat(timespec='microseconds'),
                'elapsed_ms': (perf_counter_ns() - start) // 1_000_000, 'digest': sha256(payload)}
    try:
        with store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            added = repository.insert_comparison(connection, manifest, payload)
            archived = repository.read_comparison(connection, manifest['id'])
            if (archived['manifest']['digest'] != manifest['digest'] or
                    sha256(canonical(archived['result'])) != manifest['digest']):
                raise Problem('EVIDENCE_CHANGED', '重算差异与归档证据不符', 409)
    except sqlite3.Error as error:
        raise Problem('COMPARISON_FAILED', '差异写入失败，请重跑', 503) from error
    response = {'id': manifest['id'], 'reused': not added, 'manifest': archived['manifest'],
                'result': archived['result']}
    response['state'] = state(store, archived)
    return response


def state(store, archive):
    expected = {item['run_id']: item for item in archive['manifest']['references']}
    reasons = []
    for run_id, reference in sorted(expected.items()):
        try:
            current = signature(_load(store, run_id))
        except Problem as error:
            reasons.append({'code': 'COMPARISON_STALE', 'message': '任一侧归档已修正，旧差异失效',
                            'run_id': run_id, 'detail': {'cause': error.code}})
            continue
        changed = [field for field in ('version', 'basis', 'digest') if current[field] != reference[field]]
        if changed:
            reasons.append({'code': 'COMPARISON_STALE', 'message': '任一侧归档已修正，旧差异失效',
                            'run_id': run_id,
                            'detail': {'fields': changed, 'before': {field: reference[field] for field in changed},
                                       'now': {field: current[field] for field in changed}}})
    return {'stale': bool(reasons), 'reasons': reasons}


def _verified(archive):
    if sha256(canonical(archive['result'])) != archive['manifest']['digest']:
        raise Problem('EVIDENCE_CHANGED', '差异与证据摘要不符', 409)
    return archive


def read(store, comparison_id):
    with store.connect() as connection:
        archive = _verified(repository.read_comparison(connection, comparison_id))
    return {'id': archive['manifest']['id'], 'manifest': archive['manifest'],
            'result': archive['result'], 'state': state(store, archive)}


def history(store):
    with store.connect() as connection:
        items = repository.comparisons(connection)
    return {'comparisons': [{key: item[key] for key in ('id', 'before_run_id', 'after_run_id', 'created_at',
                                                        'elapsed_ms', 'digest')}
                            for item in items]}


def _confirmed(row):
    return row['status'] in {'COMPARABLE', 'PARTIAL'}


def publish(store, comparison_id, scope='confirmed'):
    if scope not in {'confirmed', 'all'}:
        raise Problem('SCOPE_INVALID', '发布范围只能是 confirmed 或 all')
    comparison = read(store, comparison_id)
    if comparison['state']['stale']:
        raise Problem('COMPARISON_STALE', '任一侧归档已修正，旧差异失效，请重算后再发布', 409)
    bands = comparison['result']['bands']
    publishable = [row for row in bands if _confirmed(row)]
    not_comparable = [row for row in bands if row['status'] == 'NOT_COMPARABLE']
    partial = [row for row in bands if row['status'] == 'PARTIAL']
    if scope == 'all' and (not_comparable or partial):
        codes = sorted({item['code'] for row in not_comparable + partial for item in row['reasons']})
        raise Problem('NOT_COMPARABLE',
                      f'仍有 {len(not_comparable) + len(partial)} 个频带不可比（{", ".join(codes)}），不能整厅发布',
                      409)
    if not publishable:
        raise Problem('NO_CONFIRMED_BANDS', '没有可确认频带，需先统一口径', 409)
    deferred = [{'band_hz': row['band_hz'], 'status': row['status'], 'reasons': row['reasons']}
                for row in not_comparable]
    deferred.extend({'band_hz': row['band_hz'], 'status': row['status'],
                     'reasons': [item['reason'] for item in row['metrics'] if item['status'] == 'DEFERRED']}
                    for row in partial)
    identifier = sha256(canonical({'comparison_id': comparison_id,
                                   'comparison_digest': comparison['manifest']['digest'],
                                   'scope': scope}))
    try:
        with store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            try:
                existing = repository.read_publication(connection, identifier)
            except Problem as error:
                if error.code != 'PUBLICATION_MISSING':
                    raise
                existing = None
            if existing is not None:
                _verified_publication(existing)
                return {'id': identifier, 'reused': True, 'manifest': existing['manifest'],
                        'publication': existing['publication']}
            created_at = datetime.now(timezone.utc).isoformat(timespec='microseconds')
            published = bands if scope == 'all' else publishable
            body = {'comparison_id': comparison_id,
                    'comparison_digest': comparison['manifest']['digest'],
                    'scope': scope, 'rule_version': COMPARISON_VERSION, 'created_at': created_at,
                    'references': comparison['result']['references'],
                    'bands': published,
                    'deferred': deferred,
                    'summary': {'published_bands': len(published),
                                'deferred_bands': len(deferred),
                                'confirmed_metrics': sum(item['status'] == 'CONFIRMED'
                                                         for row in published for item in row['metrics'])}}
            payload = canonical(body)
            digest = sha256(payload)
            added = repository.insert_publication(connection, identifier, digest, comparison_id,
                                                  comparison['manifest']['digest'], scope, created_at, payload)
            archived = repository.read_publication(connection, identifier)
            _verified_publication(archived)
    except sqlite3.Error as error:
        raise Problem('PUBLISH_FAILED', '发布失败，已保留上一次发布', 503) from error
    return {'id': identifier, 'reused': not added, 'manifest': archived['manifest'],
            'publication': archived['publication']}


def _verified_publication(archive):
    if sha256(canonical(archive['publication'])) != archive['manifest']['digest']:
        raise Problem('EVIDENCE_CHANGED', '发布快照与摘要不符', 409)
    return archive


def publication_state(store, archive):
    try:
        comparison = read(store, archive['manifest']['comparison_id'])
    except Problem as error:
        return {'stale': True, 'reasons': [{'code': 'COMPARISON_STALE',
                                            'message': '任一侧归档已修正，旧差异失效',
                                            'detail': {'cause': error.code}}]}
    if comparison['manifest']['digest'] != archive['manifest']['comparison_digest']:
        return {'stale': True, 'reasons': [{'code': 'COMPARISON_STALE',
                                            'message': '发布依据的差异版本已变化',
                                            'detail': {'fields': ['comparison_digest']}}]}
    return comparison['state']


def read_publication(store, publication_id):
    with store.connect() as connection:
        archive = _verified_publication(repository.read_publication(connection, publication_id))
    return {'id': archive['manifest']['id'], 'manifest': archive['manifest'],
            'publication': archive['publication'], 'state': publication_state(store, archive)}


def latest_publication(store):
    with store.connect() as connection:
        archive = repository.latest_publication(connection)
    if archive is None:
        raise Problem('PUBLICATION_MISSING', '尚无发布记录', 404)
    archive = _verified_publication(archive)
    return {'id': archive['manifest']['id'], 'manifest': archive['manifest'],
            'publication': archive['publication'], 'state': publication_state(store, archive)}


def publication_history(store):
    with store.connect() as connection:
        rows = repository.publication_history(connection)
    return {'publications': rows}


def declare_basis(store, run_id, basis, note=None):
    """旧归档补登记口径；规则生成的登记不可改写，已声明过的允许更正并触发失效。"""
    basis = (basis or '').strip()
    if not basis or len(basis) > 80:
        raise Problem('BASIS_INVALID', '口径标记须为 1 至 80 个字符')
    archive = _load(store, run_id)
    current = archive['manifest']['basis']
    with store.connect() as connection:
        declared = repository.declared_basis(connection, run_id)
    if current is not None and declared is None:
        if basis == current:
            return {'run_id': run_id, 'basis': current, 'version': archive['manifest']['version'],
                    'note': None, 'declared_at': None, 'corrected': False}
        raise Problem('BASIS_ALREADY_REGISTERED',
                      f'该归档的口径 {current} 由规则生成，不能改写', 409)
    declared_at = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    with store.connect() as connection:
        connection.execute('BEGIN IMMEDIATE')
        connection.execute('UPDATE runs SET basis=? WHERE id=?', (basis, run_id))
        repository.declare_basis(connection, run_id, basis, note, declared_at)
        row = connection.execute('SELECT version FROM runs WHERE id=?', (run_id,)).fetchone()
    return {'run_id': run_id, 'basis': basis, 'version': row['version'], 'note': note,
            'declared_at': declared_at, 'corrected': declared is not None}
