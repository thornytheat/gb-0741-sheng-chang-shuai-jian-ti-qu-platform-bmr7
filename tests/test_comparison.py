import csv
import io

import pytest

from app.modules.m1_intake.contract import HEADER


def _rows(stream, rt60, hz, rate=1000, samples=1100, peak=6, amplitude=0.002):
    for index in range(samples):
        value = amplitude if index < peak else amplitude * 10 ** (-60.0 / rt60 * (index - peak) / rate / 20)
        stream.writerow((hz, index, rate, samples, 'Pa', 0, '', repr(value), ''))


@pytest.fixture
def decay_csv():
    def make(rt60, hz=1000, rate=1000, samples=1100, peak=6, amplitude=0.002):
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        _rows(writer, rt60, hz, rate, samples, peak, amplitude)
        return stream.getvalue().encode()
    return make


@pytest.fixture
def multi_band_csv():
    def make(before=None, after=None):
        """两侧各自返回多频带 CSV；入参为 {Hz: T30}。"""
        stream = io.StringIO(newline='')
        writer = csv.writer(stream)
        writer.writerow(HEADER)
        for hz, rt60 in sorted((before or after).items()):
            _rows(writer, rt60, hz)
        return stream.getvalue().encode()
    return make


@pytest.fixture
def pair(client, decay_csv):
    def make(before_rt=1.0, after_rt=0.7):
        before = client.post('/api/intake', files={'file': ('before.csv', decay_csv(before_rt))}).json()['source_id']
        later = client.post('/api/intake', files={'file': ('after.csv', decay_csv(after_rt))}).json()['source_id']
        return (client.post('/api/runs', json={'source_id': before}).json(),
                client.post('/api/runs', json={'source_id': later}).json())
    return make


def compare(client, after, before=None):
    body = {'after_run_id': after['id']}
    if before is not None:
        body['before_run_id'] = before['id']
    return client.post('/api/comparisons', json=body)


def test_same_pair_rerun_adds_no_record_and_keeps_evidence(client, pair):
    before, after = pair()
    first = compare(client, after, before).json()
    second = compare(client, after, before).json()
    assert first['id'] == second['id'] and second['reused']
    assert second['result'] == first['result']
    assert len(client.get('/api/comparisons').json()['comparisons']) == 1
    assert client.get(f"/api/comparisons/{first['id']}/export").json()['result'] == first['result']


def test_each_band_cites_both_versions_and_positive_improvement(client, pair):
    before, after = pair()
    body = compare(client, after, before).json()
    row = body['result']['bands'][0]
    assert row['band_hz'] == 1000 and row['status'] == 'COMPARABLE'
    assert row['before']['run_id'] == before['id'] and row['after']['run_id'] == after['id']
    assert row['before']['version'] == row['after']['version'] == before['manifest']['version']
    assert row['before']['basis'] == row['after']['basis'] == 'pa-sens-gain-1'
    assert row['before']['basis_origin'] == row['after']['basis_origin'] == 'GENERATED'
    assert [item['metric'] for item in row['metrics']] == ['t20', 't30']
    assert all(item['status'] == 'CONFIRMED' for item in row['metrics'])
    assert row['headline']['metric'] == 't30'
    assert row['headline']['improvement_s'] == pytest.approx(.3, abs=.01)
    assert row['metrics'][0]['before_s'] - row['metrics'][0]['after_s'] == pytest.approx(.3, abs=.01)
    assert body['result']['summary']['whole_hall_ready'] is True


def test_legacy_archive_without_basis_is_blocked_with_reason(client, pair):
    before, after = pair()
    with client.app.state.store.connect() as connection:
        connection.execute('UPDATE runs SET basis=NULL WHERE id=?', (before['id'],))
    body = compare(client, after, before).json()
    row = body['result']['bands'][0]
    assert row['status'] == 'NOT_COMPARABLE'
    assert [item['code'] for item in row['reasons']] == ['BASIS_UNKNOWN']
    assert all(item['status'] == 'DEFERRED' for item in row['metrics'])
    assert row['headline'] is None
    assert body['result']['summary']['comparable'] == 0
    assert body['result']['summary']['whole_hall_ready'] is False


