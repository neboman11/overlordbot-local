// Step-through walkthrough of the pilot radio-call charts.  The Mermaid charts in airfield.md and
// carrier.md stay the single source of truth (tests/test_radio_call_docs.py replays every call in
// them against the real parsers); this page reads them and walks the graph one step at a time.

const FENCE = /^```mermaid\s*\n([\s\S]*?)^```\s*$/gm;
const FRONT_MATTER = /^---\n[\s\S]*?\n---\n/;
const NODE = /^\s*([A-Za-z]\w*)(\(\(|\(\[|\{\{|\[|\{|\()"(.*)"(\)\)|\]\)|\}\}|\]|\}|\))\s*$/;
const EDGE = /^\s*([A-Za-z]\w*)\s+(?:(\w+)@)?(-->|-\.->|==>)(?:\|"((?:[^"\\]|\\.)*)"\|)?\s*([A-Za-z]\w*)\s*$/;
const CLASS = /^\s*class\s+([\w,]+)\s+(\w+)\s*$/;
const SLOT = /\[([A-Z][A-Z0-9_]*)\]/g;
const FLOW_ORDER = { normalFlow: 0, neutralFlow: 1, recoveryFlow: 2, errorFlow: 3 };
const TONE = { normalFlow: 'normal', neutralFlow: 'neutral', recoveryFlow: 'recovery', errorFlow: 'error' };
const STORE = 'overlord-walkthrough';

const $ = selector => document.querySelector(selector);
const el = (tag, props = {}, ...children) => {
  const node = Object.assign(document.createElement(tag), props);
  node.append(...children.filter(child => child !== null && child !== undefined));
  return node;
};

// ---------------------------------------------------------------------------------------------
// Chart parsing

function plain(text) {
  return text.replace(/<br\s*\/?>/g, ' ').replace(/\\"/g, '"').replace(/\s+/g, ' ').trim();
}

function classify(id, shape, raw) {
  const label = plain(raw);
  const say = label.match(/^`YOU:\s+\*\*(.+)\*\*`$/);
  if (say) return { id, kind: 'say', tag: 'Say', text: say[1] };
  const colon = label.indexOf(':');
  const tag = colon > 0 && colon <= 40 ? label.slice(0, colon).trim() : '';
  const text = tag ? label.slice(colon + 1).trim() : label;
  if (/^(ATC|REPLY|EVENT|RECYCLE_INSTRUCTION)\b/.test(label)) {
    return { id, kind: 'hear', tag: tag === 'RECYCLE_INSTRUCTION' ? 'Recycle instruction' : tag, text };
  }
  if (/^(CHECK\b|Any YOU call|At any)/.test(label)) return { id, kind: 'hub', tag: tag || 'Check', text };
  if (shape === '((' || /^(START|END|DONE|GO TO|FROM|Continue|RETURN|RETRY)\b/.test(label)) {
    return { id, kind: 'point', tag: tag || label.split(/[:\s]/)[0], text: tag ? text : label };
  }
  return { id, kind: 'do', tag: tag || 'Do', text };
}

function parseChart(source) {
  const nodes = new Map();
  const edges = [];
  const classes = new Map();
  for (const line of source.replace(FRONT_MATTER, '').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || /^(%%|flowchart|classDef)/.test(trimmed)) continue;
    let match = NODE.exec(line);
    if (match) { nodes.set(match[1], classify(match[1], match[2], match[3])); continue; }
    match = EDGE.exec(line);
    if (match) { edges.push({ from: match[1], name: match[2], dashed: match[3] === '-.->', label: plain(match[4] || ''), to: match[5] }); continue; }
    match = CLASS.exec(line);
    if (match) { for (const name of match[1].split(',')) classes.set(name, match[2]); continue; }
    throw new Error(`Unrecognised chart line: ${trimmed.slice(0, 60)}`);
  }
  const out = new Map([...nodes.keys()].map(id => [id, []]));
  for (const edge of edges) {
    if (!nodes.has(edge.from) || !nodes.has(edge.to)) throw new Error(`Edge to unknown step ${edge.from} → ${edge.to}`);
    edge.flow = classes.get(edge.name) || (edge.dashed ? 'recoveryFlow' : 'normalFlow');
    out.get(edge.from).push(edge);
  }
  for (const list of out.values()) {
    list.forEach((edge, index) => { edge.order = index; });
    list.sort((a, b) => (FLOW_ORDER[a.flow] ?? 9) - (FLOW_ORDER[b.flow] ?? 9) || a.order - b.order);
  }
  return { nodes, out };
}

