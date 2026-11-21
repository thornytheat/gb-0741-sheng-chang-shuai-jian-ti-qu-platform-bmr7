from app.reasons import reason

METRICS = ('t20', 't30')
EPSILON = 1e-12


def finding(code, side=None, **detail):
    item = {**reason(code)}
    if side:
        item['side'] = side
    if detail:
        item['detail'] = detail
    return item


def side(run, row):
    manifest = (run or {}).get('manifest', {})
    declaration = (run or {}).get('declaration')
    return {'run_id': manifest.get('id'), 'source_id': manifest.get('source_id'),
            'version': manifest.get('version'), 'basis': manifest.get('basis'),
            'basis_origin': 'DECLARED' if declaration else ('GENERATED' if manifest.get('basis') else 'UNKNOWN'),
            'basis_note': declaration['note'] if declaration else None,
            'basis_declared_at': declaration['declared_at'] if declaration else None,
            'status': row['status'] if row else None,
            't20_s': row['t20_s'] if row else None, 't30_s': row['t30_s'] if row else None,
            'findings': [item['code'] for item in (row or {}).get('findings', [])]}


def metric_item(metric, before, after, confirmed):
    before_value, after_value = before[f'{metric}_s'], after[f'{metric}_s']
    base = {'metric': metric, 'status': 'CONFIRMED' if confirmed else 'DEFERRED',
            'before_s': before_value, 'after_s': after_value}
    if not confirmed:
        return base
    improvement = before_value - after_value
    return {**base, 'improvement_s': improvement,
            'improvement_pct': improvement / before_value * 100.0 if before_value else None,
            'direction': ('IMPROVED' if improvement > EPSILON
                          else 'REGRESSED' if improvement < -EPSILON else 'UNCHANGED')}


def band_row(band, before_run, after_run, before_row, after_row, override):
    before, after = side(before_run, before_row), side(after_run, after_row)
    blockers = []
    if before_run is None:
        blockers.append(finding('BASELINE_MISSING', band_hz=band))
    elif before_row is None:
        blockers.append(finding('BAND_MISSING', 'before', band_hz=band))
    if after_row is None:
        blockers.append(finding('BAND_MISSING', 'after', band_hz=band))
    if before_row is not None and after_row is not None:
        if before['version'] != after['version']:
            blockers.append(finding('VERSION_MISMATCH', before=before['version'], after=after['version']))
        if before['basis'] is None or after['basis'] is None:
            blockers.append(finding('BASIS_UNKNOWN', before=before['basis'], after=after['basis']))
        elif before['basis'] != after['basis']:
            blockers.append(finding('BASIS_MISMATCH', before=before['basis'], after=after['basis']))
    reasons = list(blockers)
    items = []
    for metric in METRICS:
        if blockers:
            items.append({**metric_item(metric, before, after, False), 'reason': blockers[0]})
            continue
        unavailable = [name for name, value in (('before', before[f'{metric}_s']),
                                                ('after', after[f'{metric}_s'])) if value is None]
        if unavailable:
            missing = finding('METRIC_UNAVAILABLE', metric=metric, sides=unavailable)
            reasons.append(missing)
            items.append({**metric_item(metric, before, after, False), 'reason': missing})
        else:
            items.append(metric_item(metric, before, after, True))
    confirmed = [item for item in items if item['status'] == 'CONFIRMED']
    if not confirmed:
        status = 'NOT_COMPARABLE'
    elif len(confirmed) == len(items):
        status = 'COMPARABLE'
    else:
        status = 'PARTIAL'
    headline_metric = 't30' if any(item['metric'] == 't30' for item in confirmed) else (
        confirmed[0]['metric'] if confirmed else None)
    headline = next((item for item in confirmed if item['metric'] == headline_metric), None)
    return {'band_hz': band, 'status': status, 'before': before, 'after': after,
            'baseline_override': override,
            'headline': (None if headline is None else {
                'metric': headline['metric'], 'before_s': headline['before_s'],
                'after_s': headline['after_s'], 'improvement_s': headline['improvement_s'],
                'improvement_pct': headline['improvement_pct'], 'direction': headline['direction']}),
            'metrics': items, 'reasons': dedupe(reasons)}


def dedupe(reasons):
    seen, unique = set(), []
    for item in reasons:
        key = (item['code'], item.get('side'), item.get('metric'))
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique
