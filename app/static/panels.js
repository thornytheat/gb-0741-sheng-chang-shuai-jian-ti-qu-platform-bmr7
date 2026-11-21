export const $ = (id) => document.getElementById(id);
export const fixed = (value, digits = 3) => value == null ? '—' : Number(value).toFixed(digits);
const scientific = (value) => value == null ? '—' : value.toExponential(3);
const states = {FIT: ['可拟合', 'good'], INSUFFICIENT: ['范围不足', 'warn'], ISOLATED: ['隔离', 'bad']};

function el(tag, text, className) {
  const node = document.createElement(tag);
  if (text != null) node.textContent = text;
  if (className) node.className = className;
  return node;
}

function specs(items) {
  const root = el('dl', null, 'specs');
  for (const [key, value] of items) {
    const column = el('div');
    column.append(el('dt', key), el('dd', value ?? '—'));
    root.append(column);
  }
  return root;
}

function action(label, click) {
  const button = el('button', label);
  button.type = 'button';
  button.addEventListener('click', click);
  return button;
}

export function evidence(title, body) {
  $('evidence-title').textContent = title;
  $('evidence-text').textContent = JSON.stringify(body, null, 2);
  $('evidence').showModal();
}

export function table(host, headers, rows, selected = -1) {
  if (!rows.length) {
    $(host).replaceChildren(el('p', '暂无记录', 'empty'));
    return;
  }
  const node = el('table', null, 'ledger');
  const head = node.createTHead().insertRow();
  for (const title of headers) {
    const heading = el('th', title);
    heading.scope = 'col';
    head.append(heading);
  }
  const body = node.createTBody();
  rows.forEach((row, index) => {
    const tr = body.insertRow();
    if (index === selected) tr.setAttribute('aria-selected', 'true');
    for (const item of row) {
      const td = tr.insertCell();
      if (item instanceof Node) td.append(item);
      else {
        td.textContent = item ?? '—'; td.title = td.textContent;
        if (typeof item === 'number' || /^-?\d+\.\d+/.test(item ?? '')) td.className = 'num';
      }
    }
  });
  $(host).replaceChildren(node);
}

export function fitPanel(fit, row) {
  if (!fit) {
    $('fit-tiles').replaceChildren(el('p', row?.findings[0]?.message || '暂无可用区间', 'empty'));
    return;
  }
  $('fit-tiles').replaceChildren(...[['T20', fit.t20], ['T30', fit.t30]].map(([name, trial]) => {
    const tile = el('section', null, 'fit-tile');
    const title = el('div', null, 'tile-heading');
    title.append(el('h3', name), el('strong', `${fixed(trial.rt60_s)}${trial.rt60_s != null ? ' s' : ''}`));
    tile.append(title, el('p', `${trial.from_db} 至 ${trial.to_db} dB`, 'muted'));
    if (trial.error) tile.append(el('p', trial.error.message, 'warn'));
    if (trial.window) tile.append(specs([
      ['时段 / s', `${fixed(trial.window.start_s)}–${fixed(trial.window.end_s)}`], ['采样点', trial.window.point_count],
      ['斜率 / dB/s', fixed(trial.slope_db_s ?? trial.regression?.slope_db_s, 2)], ['R²', fixed(trial.r2 ?? trial.regression?.r2, 4)],
      ['来源行', `${trial.window.start_line} → ${trial.window.end_line}`],
    ]));
    return tile;
  }));
}

export function noisePanel(noise) {
  if (!noise) {
    $('noise-body').replaceChildren(el('p', '此频带没有可用响应', 'empty'));
    return;
  }
  const depth = el('progress', null, 'depth');
  depth.max = Math.max(60, Math.ceil(noise.usable_depth_db));
  depth.value = noise.usable_depth_db;
  depth.setAttribute('aria-label', '可用衰减深度');
  $('noise-body').replaceChildren(specs([
    ['峰值至噪声 / dB', fixed(noise.peak_to_noise_db, 1)], ['噪声功率 / Pa²', scientific(noise.noise_power_pa2)],
    ['可用深度 / dB', fixed(noise.usable_depth_db, 1)], ['噪声底 / dB', fixed(noise.floor_db, 1)],
    ['可用截止 / s', fixed(noise.usable_until_s)], ['测噪起点 / s', fixed(noise.noise_from_s)],
    ['最低余量 / dB', noise.required_margin_db], ['适用档位', noise.supports_t30 ? 'T20 / T30' : noise.supports_t20 ? 'T20' : '无'],
  ]), depth);
}