async function loadCharts() {
  const manifest = await fetchJson('walkthrough.json');
  const sources = new Map();
  const charts = new Map();
  for (const spec of manifest.charts) {
    if (!sources.has(spec.source)) sources.set(spec.source, fetchText(spec.source));
    const blocks = [...(await sources.get(spec.source)).matchAll(FENCE)];
    const block = blocks[spec.index];
    if (!block) throw new Error(`${spec.title} chart is missing from ${spec.source}`);
    const chart = { ...spec, ...parseChart(block[1]) };
    if (!chart.nodes.has(chart.start)) throw new Error(`${spec.title} has no step ${chart.start}`);
    charts.set(spec.id, chart);
  }
  for (const chart of charts.values()) {
    for (const [from, targets] of Object.entries(chart.jumps)) {
      for (const target of targets) {
        const [chartId, nodeId] = target.split(':');
        if (!chart.nodes.has(from) || !charts.get(chartId)?.nodes.has(nodeId)) throw new Error(`Broken chart link ${from} → ${target}`);
      }
    }
  }
  return { charts, placeholders: manifest.placeholders };
}

async function fetchText(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${url} request failed (${response.status})`);
  return response.text();
}

async function fetchJson(url) {
  return JSON.parse(await fetchText(url));
}

// ---------------------------------------------------------------------------------------------
// State

const saved = (() => {
  try { return JSON.parse(localStorage.getItem(STORE)) || {}; } catch { return {}; }
})();
const state = {
  chart: null,
  positions: saved.positions || {},   // chart id -> { node, trail: [node ids] }
  values: saved.values || {},
};

function persist() {
  try { localStorage.setItem(STORE, JSON.stringify({ chart: state.chart, positions: state.positions, values: state.values })); } catch { /* private mode */ }
}

let data;

function position(chartId = state.chart) {
  const chart = data.charts.get(chartId);
  const current = state.positions[chartId];
  if (!current || !chart.nodes.has(current.node)) state.positions[chartId] = { node: chart.start, trail: [] };
  state.positions[chartId].trail = state.positions[chartId].trail.filter(id => chart.nodes.has(id));
  return state.positions[chartId];
}

function go(chartId, nodeId, { record = true } = {}) {
  if (chartId !== state.chart) {
    state.chart = chartId;
    if (record) {
      const target = position(chartId);
      if (target.node !== nodeId) target.trail.push(target.node);
      target.node = nodeId;
    }
  } else {
    const here = position();
    if (record && here.node !== nodeId) here.trail.push(here.node);
    here.node = nodeId;
  }
  if (state.positions[chartId].trail.length > 200) state.positions[chartId].trail.splice(0, 50);
  persist();
  history.replaceState(null, '', `#${chartId}/${nodeId}`);
  render();
}

function back() {
  const here = position();
  if (!here.trail.length) return;
  here.node = here.trail.pop();
  go(state.chart, here.node, { record: false });
}

function restart() {
  const chart = data.charts.get(state.chart);
  state.positions[state.chart] = { node: chart.start, trail: [] };
  go(state.chart, chart.start, { record: false });
}

// ---------------------------------------------------------------------------------------------
// Rendering