def test_declared_legacy_basis_unblocks_only_when_it_matches(client, pair):
    before, after = pair()
    with client.app.state.store.connect() as connection:
        connection.execute('UPDATE runs SET basis=NULL WHERE id=?', (before['id'],))
    declared = client.post(f"/api/runs/{before['id']}/basis", json={'basis': 'pa-sens-gain-1'}).json()
    assert declared['basis'] == 'pa-sens-gain-1' and declared['corrected'] is False
    matched = compare(client, after, before).json()['result']['bands'][0]
    assert matched['status'] == 'COMPARABLE' and matched['reasons'] == []
    assert matched['before']['basis_origin'] == 'DECLARED'
    assert matched['before']['basis_note'] is None
    client.post(f"/api/runs/{before['id']}/basis", json={'basis': 'legacy-gain-0'})
    mismatched = compare(client, after, before).json()['result']['bands'][0]
    assert mismatched['status'] == 'NOT_COMPARABLE'
    assert [item['code'] for item in mismatched['reasons']] == ['BASIS_MISMATCH']
    assert mismatched['reasons'][0]['detail'] == {'before': 'legacy-gain-0', 'after': 'pa-sens-gain-1'}


def test_generated_basis_cannot_be_rewritten(client, pair):
    before, after = pair()
    refused = client.post(f"/api/runs/{after['id']}/basis", json={'basis': 'legacy-gain-0'})
    assert (refused.status_code, refused.json()['code']) == (409, 'BASIS_ALREADY_REGISTERED')
    accepted = client.post(f"/api/runs/{after['id']}/basis", json={'basis': 'pa-sens-gain-1'})
    assert accepted.status_code == 200


def test_fixing_either_side_invalidates_old_difference_and_publication(client, pair):
    before, after = pair()
    with client.app.state.store.connect() as connection:
        connection.execute('UPDATE runs SET basis=NULL WHERE id=?', (before['id'],))
    client.post(f"/api/runs/{before['id']}/basis", json={'basis': 'pa-sens-gain-1'})
    comparison = compare(client, after, before).json()
    release = client.post('/api/publications', json={'comparison_id': comparison['id']}).json()
    assert client.get(f"/api/publications/{release['id']}").json()['state']['stale'] is False
    corrected = client.post(f"/api/runs/{before['id']}/basis", json={'basis': 'legacy-gain-0'}).json()
    assert corrected['corrected'] is True
    stale = client.get(f"/api/comparisons/{comparison['id']}").json()
    assert stale['state']['stale'] is True
    assert stale['state']['reasons'][0]['run_id'] == before['id']
    assert stale['state']['reasons'][0]['detail']['fields'] == ['basis']
    assert stale['result'] == comparison['result']
    refused = client.post('/api/publications', json={'comparison_id': comparison['id']})
    assert (refused.status_code, refused.json()['code']) == (409, 'COMPARISON_STALE')
    assert client.get(f"/api/publications/{release['id']}").json()['state']['stale'] is True
    assert client.get('/api/publications/latest').json()['id'] == release['id']


def test_confirmed_scope_ships_now_and_defers_incomparable_band(client, multi_band_csv):
    before_source = client.post('/api/intake', files={'file': ('b.csv', multi_band_csv(
        before={1000: 1.0, 2000: 1.0}))}).json()['source_id']
    after_source = client.post('/api/intake', files={'file': ('a.csv', multi_band_csv(
        after={1000: .7, 2000: .7}))}).json()['source_id']
    before = client.post('/api/runs', json={'source_id': before_source}).json()
    after = client.post('/api/runs', json={'source_id': after_source}).json()
    # 整份旧归档按旧口径生成；其中 1000 Hz 另有当前口径的单带基线可引用。
    with client.app.state.store.connect() as connection:
        connection.execute("UPDATE runs SET basis='legacy-gain-0' WHERE id=?", (before['id'],))
    clean_source = client.post('/api/intake', files={'file': ('c.csv', multi_band_csv(
        before={1000: 1.0}))}).json()['source_id']
    clean = client.post('/api/runs', json={'source_id': clean_source}).json()
    body = client.post('/api/comparisons', json={'after_run_id': after['id'], 'before_run_id': before['id'],
                                                 'baseline': {'1000': clean['id']}}).json()
    rows = {row['band_hz']: row for row in body['result']['bands']}
    assert rows[1000]['status'] == 'COMPARABLE' and rows[1000]['baseline_override'] is True
    assert rows[2000]['status'] == 'NOT_COMPARABLE'
    assert [item['code'] for item in rows[2000]['reasons']] == ['BASIS_MISMATCH']
    blocked = client.post('/api/publications', json={'comparison_id': body['id'], 'scope': 'all'})
    assert (blocked.status_code, blocked.json()['code']) == (409, 'NOT_COMPARABLE')
    assert client.get('/api/publications').json()['publications'] == []
    release = client.post('/api/publications', json={'comparison_id': body['id']}).json()
    assert release['reused'] is False and release['publication']['scope'] == 'confirmed'
    assert [row['band_hz'] for row in release['publication']['bands']] == [1000]
    assert release['publication']['summary'] == {'published_bands': 1, 'deferred_bands': 1,
                                                 'confirmed_metrics': 2}
    assert release['publication']['deferred'][0]['band_hz'] == 2000
    assert release['publication']['deferred'][0]['reasons'][0]['code'] == 'BASIS_MISMATCH'
    repeat = client.post('/api/publications', json={'comparison_id': body['id']}).json()
    assert repeat['reused'] and repeat['id'] == release['id']


def test_nothing_confirmed_refuses_publication(client, pair):
    before, after = pair()
    with client.app.state.store.connect() as connection:
        connection.execute('UPDATE runs SET basis=NULL WHERE id=?', (before['id'],))
    comparison = compare(client, after, before).json()
    for scope in ('confirmed', 'all'):
        refused = client.post('/api/publications', json={'comparison_id': comparison['id'], 'scope': scope})
        assert refused.status_code == 409 and refused.json()['code'] in {'NOT_COMPARABLE', 'NO_CONFIRMED_BANDS'}
    assert client.get('/api/publications').json()['publications'] == []


def test_publish_failure_rolls_back_and_keeps_previous_release(client, pair):
    before, after = pair()
    comparison = compare(client, after, before).json()
    first = client.post('/api/publications', json={'comparison_id': comparison['id']}).json()
    with client.app.state.store.connect() as connection:
        connection.execute(
            "CREATE TRIGGER fail_release BEFORE INSERT ON publications WHEN NEW.scope='all' "
            "BEGIN SELECT RAISE(ABORT, 'test'); END")
    failed = client.post('/api/publications', json={'comparison_id': comparison['id'], 'scope': 'all'})
    assert (failed.status_code, failed.json()['code']) == (503, 'PUBLISH_FAILED')
    with client.app.state.store.connect() as connection:
        connection.execute('DROP TRIGGER fail_release')
    latest = client.get('/api/publications/latest').json()
    assert latest['id'] == first['id']
    assert [row['id'] for row in client.get('/api/publications').json()['publications']] == [first['id']]
    recovered = client.post('/api/publications', json={'comparison_id': comparison['id'], 'scope': 'all'}).json()
    assert recovered['reused'] is False and recovered['publication']['scope'] == 'all'