export function reconcile(data, band, pick) {
  const totals = data.totals;
  for (const key of ['fitted', 'insufficient', 'isolated', 'flagged']) $(key).textContent = totals[key];
  $('difference').textContent = totals.band_difference;
  $('balance').textContent = data.status === 'MATCHED' ? '频带、样本已对平' : '存在勾稽差额';
  table('band-table', ['Hz', '判定', 'T20 / s', 'T30 / s', '标差', '证据'], data.bands.map((row) => {
    const [label, style] = states[row.status];
    return [action(`${row.band_hz}`, () => pick(row.band_hz)), el('span', label, `status-label ${style}`),
      fixed(row.t20_s), fixed(row.t30_s), row.gap_flag ? el('span', `${fixed(100 * row.relative_gap, 1)}%`, 'warn') : '—',
      action('查看', () => evidence(`${row.band_hz} Hz 判定`, row))];
  }), data.bands.findIndex((row) => row.band_hz === band));
}

export function intake(data) {
  const count = data.counts;
  $('intake-data').replaceChildren(specs([['编码', data.encoding], ['输入记录', count.input], ['采信', count.accepted],
    ['隔离', count.isolated], ['重复', count.duplicate], ['空白', count.blank], ['重复表头', count.repeated_headers]]));
  const findings = data.isolated_bands.flatMap((band) => band.findings.map((item) => ({band_hz: band.band_hz, ...item})));
  const rows = [...findings, ...data.row_issues];
  table('intake-issues', ['频带 / Hz', '来源行', '原因码', '证据'], rows.map((row) => [row.band_hz, row.line ?? row.lines?.join('、'),
    row.code, action('查看', () => evidence(row.message, row))]));
}

export function calibration(data) {
  table('calibration-data', ['Hz', '输入单位', '增益 / dB', '系数 / Pa', '峰值 / Pa', 'RMS / Pa', '来源行'], data.bands.map((row) => [
    row.band_hz, row.input.unit, row.input.gain_db, scientific(row.factor_pa), fixed(row.peak_pa, 5), fixed(row.rms_pa, 5), row.peak_line,
  ]));
}

export function history(runs, current, pick) {
  table('run-list', ['批次', '来源摘要', '规则', '口径', '归档时间 UTC', '耗时 ms', '结果摘要'], runs.map((row) => [
    action(row.id.slice(0, 12), () => pick(row.id)), row.source_id.slice(0, 12), row.version,
    row.basis ?? el('span', '旧归档 · 未登记', 'warn'), row.created_at.slice(0, 19).replace('T', ' '),
    row.elapsed_ms, action(row.digest.slice(0, 12), () => evidence('运行清单', row)),
  ]), runs.findIndex((row) => row.id === current));
}

export function archiveOptions(runs) {
  const option = (row) => new Option(
    `${row.id.slice(0, 8)} · ${row.source_id.slice(0, 8)} · ${row.basis ?? '口径未登记'}`,
    row.id);
  $('after-run').replaceChildren(new Option('—', ''), ...runs.map(option));
  $('before-run').replaceChildren(new Option('整厅不限', ''), ...runs.map(option));
}

const basisLabels = {BASIS_MISMATCH: '口径不同', BASIS_UNKNOWN: '口径未登记', VERSION_MISMATCH: '规则版本不同',
  BAND_MISSING: '缺频带', BASELINE_MISSING: '缺基线', METRIC_UNAVAILABLE: '该档未出具'};