function spoken(text, { fill = true } = {}) {
  const fragment = document.createDocumentFragment();
  let last = 0;
  for (const match of text.matchAll(SLOT)) {
    fragment.append(text.slice(last, match.index));
    const value = fill ? (state.values[match[1]] || '').trim() : '';
    fragment.append(value
      ? el('span', { className: 'filled', title: match[1] }, value)
      : el('span', { className: 'slot' }, match[1].toLowerCase().replace(/_/g, ' ')));
    last = match.index + match[0].length;
  }
  fragment.append(text.slice(last));
  return fragment;
}

// "A — or — B" lists alternative transmissions; a single " — " after one adds an explanatory note.
function alternatives(text) {
  return text.split(' — or — ').map(part => {
    const [main, ...note] = part.split(' — ');
    return { main, note: note.join(' — ') };
  });
}

function body(step) {
  const wrap = el('div', { className: `body body-${step.kind}` });
  for (const alt of alternatives(step.text)) {
    wrap.append(el('p', { className: 'line' }, spoken(alt.main)));
    if (alt.note) wrap.append(el('p', { className: 'note' }, spoken(alt.note)));
  }
  return wrap;
}

const KIND_LABEL = { say: 'You say', hear: 'You hear', do: 'Do', hub: 'Check', point: '' };

function tagFor(step) {
  if (step.kind === 'say') return 'You say';
  if (step.kind === 'hear') return step.tag.replace(/^REPLY\b/, 'Reply').replace(/^EVENT\b/, 'Event');
  return step.tag || KIND_LABEL[step.kind];
}

function choicesFor(chart, nodeId) {
  const list = chart.out.get(nodeId).map(edge => ({ chart: chart.id, to: edge.to, label: edge.label, flow: edge.flow }));
  for (const target of chart.jumps[nodeId] || []) {
    const [chartId, to] = target.split(':');
    list.push({ chart: chartId, to, label: `continue in ${data.charts.get(chartId).title.toLowerCase()}`, flow: 'normalFlow' });
  }
  return list;
}

function nextHeading(step, count) {
  if (!count) return 'End of this path';
  if (step.kind === 'say') return 'Then listen for';
  if (step.kind === 'hub') return 'Possible outcomes';
  return 'What happens next';
}

function render() {
  const chart = data.charts.get(state.chart);
  const here = position();
  const step = chart.nodes.get(here.node);
  document.title = `${chart.title} · Radio call walkthrough`;

  for (const tab of document.querySelectorAll('.tabs button')) {
    tab.setAttribute('aria-current', tab.dataset.chart === state.chart ? 'page' : 'false');
  }

  const kind = $('#step-kind');
  kind.textContent = tagFor(step);
  kind.dataset.kind = step.kind;
  $('#step-text').replaceChildren(body(step));
  $('#step-text').dataset.kind = step.kind;
  $('[data-action="back"]').disabled = !here.trail.length;

  const choices = choicesFor(chart, here.node);
  if (!choices.length) choices.push(...endChoices(chart, here, step));
  $('#next-title').textContent = nextHeading(step, choices.some(choice => choice.to) ? choices.length : 0);
  const list = $('.choices');
  list.replaceChildren(...choices.map((choice, index) => {
    const target = choice.to && data.charts.get(choice.chart).nodes.get(choice.to);
    const button = el('button', { type: 'button', className: `choice tone-${TONE[choice.flow] || 'neutral'}` },
      el('span', { className: 'key' }, index < 9 ? String(index + 1) : ''),
      el('span', { className: 'choice-main' },
        choice.label ? el('span', { className: 'when' }, choice.label) : null,
        target
          ? el('span', { className: `target kind-${target.kind}` },
            el('span', { className: 'target-tag' }, tagFor(target)),
            el('span', { className: 'target-text' }, spoken(target.kind === 'say' ? target.text : alternatives(target.text).map(a => a.main).join('  ·  '))))
          : el('span', { className: 'target-text' }, choice.title)));
    button.addEventListener('click', () => (choice.action ? choice.action() : go(choice.chart, choice.to)));
    return el('li', {}, button);
  }));

  renderShortcuts(chart);
  renderTrail(chart, here);
  renderFields(chart);
}