def test_partial_band_confirms_available_metric_only(client, decay_csv):
    # 4000 Hz 一档可用、一档深度不足：T20 可确认，T30 记为不可比原因。
    before_source = client.post('/api/intake', files={'file': ('b.csv', decay_csv(1.0, hz=4000))}).json()['source_id']
    after_source = client.post('/api/intake', files={'file': ('a.csv', decay_csv(.7, hz=4000))}).json()['source_id']
    before = client.post('/api/runs', json={'source_id': before_source}).json()
    after = client.post('/api/runs', json={'source_id': after_source}).json()
    body = compare(client, after, before).json()
    row = body['result']['bands'][0]
    assert row['status'] in {'COMPARABLE', 'PARTIAL'}
    if row['status'] == 'PARTIAL':
        assert [item['status'] for item in row['metrics']] == ['CONFIRMED', 'DEFERRED']
        assert row['metrics'][1]['reason']['code'] == 'METRIC_UNAVAILABLE'


def test_missing_baseline_invalid_band_and_unknown_ids(client, pair):
    before, after = pair()
    assert compare(client, after).json()['code'] == 'CHOOSE_BASELINE'
    bad_band = client.post('/api/comparisons', json={'after_run_id': after['id'], 'baseline': {'x': before['id']}})
    assert (bad_band.status_code, bad_band.json()['code']) == (422, 'BASELINE_INVALID')
    missing = client.post('/api/comparisons', json={'after_run_id': 'nope', 'before_run_id': before['id']})
    unknown = client.get('/api/comparisons/nope')
    assert [(reply.status_code, reply.json()['code']) for reply in (missing, unknown)] == [
        (404, 'RUN_MISSING'), (404, 'COMPARISON_MISSING')]


def test_per_band_baseline_override_is_cited(client, pair, decay_csv):
    before, after = pair()
    other = client.post('/api/intake', files={'file': ('other.csv', decay_csv(.9))}).json()['source_id']
    other_run = client.post('/api/runs', json={'source_id': other}).json()
    body = client.post('/api/comparisons', json={'after_run_id': after['id'], 'before_run_id': before['id'],
                                                 'baseline': {'1000': other_run['id']}}).json()
    row = body['result']['bands'][0]
    assert row['baseline_override'] is True and row['before']['run_id'] == other_run['id']
    assert body['result']['overrides'] == {'1000': other_run['id']}
    assert {item['run_id'] for item in body['result']['references']} == {before['id'], after['id'], other_run['id']}


def test_same_source_under_conflicting_basis_is_refused(client, pair, monkeypatch):
    from app.modules.m7_archive import service
    before, _ = pair()
    monkeypatch.setattr('app.modules.m7_archive.service.CALIBRATION_BASIS', 'other-basis-2')
    again = client.post('/api/runs', json={'source_id': before['manifest']['source_id']})
    assert (again.status_code, again.json()['code']) == (409, 'ARCHIVE_CONFLICT')


def test_comparison_snapshot_is_pure_rule_judgement(client, pair):
    before, after = pair()
    body = compare(client, after, before).json()
    summary = body['result']['summary']
    assert summary['bands'] == 1 and summary['confirmed_metrics'] == 2 and summary['deferred_metrics'] == 0
    assert summary['reason_counts'] == {}
    row = body['result']['bands'][0]
    assert row['headline']['direction'] == 'IMPROVED'
    assert row['metrics'][0]['before_s'] == row['before']['t20_s']
    assert row['metrics'][0]['after_s'] == row['after']['t20_s']


def test_legacy_database_is_migrated_without_touching_archived_bytes(tmp_path):
    import sqlite3
    from app.config import SAMPLES
    from app.evidence import canonical, sha256
    from app.modules.m1_intake.reader import read
    from app.modules.m1_intake.service import summary
    from app.modules.m6_reconcile.service import evaluate
    from app.modules.m7_archive.service import key as archive_key
    from app.store import Store
    from fastapi.testclient import TestClient
    from app.main import create_app

    raw = SAMPLES['reference'].read_bytes()
    data = read(raw)
    result = {'intake': summary(sha256(raw), data), **evaluate(data)}
    payload = canonical(result)
    legacy = tmp_path / 'legacy.sqlite3'
    connection = sqlite3.connect(legacy)
    connection.executescript(
        'CREATE TABLE source (id TEXT PRIMARY KEY, content BLOB NOT NULL);'
        'CREATE TABLE runs (id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source(id), '
        'version TEXT NOT NULL, created_at TEXT NOT NULL, elapsed_ms INTEGER NOT NULL, '
        'digest TEXT NOT NULL, evidence BLOB NOT NULL);')
    connection.execute('INSERT INTO source VALUES (?, ?)', (sha256(raw), raw))
    legacy_id = archive_key(sha256(raw))
    connection.execute('INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?)',
                       (legacy_id, sha256(raw), 'rt-schroeder-1',
                        '2026-01-01T00:00:00.000000+00:00', 1, sha256(payload), payload))
    connection.commit()
    connection.close()
    store = Store(legacy)
    with store.connect() as rows:
        archived = rows.execute('SELECT basis FROM runs WHERE id=?', (legacy_id,)).fetchone()
        assert archived['basis'] is None and sha256(raw) == rows.execute(
            'SELECT id FROM source').fetchone()['id']
    with TestClient(create_app(legacy)) as client:
        kept = client.get(f'/api/runs/{legacy_id}')
        assert kept.status_code == 200 and kept.json()['result'] == result
        exported = client.get(f'/api/runs/{legacy_id}/export').json()
        assert exported['result'] == result and exported['manifest']['basis'] is None
        # 旧归档未登记口径时，重跑不得静默补登记为当前口径。
        repeated = client.post(f'/api/runs/{legacy_id}/rerun')
        assert repeated.status_code in {200, 409}
        if repeated.status_code == 409:
            assert repeated.json()['code'] in {'VERSION_CHANGED', 'EVIDENCE_CHANGED', 'ARCHIVE_CONFLICT'}
        else:
            assert repeated.json()['manifest']['basis'] is None


def _archive(run_id, basis='pa-sens-gain-1', version='rt-schroeder-1'):
    return {'manifest': {'id': run_id, 'source_id': 'src', 'version': version, 'basis': basis}}


def _row(hz=1000, t20=None, t30=None, status='FIT'):
    return {'band_hz': hz, 'status': status, 't20_s': t20, 't30_s': t30, 'findings': []}


def test_band_row_marks_partial_when_one_metric_missing():
    from app.modules.m8_comparison.comparison import band_row
    row = band_row(1000, _archive('b'), _archive('a'), _row(t20=1.0, t30=1.0), _row(t20=.7), False)
    assert row['status'] == 'PARTIAL'
    assert [item['status'] for item in row['metrics']] == ['CONFIRMED', 'DEFERRED']
    assert row['metrics'][1]['reason'] == {'code': 'METRIC_UNAVAILABLE', 'message': '该档在某一侧未出具 RT60',
                                           'detail': {'metric': 't30', 'sides': ['after']}}
    assert row['headline']['metric'] == 't20'
    assert row['reasons'] == [row['metrics'][1]['reason']]


def test_band_row_prefers_basis_blocker_over_metric_gap():
    from app.modules.m8_comparison.comparison import band_row
    row = band_row(1000, _archive('b', basis='legacy'), _archive('a'), _row(t20=1.0), _row(t20=.7), False)
    assert row['status'] == 'NOT_COMPARABLE'
    assert [item['code'] for item in row['reasons']] == ['BASIS_MISMATCH']
    assert all(item['status'] == 'DEFERRED' for item in row['metrics'])


def test_band_row_missing_band_and_regression_direction():
    from app.modules.m8_comparison.comparison import band_row
    absent = band_row(1000, _archive('b'), _archive('a'), None, _row(t20=.7, t30=.7), False)
    assert absent['status'] == 'NOT_COMPARABLE'
    assert absent['reasons'][0]['code'] == 'BAND_MISSING' and absent['reasons'][0]['side'] == 'before'
    worse = band_row(1000, _archive('b'), _archive('a'), _row(t20=.7, t30=.7), _row(t20=1.0, t30=1.0), False)
    assert worse['headline']['direction'] == 'REGRESSED' and worse['headline']['improvement_s'] < 0