const compareStates = {COMPARABLE: ['可比', 'good'], PARTIAL: ['部分可比', 'warn'], NOT_COMPARABLE: ['不可比', 'bad']};

function sideLabel(side) {
  if (!side?.run_id) return '—';
  return `${side.run_id.slice(0, 8)} · ${side.version ?? '—'} · ${side.basis ?? '未登记'}`;
}

function metricCell(metric) {
  if (!metric) return '—';
  if (metric.status !== 'CONFIRMED') {
    return el('span', metric.reason?.message ?? '不可比', 'warn');
  }
  const sign = metric.improvement_s > 0 ? '+' : '';
  const text = `${sign}${fixed(metric.improvement_s)} s`;
  const span = el('span', text, metric.direction === 'REGRESSED' ? 'bad' : 'good');
  span.title = `${fixed(metric.before_s)} → ${fixed(metric.after_s)}`;
  return span;
}

export function comparison(data, pick) {
  const rows = data?.result?.bands ?? [];
  const summary = data?.result?.summary;
  $('comparison-summary').textContent = summary
    ? `${summary.comparable}/${summary.bands} 频带可比 · ${summary.confirmed_metrics} 项可确认`
    : '—';
  $('comparison-state').textContent = data?.state?.stale ? '旧差异已失效' : data ? '差异为当前版本' : '';
  $('comparison-state').className = data?.state?.stale ? 'bad' : data ? 'muted' : 'muted';
  table('comparison-bands', ['Hz', '判定', '改造前版本', '改造后版本', 'T20', 'T30', '证据'], rows.map((row) => {
    const [label, style] = compareStates[row.status];
    return [action(`${row.band_hz}`, () => pick?.(row.band_hz)), el('span', label, `status-label ${style}`),
      sideLabel(row.before), sideLabel(row.after),
      metricCell(row.metrics.find((item) => item.metric === 't20')),
      metricCell(row.metrics.find((item) => item.metric === 't30')),
      action('查看', () => evidence(`${row.band_hz} Hz 差异`, row))];
  }), -1);
  const reasons = rows.flatMap((row) => row.reasons.map((item) => ({band_hz: row.band_hz, ...item})));
  if (!reasons.length) {
    $('comparison-reasons').replaceChildren(el('p', '全部频带可比，无不可比项', 'muted'));
    return;
  }
  const list = el('div', null, 'reason-list');
  for (const item of reasons) {
    list.append(el('p', `${item.band_hz} Hz · ${basisLabels[item.code] ?? item.message}：${item.message}`,
      item.code === 'COMPARISON_STALE' ? 'bad' : 'warn'));
  }
  $('comparison-reasons').replaceChildren(el('h3', '不可比项及原因', 'reason-heading'), list);
}

export function publication(data, releases) {
  if (!data) {
    $('publication-scope').textContent = '—';
    $('publication-body').replaceChildren(el('p', '尚无发布', 'empty'));
    table('publication-list', ['范围', '差异版本', '发布时间 UTC'], []);
    return;
  }
  const body = data.publication;
  $('publication-scope').textContent = body.scope === 'confirmed' ? '已发布可确认频带' : '整厅发布';
  const items = [['发布编号', data.id.slice(0, 12)], ['依据差异', body.comparison_id.slice(0, 12)],
    ['发布频带', `${body.summary.published_bands} / 共 ${body.summary.published_bands + body.summary.deferred_bands}`],
    ['可确认项', body.summary.confirmed_metrics], ['不可比频带', body.summary.deferred_bands],
    ['状态', data.state.stale ? '已失效，需重算' : '当前有效']];
  $('publication-body').replaceChildren(specs(items));
  table('publication-list', ['范围', '依据差异', '发布批次', '发布频带', '时间 UTC', '证据'], (releases ?? []).map((row) => [
    row.scope === 'confirmed' ? '可确认频带' : '整厅', row.comparison_id.slice(0, 8), row.id.slice(0, 8),
    row.summary?.published_bands ?? '—', row.created_at.slice(0, 19).replace('T', ' '),
    action('查看', () => evidence('发布快照', row))]));
}