// Status replies (airborne, landed, no session…) end their chart path: resume where the pilot was
// before making that call, or restart once the procedure itself has ended.
function endChoices(chart, here, step) {
  const result = [];
  const lastCall = here.trail.findLastIndex(id => chart.nodes.get(id).kind === 'say');
  const before = lastCall > 0 ? here.trail[lastCall - 1] : null;
  if (step.kind !== 'point' && before) {
    result.push({ chart: chart.id, to: before, label: 'carry on from before that call', flow: 'normalFlow' });
  }
  result.push({ action: restart, title: `Restart ${chart.title.toLowerCase()}`, flow: 'neutralFlow' });
  return result;
}

function hubChildren(chart, test) {
  const seen = new Set();
  const result = [];
  for (const step of chart.nodes.values()) {
    if (step.kind !== 'hub' || !test(step)) continue;
    for (const edge of chart.out.get(step.id)) {
      if (!seen.has(edge.to)) { seen.add(edge.to); result.push(chart.nodes.get(edge.to)); }
    }
  }
  return result;
}

function jumpItem(chart, step) {
  const button = el('button', { type: 'button', className: `jump kind-${step.kind}` },
    el('span', { className: 'target-tag' }, tagFor(step)),
    el('span', { className: 'target-text' }, spoken(alternatives(step.text)[0].main)));
  button.addEventListener('click', () => go(chart.id, step.id));
  return el('li', {}, button);
}

function renderShortcuts(chart) {
  const anytime = hubChildren(chart, step => /^At any/.test(step.text) || /^At any/.test(step.tag));
  const refusals = hubChildren(chart, step => /^(Any YOU call|CHECK every call)/.test(`${step.tag}: ${step.text}`) || /^Any YOU call/.test(step.text));
  $('#anytime').hidden = !anytime.length;
  $('#anytime .jump-list').replaceChildren(...anytime.map(step => jumpItem(chart, step)));
  $('#refusals').hidden = !refusals.length;
  $('#refusals .jump-list').replaceChildren(...refusals.map(step => jumpItem(chart, step)));
}

function renderTrail(chart, here) {
  const steps = [...here.trail, here.node];
  const shown = steps.slice(-14);
  const offset = steps.length - shown.length;
  $('.trail-list').replaceChildren(...shown.map((id, index) => {
    const step = chart.nodes.get(id);
    const current = offset + index === steps.length - 1;
    const button = el('button', { type: 'button', className: `trail-step kind-${step.kind}`, disabled: current },
      el('span', { className: 'target-tag' }, tagFor(step)),
      el('span', { className: 'target-text' }, spoken(alternatives(step.text)[0].main)));
    if (current) button.setAttribute('aria-current', 'step');
    button.addEventListener('click', () => {
      here.trail = steps.slice(0, offset + index);
      go(chart.id, id, { record: false });
    });
    return el('li', {}, button);
  }));
}

let fieldsFamily = null;

function renderFields(chart) {
  if (fieldsFamily === chart.family) return;
  fieldsFamily = chart.family;
  const keys = new Set();
  for (const other of data.charts.values()) {
    if (other.family !== chart.family) continue;
    for (const step of other.nodes.values()) {
      if (step.kind === 'say') for (const match of step.text.matchAll(SLOT)) keys.add(match[1]);
    }
  }
  const known = Object.keys(data.placeholders);
  const ordered = [...keys].sort((a, b) => (known.indexOf(a) + 1 || 99) - (known.indexOf(b) + 1 || 99));
  $('#values .fields').replaceChildren(...ordered.map(key => {
    const meta = data.placeholders[key] || { label: key.toLowerCase().replace(/_/g, ' '), hint: '' };
    const input = el('input', { id: `value-${key}`, type: 'text', value: state.values[key] || '', placeholder: meta.hint, autocomplete: 'off', spellcheck: false });
    input.addEventListener('input', () => {
      state.values[key] = input.value;
      persist();
      const focused = document.activeElement;
      render();
      focused?.focus();
    });
    return el('div', { className: 'field' }, el('label', { htmlFor: input.id }, meta.label), input);
  }));
}

// ---------------------------------------------------------------------------------------------
// Find

function setupFind() {
  const input = $('#find');
  const results = $('.results');
  const search = () => {
    const words = input.value.toLowerCase().split(/\s+/).filter(Boolean);
    if (!words.length) { results.replaceChildren(); return; }
    const hits = [];
    for (const chart of data.charts.values()) {
      for (const step of chart.nodes.values()) {
        if (step.kind === 'hub' || step.kind === 'point') continue;
        const hay = `${step.tag} ${step.text}`.toLowerCase();
        if (words.every(word => hay.includes(word))) hits.push({ chart, step });
      }
    }
    hits.sort((a, b) => (a.chart.id === state.chart ? 0 : 1) - (b.chart.id === state.chart ? 0 : 1)
      || (a.step.kind === 'say' ? 0 : 1) - (b.step.kind === 'say' ? 0 : 1));
    results.replaceChildren(...hits.slice(0, 12).map(({ chart, step }) => {
      const button = el('button', { type: 'button', className: `jump kind-${step.kind}` },
        el('span', { className: 'target-tag' }, `${chart.title} · ${tagFor(step)}`),
        el('span', { className: 'target-text' }, spoken(alternatives(step.text)[0].main)));
      button.addEventListener('click', () => {
        input.value = '';
        results.replaceChildren();
        go(chart.id, step.id);
        $('.choices button')?.focus();
      });
      return el('li', {}, button);
    }));
    if (!hits.length) results.append(el('li', { className: 'empty' }, 'No step matches. Try fewer words.'));
  };
  input.addEventListener('input', search);
  input.addEventListener('keydown', event => {
    if (event.key === 'Escape') { input.value = ''; search(); input.blur(); }
    if (event.key === 'Enter') results.querySelector('button')?.click();
  });
}

// ---------------------------------------------------------------------------------------------
// Startup

function fromHash() {
  const [chartId, nodeId] = decodeURIComponent(location.hash.slice(1)).split('/');
  const chart = data.charts.get(chartId);
  if (!chart) return false;
  state.chart = chartId;
  const here = position(chartId);
  if (nodeId && chart.nodes.has(nodeId) && nodeId !== here.node) {
    here.trail.push(here.node);
    here.node = nodeId;
  }
  return true;
}

function setupTabs() {
  $('.tabs').replaceChildren(...[...data.charts.values()].map(chart => {
    const button = el('button', { type: 'button', textContent: chart.title });
    button.dataset.chart = chart.id;
    button.addEventListener('click', () => go(chart.id, position(chart.id).node, { record: false }));
    return button;
  }));
}

function setupKeys() {
  $('[data-action="back"]').addEventListener('click', back);
  $('[data-action="restart"]').addEventListener('click', restart);
  document.addEventListener('keydown', event => {
    if (event.ctrlKey || event.metaKey || event.altKey) return;
    if (event.target.closest('input, textarea, select')) return;
    if (/^[1-9]$/.test(event.key)) {
      const button = document.querySelectorAll('.choices button')[Number(event.key) - 1];
      if (button) { event.preventDefault(); button.click(); }
    } else if (event.key === 'Backspace') {
      event.preventDefault();
      back();
    } else if (event.key === 'Home') {
      event.preventDefault();
      restart();
    } else if (event.key === '/') {
      event.preventDefault();
      $('#find').focus();
    }
  });
  window.addEventListener('hashchange', () => { if (fromHash()) { persist(); render(); } });
}

try {
  data = await loadCharts();
  setupTabs();
  setupKeys();
  setupFind();
  if (!fromHash()) state.chart = saved.chart && data.charts.has(saved.chart) ? saved.chart : data.charts.keys().next().value;
  go(state.chart, position().node, { record: false });
} catch (error) {
  $('#step-text').replaceChildren(el('p', { className: 'loading' },
    `Walkthrough unavailable: ${error.message}. The phrase guides linked on this page contain the same procedures.`));
}
