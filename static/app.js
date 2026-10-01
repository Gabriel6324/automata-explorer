// Client-side helpers for DFA form parsing, validation and visual editing.
function parseCsv(value) {
  return value.split(',').map(v => v.trim()).filter(Boolean);
}

function uniqueSorted(values) {
  return Array.from(new Set(values.filter(Boolean))).sort();
}

// Parse transition lines and keep validation errors for the form panel.
function parseTransitionsDetailed(text) {
  const lines = text.replaceAll(';', '\n').split('\n').map(v => v.trim()).filter(Boolean);
  const transitions = Object.create(null);
  const errors = [];
  const seen = new Map();
  lines.forEach((line, idx) => {
    if (line.split('=').length !== 2 || line.split(',').length !== 2) {
      errors.push(`Line ${idx + 1}: use format state,symbol=next_state`);
      return;
    }
    const [left, right] = line.split('=');
    const [state, symbol] = left.split(',');
    const src = (state || '').trim();
    const sym = (symbol || '').trim();
    const dst = (right || '').trim();
    if (!src || !sym || !dst) {
      errors.push(`Line ${idx + 1}: missing state, symbol or next_state`);
      return;
    }
    const key = `${src}::${sym}`;
    if (seen.has(key)) {
      errors.push(`Line ${idx + 1}: duplicate transition for (${src}, ${sym})`);
      return;
    }
    seen.set(key, idx + 1);
    if (!transitions[src]) transitions[src] = Object.create(null);
    transitions[src][sym] = dst;
  });
  return { transitions, errors, lines };
}

function parseTransitions(text) {
  return parseTransitionsDetailed(text).transitions;
}

function deepClone(value) {
  return JSON.parse(JSON.stringify(value));
}


// Store the reconnect hint choice for the current browser session.
function reconnectHintSuppressed() {
  try {
    return sessionStorage.getItem('ae_reconnect_hint_suppressed') === '1';
  } catch (e) {
    return false;
  }
}

function setReconnectHintSuppressed(value) {
  try {
    if (value) sessionStorage.setItem('ae_reconnect_hint_suppressed', '1');
    else sessionStorage.removeItem('ae_reconnect_hint_suppressed');
  } catch (e) {}
}

function showReconnectHintDialog(onContinue) {
  if (reconnectHintSuppressed()) {
    onContinue();
    return;
  }
  const existing = document.getElementById('ae-reconnect-hint-overlay');
  if (existing) existing.remove();
  const overlay = document.createElement('div');
  overlay.id = 'ae-reconnect-hint-overlay';
  overlay.className = 'ae-modal-overlay';
  overlay.innerHTML = `
    <div class="ae-modal-card" role="dialog" aria-modal="true" aria-labelledby="ae-reconnect-title">
      <h4 id="ae-reconnect-title">Reconnect mode</h4>
      <p>Select one transition, click <strong>Reconnect transition</strong>, then click the new target state.</p>
      <label class="ae-modal-check"><input type="checkbox" id="ae-reconnect-hide"> Don't show this again this session</label>
      <div class="ae-modal-actions">
        <button type="button" class="secondary" id="ae-reconnect-cancel">Cancel</button>
        <button type="button" id="ae-reconnect-ok">Continue</button>
      </div>
    </div>`;
  document.body.appendChild(overlay);
  const close = () => overlay.remove();
  overlay.querySelector('#ae-reconnect-cancel').addEventListener('click', close);
  overlay.querySelector('#ae-reconnect-ok').addEventListener('click', () => {
    const checked = overlay.querySelector('#ae-reconnect-hide').checked;
    if (checked) setReconnectHintSuppressed(true);
    close();
    onContinue();
  });
  overlay.addEventListener('click', (evt) => {
    if (evt.target === overlay) close();
  });
}

// Style self-loops and normal edges so the graph is easier to read.
function edgeStyleForDirection(edge) {
  const from = edge.from;
  const to = edge.to;
  const baseFont = {
    strokeWidth: 4,
    strokeColor: '#ffffff',
    background: 'rgba(255,255,255,0.68)',
    align: 'horizontal',
  };

  if (from === to) {
    return {
      smooth: { type: 'curvedCW', roundness: 0.5 },
      font: { ...baseFont, vadjust: -14 },
    };
  }

  return {
    smooth: true,
    font: { ...baseFont, vadjust: -2 },
  };
}

function applyBidirectionalSmooth(edges) {
  return edges.map(edge => {
    const style = edgeStyleForDirection(edge);
    return {
      ...edge,
      smooth: style.smooth,
      font: style.font,
    };
  });
}

// Convert structured form fields into the DFA object used by previews.
function setFormAlphabet(form, value) {
  const fixed = form.dataset.fixedAlphabet;
  form.querySelector('[name="alphabet"]').value = fixed === undefined ? value : fixed;
}

function dfaFromForm(form) {
  setFormAlphabet(form, form.querySelector('[name="alphabet"]').value || '');
  return {
    states: parseCsv(form.querySelector('[name="states"]').value || ''),
    alphabet: parseCsv(form.querySelector('[name="alphabet"]').value || ''),
    start: (form.querySelector('[name="start"]').value || '').trim(),
    accepting: parseCsv(form.querySelector('[name="accepting"]').value || ''),
    transitions: parseTransitions(form.querySelector('[name="transitions"]').value || ''),
  };
}

function fillFormFromDfa(form, dfa) {
  if (!form || !dfa) return;
  form.querySelector('[name="states"]').value = (dfa.states || []).join(',');
  setFormAlphabet(form, (dfa.alphabet || []).join(','));
  form.querySelector('[name="start"]').value = dfa.start || '';
  form.querySelector('[name="accepting"]').value = (dfa.accepting || []).join(',');
  const lines = [];
  const edgeLines = [];
  for (const [src, mapping] of Object.entries(dfa.transitions || {})) {
    for (const [sym, dst] of Object.entries(mapping)) {
      edgeLines.push({ src, sym, dst });
    }
  }
  edgeLines.sort((a, b) => (a.src + a.sym + a.dst).localeCompare(b.src + b.sym + b.dst));
  edgeLines.forEach(item => lines.push(`${item.src},${item.sym}=${item.dst}`));
  form.querySelector('[name="transitions"]').value = lines.join('\n');
}

// Convert a DFA object into vis-network nodes and edges.
function dfaToVisData(dfa) {
  const nodes = [];
  const edgeGroups = new Map();
  const accepting = new Set(dfa.accepting || []);
  const states = dfa.states || [];

  for (const state of states) {
    let label = state;
    if (state === dfa.start) label = '→ ' + label;
    if (accepting.has(state)) label = label + ' *';
    nodes.push({ id: state, label, shape: 'box' });
  }

  for (const [src, mapping] of Object.entries(dfa.transitions || {})) {
    for (const [sym, dst] of Object.entries(mapping)) {
      const key = `${src}=>${dst}`;
      if (!edgeGroups.has(key)) edgeGroups.set(key, { from: src, to: dst, symbols: [] });
      edgeGroups.get(key).symbols.push(sym);
    }
  }
  const edges = applyBidirectionalSmooth(Array.from(edgeGroups.values()).map((edge, index) => ({
    id: `edge_${index}_${edge.from}_${edge.to}`,
    from: edge.from,
    to: edge.to,
    label: uniqueSorted(edge.symbols).join(','),
    arrows: 'to',
  })));
  return { nodes, edges };
}

// Render a read-only DFA preview and the matching JSON view.
function renderDfaPreview(dfa, containerId, jsonId) {
  const container = document.getElementById(containerId);
  const jsonBlock = document.getElementById(jsonId);
  if (!container) return null;
  const visData = dfaToVisData(dfa);
  const data = {
    nodes: new vis.DataSet(visData.nodes),
    edges: new vis.DataSet(visData.edges),
  };
  const options = {
    autoResize: true,
    physics: false,
    layout: { improvedLayout: true },
    edges: { font: { align: 'middle' }, smooth: true },
    interaction: { dragNodes: true, dragView: true, zoomView: true },
  };
  container.innerHTML = '';
  const network = new vis.Network(container, data, options);
  setTimeout(() => {
    try {
      network.redraw();
      network.fit({ animation: false, maxZoomLevel: 1.1 });
    } catch (e) {}
  }, 0);
  if (jsonBlock) jsonBlock.textContent = JSON.stringify(dfa, null, 2);
  return network;
}

function renderDfaPreviewFromForm(form, containerId, jsonId) {
  const dfa = dfaFromForm(form);
  return renderDfaPreview(dfa, containerId, jsonId);
}

function showBuilderTab(id, btn) {
  const card = btn.closest('.card') || document;
  card.querySelectorAll('.builder-tab').forEach(el => el.classList.remove('active'));
  card.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  const target = card.querySelector(`#${id}`) || document.getElementById(id);
  if (target) target.classList.add('active');
  btn.classList.add('active');
  const form = btn.form || btn.closest('form');
  if (form && form._dfaEditor) {
    if (id.includes('preview')) {
      form._dfaEditor.syncFormFromEditor();
      renderDfaPreviewFromForm(form, form.dataset.previewId, form.dataset.jsonId);
    } else if (id.includes('visual')) {
      form._dfaEditor.refresh();
    }
  }
}

function downloadDfaJson(form) {
  if (form && form._dfaEditor) form._dfaEditor.syncFormFromEditor();
  const dfa = dfaFromForm(form);
  const blob = new Blob([JSON.stringify(dfa, null, 2)], { type: 'application/json' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'dfa.json';
  a.click();
  URL.revokeObjectURL(a.href);
}

function validatedDfaImport(raw, fixedAlphabet) {
  const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
  if (!record(raw) || !['states','alphabet','accepting'].every(key => Array.isArray(raw[key]) && raw[key].every(value => typeof value === 'string')) || typeof raw.start !== 'string' || !record(raw.transitions)) {
    throw new Error('DFA JSON needs states, alphabet, start, accepting and a transitions object.');
  }
  if (Object.values(raw.transitions).some(mapping => !record(mapping) || Object.values(mapping).some(target => typeof target !== 'string'))) {
    throw new Error('Each transition must map a symbol to a state name.');
  }
  const lines = Object.entries(raw.transitions).flatMap(([src,mapping]) => Object.entries(mapping).map(([symbol,target]) => `${src},${symbol}=${target}`));
  const report = validationSummary(raw, lines.join('\n'));
  if (report.errors.length) throw new Error(report.errors.join('\n'));
  if (fixedAlphabet !== undefined && JSON.stringify([...raw.alphabet].sort()) !== JSON.stringify(parseCsv(fixedAlphabet).sort())) {
    throw new Error('The imported alphabet must match this exercise.');
  }
  return raw;
}

function loadDfaJsonIntoForm(fileInput, form) {
  const file = fileInput.files && fileInput.files[0];
  if (!file || !form) return;
  const reader = new FileReader();
  reader.onload = function(e) {
    try {
      const dfa = validatedDfaImport(JSON.parse(e.target.result), form.dataset.fixedAlphabet);
      if (form._dfaEditor) form._dfaEditor.pushHistory();
      fillFormFromDfa(form, dfa);
      if (form._dfaEditor) form._dfaEditor.loadFromForm();
      const preview = form.dataset.previewId;
      const json = form.dataset.jsonId;
      if (preview && json) renderDfaPreview(dfa, preview, json);
    } catch (err) {
      alert(err.message || 'Invalid JSON file.');
    }
  };
  reader.readAsText(file);
  fileInput.value = '';
}

// Build inline validation messages before the form is submitted.
function validationSummary(dfa, rawText) {
  const states = dfa.states || [];
  const alphabet = dfa.alphabet || [];
  const accepting = dfa.accepting || [];
  const errors = [];
  const warnings = [];
  const tips = [];
  const fieldErrors = { states: [], alphabet: [], start: [], accepting: [], transitions: [] };

  if (!states.length) {
    errors.push('Add at least one state.');
    fieldErrors.states.push('Add at least one state.');
  }
  if (new Set(states).size !== states.length || states.some(state => /[,;=\r\n]/.test(state))) {
    const message = 'State names must be unique and cannot contain commas, semicolons, equals signs or line breaks.';
    errors.push(message);
    fieldErrors.states.push(message);
  }
  if (new Set(alphabet).size !== alphabet.length || alphabet.some(symbol => Array.from(symbol).length !== 1 || /[,;=ε\s]/u.test(symbol))) {
    const message = 'Alphabet symbols must be unique single characters; delimiters and ε are reserved.';
    errors.push(message);
    fieldErrors.alphabet.push(message);
  }
  if (!dfa.start) {
    errors.push('Choose a start state.');
    fieldErrors.start.push('Choose a start state.');
  } else if (!states.includes(dfa.start)) {
    errors.push(`Start state ${dfa.start} is not listed in states.`);
    fieldErrors.start.push('Start state must appear in states.');
  }
  accepting.forEach(state => {
    if (!states.includes(state)) {
      errors.push(`Accepting state ${state} is not listed in states.`);
      fieldErrors.accepting.push(`Unknown accepting state: ${state}`);
    }
  });

  const parsed = parseTransitionsDetailed(rawText || '');
  parsed.errors.forEach(error => {
    errors.push(error);
    fieldErrors.transitions.push(error);
  });

  Object.entries(dfa.transitions || {}).forEach(([src, mapping]) => {
    if (!states.includes(src)) {
      errors.push(`Transition source ${src} is not listed in states.`);
      fieldErrors.transitions.push(`Unknown source: ${src}`);
    }
    Object.entries(mapping).forEach(([sym, dst]) => {
      if (!alphabet.includes(sym)) {
        errors.push(`Symbol ${sym} is not in the alphabet.`);
        fieldErrors.transitions.push(`Unknown symbol: ${sym}`);
      }
      if (!states.includes(dst)) {
        errors.push(`Transition target ${dst} is not listed in states.`);
        fieldErrors.transitions.push(`Unknown target: ${dst}`);
      }
    });
  });

  const missing = [];
  states.forEach(state => {
    alphabet.forEach(symbol => {
      if (!(dfa.transitions[state] && dfa.transitions[state][symbol])) {
        missing.push(`${state} on ${symbol}`);
      }
    });
  });
  if (missing.length) {
    warnings.push(`Missing transitions for ${missing.length} state/symbol pair(s): ${missing.slice(0, 6).join(', ')}${missing.length > 6 ? ', ...' : ''}`);
    tips.push('For a total DFA, every state usually needs one outgoing transition for each symbol.');
  }

  if (dfa.start && states.includes(dfa.start)) {
    const seen = new Set();
    const q = [dfa.start];
    while (q.length) {
      const current = q.shift();
      if (seen.has(current)) continue;
      seen.add(current);
      Object.values(dfa.transitions[current] || {}).forEach(dst => {
        if (!seen.has(dst)) q.push(dst);
      });
    }
    const unreachable = states.filter(state => !seen.has(state));
    if (unreachable.length) {
      warnings.push(`Unreachable states: ${unreachable.join(', ')}`);
      tips.push('Unreachable states do not affect the language and often stop a DFA being minimal.');
    }
  }

  if (!errors.length && dfa.start && states.includes(dfa.start) && alphabet.length) {
    const partitionInfo = partitionSnapshotsJs(dfa);
    if (partitionInfo.groups.length) {
      warnings.push(`Possible merge groups: ${partitionInfo.groups.map(group => `{${group.join(', ')}}`).join(' | ')}`);
      const last = partitionInfo.snapshots[partitionInfo.snapshots.length - 1];
      if (last && last.blocks && last.blocks.length) {
        tips.push(`Stable partition: ${last.blocks.map(block => `{${block.join(', ')}}`).join(' | ')}`);
      }
    }
  }

  if (!errors.length && !warnings.length) {
    tips.push('The structure is well formed. Next check whether it matches the target language and whether any states can be merged.');
  }
  if (states.length && alphabet.length) {
    const transitionCount = Object.values(dfa.transitions || {}).reduce((acc, mapping) => acc + Object.keys(mapping).length, 0);
    tips.push(`Current size: ${states.length} state(s), ${alphabet.length} symbol(s), ${transitionCount}/${states.length * alphabet.length} transitions filled.`);
  }

  return { errors, warnings, tips, fieldErrors };
}

function renderValidation(form) {
  const panelId = form.dataset.validationId;
  const panel = panelId ? document.getElementById(panelId) : null;
  if (!panel) return;
  const dfa = dfaFromForm(form);
  const rawText = form.querySelector('[name="transitions"]').value || '';
  const report = validationSummary(dfa, rawText);

  ['states', 'alphabet', 'start', 'accepting', 'transitions'].forEach(name => {
    const field = form.querySelector(`[name="${name}"]`);
    if (!field) return;
    field.classList.toggle('field-invalid', report.fieldErrors[name].length > 0);
  });

  panel.replaceChildren();
  const heading = document.createElement('h4');
  heading.textContent = 'Inline validation & teaching feedback';
  panel.appendChild(heading);
  [
    ['Errors', report.errors, 'has-errors', 'No blocking errors.'],
    ['Warnings', report.warnings, 'has-warnings', 'No structural warnings.'],
    ['Teaching tips', report.tips, '', ''],
  ].forEach(([label, items, style, empty]) => {
    const section = document.createElement('div');
    section.className = `validation-section ${items.length ? style : ''}`;
    appendTextMessages(section, label, items, empty);
    panel.appendChild(section);
  });
}

function appendTextMessages(container, title, lines, empty = '') {
  const heading = document.createElement('strong');
  heading.textContent = title;
  container.appendChild(heading);
  if (lines.length) {
    const list = document.createElement('ul');
    lines.forEach(line => {
      const item = document.createElement('li');
      item.textContent = line;
      list.appendChild(item);
    });
    container.appendChild(list);
  } else if (empty) {
    const text = document.createElement('p');
    text.className = 'muted';
    text.textContent = empty;
    container.appendChild(text);
  }
}

function reachableStatesJs(dfa) {
  const visited = new Set();
  const queue = [];
  if (dfa.start) queue.push(dfa.start);
  while (queue.length) {
    const state = queue.shift();
    if (!state || visited.has(state)) continue;
    visited.add(state);
    Object.values((dfa.transitions || {})[state] || {}).forEach(dst => {
      if (!visited.has(dst)) queue.push(dst);
    });
  }
  return visited;
}

function completeDfaJs(dfa) {
  const alphabet = uniqueSorted((dfa.alphabet || []).slice());
  const states = Array.from(new Set((dfa.states || []).slice()));
  const transitions = Object.create(null);
  states.forEach(state => {
    transitions[state] = { ...((dfa.transitions || {})[state] || {}) };
  });
  let dead = '__dead__';
  let needDead = false;
  while (states.includes(dead)) dead += '_';
  states.forEach(state => {
    alphabet.forEach(symbol => {
      if (!transitions[state][symbol]) {
        needDead = true;
        transitions[state][symbol] = dead;
      }
    });
  });
  if (needDead) {
    states.push(dead);
    transitions[dead] = {};
    alphabet.forEach(symbol => {
      transitions[dead][symbol] = dead;
    });
  }
  return {
    states,
    alphabet,
    start: dfa.start || '',
    accepting: Array.from(new Set(dfa.accepting || [])),
    transitions,
  };
}

function trimDfaJs(dfa) {
  const seen = reachableStatesJs(dfa);
  return {
    states: (dfa.states || []).filter(state => seen.has(state)),
    alphabet: (dfa.alphabet || []).slice(),
    start: dfa.start || '',
    accepting: (dfa.accepting || []).filter(state => seen.has(state)),
    transitions: Object.fromEntries(
      (dfa.states || [])
        .filter(state => seen.has(state))
        .map(state => [state, Object.fromEntries(Object.entries((dfa.transitions || {})[state] || {}).filter(([, dst]) => seen.has(dst)))])
    ),
  };
}

// Mirror the server-side partition feedback for client-side warnings.
function partitionSnapshotsJs(dfa) {
  const originalStates = new Set(dfa.states || []);
  const cleaned = trimDfaJs(completeDfaJs(dfa));
  if (!cleaned.start || !(cleaned.states || []).length || !(cleaned.alphabet || []).length) {
    return { groups: [], snapshots: [] };
  }
  const states = (cleaned.states || []).slice().sort();
  const accepting = new Set(cleaned.accepting || []);
  let partition = [];
  const acceptingBlock = states.filter(state => accepting.has(state));
  const rejectingBlock = states.filter(state => !accepting.has(state));
  if (acceptingBlock.length) partition.push(acceptingBlock);
  if (rejectingBlock.length) partition.push(rejectingBlock);
  const snapshots = [{ round: 0, blocks: partition.map(block => block.filter(state => originalStates.has(state))).filter(block => block.length) }];
  while (true) {
    const blockIndex = Object.create(null);
    partition.forEach((block, index) => block.forEach(state => { blockIndex[state] = index; }));
    const nextPartition = [];
    let changed = false;
    partition.forEach(block => {
      const grouped = new Map();
      block.forEach(state => {
        const signature = (cleaned.alphabet || []).map(symbol => `${symbol}:${blockIndex[(cleaned.transitions[state] || {})[symbol]]}`).join('|');
        if (!grouped.has(signature)) grouped.set(signature, []);
        grouped.get(signature).push(state);
      });
      const refined = Array.from(grouped.values()).map(group => group.slice().sort()).sort((a, b) => a[0].localeCompare(b[0]));
      if (refined.length > 1) changed = true;
      nextPartition.push(...refined);
    });
    if (!changed) break;
    partition = nextPartition;
    snapshots.push({ round: snapshots.length, blocks: partition.map(block => block.filter(state => originalStates.has(state))).filter(block => block.length) });
  }
  const finalBlocks = partition.map(block => block.filter(state => originalStates.has(state))).filter(block => block.length);
  return {
    groups: finalBlocks.filter(block => block.length > 1),
    snapshots,
  };
}

function setSourceStatus(form, kind, title, lines) {
  const panelId = form.dataset.sourceStatusId;
  const panel = panelId ? document.getElementById(panelId) : null;
  if (!panel) return;
  panel.classList.remove('ok', 'error');
  if (kind) panel.classList.add(kind);
  panel.replaceChildren();
  appendTextMessages(panel, title, lines || []);
}

function setDerivedMode(form, derived) {
  form.querySelectorAll('[data-dfa-lock]').forEach(field => {
    field.readOnly = derived;
    field.classList.toggle('derived-disabled', derived);
  });
  form.querySelectorAll('[data-dfa-edit-btn]').forEach(button => {
    button.disabled = derived;
    button.classList.toggle('derived-disabled', derived);
  });
  const noteId = form.dataset.sourceModeNoteId;
  const note = noteId ? document.getElementById(noteId) : null;
  if (note) note.style.display = derived ? '' : 'none';
}

// Ask the server to generate a target DFA preview for regex or NFA input.
async function requestSourcePreview(form) {
  const select = form.querySelector('[data-source-kind]');
  if (!select) return;
  const version = form._sourcePreviewVersion = (form._sourcePreviewVersion || 0) + 1;
  const value = select.value || 'dfa';
  if (value === 'dfa') {
    setDerivedMode(form, false);
    setSourceStatus(form, '', 'Direct DFA mode', ['Edit the DFA directly in the form or on the canvas below.']);
    if (form._dfaEditor) form._dfaEditor.loadFromForm();
    renderDfaPreviewFromForm(form, form.dataset.previewId, form.dataset.jsonId);
    return;
  }
  setDerivedMode(form, true);
  const input = form.querySelector(`[data-source-input="${value}"]`);
  const sourcePayload = input ? input.value.trim() : '';
  if (!sourcePayload) {
    const emptyLabel = value === 'regex' ? 'Enter a regular expression to generate the DFA preview.' : 'Enter NFA JSON to generate the DFA preview.';
    setSourceStatus(form, '', 'Waiting for source input', [emptyLabel]);
    return;
  }
  setSourceStatus(form, '', 'Generating DFA preview…', []);
  try {
    const response = await fetch('/api/source-preview', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source_kind: value, source_payload: sourcePayload }),
    });
    const payload = await response.json();
    if (version !== form._sourcePreviewVersion) return;
    if (!response.ok || !payload.ok) {
      setSourceStatus(form, 'error', 'Source preview error', payload.errors || ['Could not generate the DFA preview from this source.']);
      return;
    }
    fillFormFromDfa(form, payload.dfa);
    if (form._dfaEditor) form._dfaEditor.loadFromForm();
    renderDfaPreview(payload.dfa, form.dataset.previewId, form.dataset.jsonId);
    setSourceStatus(form, 'ok', payload.summary.title || 'Source preview ready', payload.summary.notes || []);
  } catch (error) {
    if (version !== form._sourcePreviewVersion) return;
    setSourceStatus(form, 'error', 'Source preview error', ['The preview request failed. Check the source and try again.']);
  }
}

function scheduleSourcePreview(form, immediate = false) {
  if (!form) return;
  form._sourcePreviewVersion = (form._sourcePreviewVersion || 0) + 1;
  clearTimeout(form._sourcePreviewTimer);
  if (immediate) {
    requestSourcePreview(form);
    return;
  }
  form._sourcePreviewTimer = setTimeout(() => requestSourcePreview(form), 450);
}

function previewSourceFromButton(btn) {
  const form = btn.form || btn.closest('form');
  scheduleSourcePreview(form, true);
}

function insertRegexSample(btn, value) {
  const form = btn.form || btn.closest('form');
  const field = form.querySelector('[name="regex_source"]');
  if (!field) return;
  field.value = value;
  scheduleSourcePreview(form, true);
}

function insertNfaSample(btn) {
  const form = btn.form || btn.closest('form');
  const field = form.querySelector('[name="nfa_source"]');
  if (!field) return;
  field.value = `{
  "states": ["q0", "q1"],
  "alphabet": ["a", "b"],
  "start": "q0",
  "accepting": ["q1"],
  "transitions": {
    "q0": {"a": ["q0", "q1"], "b": ["q0"]},
    "q1": {"b": ["q1"], "ε": ["q0"]}
  }
}`;
  scheduleSourcePreview(form, true);
}

// Show the source panel that matches the teacher-selected mode.
function updateSourceKindUI(form) {
  if (!form) return;
  const select = form.querySelector('[data-source-kind]');
  if (!select) return;
  const value = select.value || 'dfa';
  form.querySelectorAll('[data-source-panel]').forEach(panel => {
    const active = panel.dataset.sourcePanel === value;
    panel.style.display = active ? '' : 'none';
    panel.classList.toggle('active-source', active);
  });
  scheduleSourcePreview(form, value === 'dfa');
}

// Interactive DFA editor used on exercise forms and student attempts.
class DfaVisualEditor {
  constructor(form, containerId) {
    this.form = form;
    this.container = document.getElementById(containerId);
    this.nodes = new vis.DataSet([]);
    this.edges = new vis.DataSet([]);
    this.history = [];
    this.redoStack = [];
    this.isRestoring = false;
    this.reconnectModeEdgeId = null;
    this.formSyncTimer = null;
    this.storageKey = `dfaEditorBridge:${window.location.pathname}`;
    this.network = new vis.Network(this.container, { nodes: this.nodes, edges: this.edges }, {
      autoResize: true,
      physics: false,
      edges: { font: { align: 'middle' }, smooth: true, selectionWidth: 1 },
      interaction: { dragNodes: true, dragView: true, zoomView: true, multiselect: true },
      manipulation: { enabled: false },
    });
    this.network.on('dragEnd', () => {
      this.recomputeEdgeSmooth();
      this.syncFormFromEditor();
    });
    this.network.on('click', params => {
      if (this.reconnectModeEdgeId) {
        if (params.nodes.length === 1) {
          this.applyReconnectTarget(params.nodes[0]);
          return;
        }
        if (!params.nodes.length || params.edges.includes(this.reconnectModeEdgeId)) {
          this.exitReconnectMode();
          return;
        }
      }
    });
    this.network.on('doubleClick', params => {
      if (params.nodes.length === 1) this.editNode(params.nodes[0]);
      if (params.edges.length === 1) this.editEdge(params.edges[0]);
    });
    const restored = this.restoreReloadBridge();
    if (!restored) this.loadFromForm();
  }

  captureState() {
    const positions = this.network.getPositions(this.nodes.getIds());
    return {
      form: {
        states: this.form.querySelector('[name="states"]').value || '',
        alphabet: this.form.querySelector('[name="alphabet"]').value || '',
        start: this.form.querySelector('[name="start"]').value || '',
        accepting: this.form.querySelector('[name="accepting"]').value || '',
        transitions: this.form.querySelector('[name="transitions"]').value || '',
      },
      nodes: this.nodes.get().map(node => ({ ...node, ...(positions[node.id] || {}) })),
      edges: this.edges.get().map(edge => ({ ...edge })),
    };
  }

  pushHistory() {
    // Save editor state so undo and redo can restore it.
    if (this.isRestoring) return;
    this.history.push(deepClone(this.captureState()));
    this.redoStack = [];
    if (this.history.length > 60) this.history.shift();
  }

  restoreState(snapshot) {
    if (!snapshot) return;
    this.isRestoring = true;
    this.form.querySelector('[name="states"]').value = snapshot.form.states || '';
    setFormAlphabet(this.form, snapshot.form.alphabet || '');
    this.form.querySelector('[name="start"]').value = snapshot.form.start || '';
    this.form.querySelector('[name="accepting"]').value = snapshot.form.accepting || '';
    this.form.querySelector('[name="transitions"]').value = snapshot.form.transitions || '';
    this.nodes.clear();
    this.edges.clear();
    this.nodes.add(snapshot.nodes || []);
    this.edges.add(applyBidirectionalSmooth(snapshot.edges || []));
    this.isRestoring = false;
    this.syncFormFromEditor();
    this.refresh(false);
  }


  saveReloadBridge() {
    try {
      const payload = {
        history: this.history,
        redoStack: this.redoStack,
        current: this.captureState(),
      };
      window.sessionStorage.setItem(this.storageKey, JSON.stringify(payload));
    } catch (e) {}
  }

  restoreReloadBridge() {
    try {
      const raw = window.sessionStorage.getItem(this.storageKey);
      if (!raw) return false;
      window.sessionStorage.removeItem(this.storageKey);
      const payload = JSON.parse(raw);
      this.history = payload.history || [];
      this.redoStack = payload.redoStack || [];
      if (payload.current) {
        this.restoreState(payload.current);
        return true;
      }
    } catch (e) {}
    return false;
  }

  undo() {
    if (!this.history.length) {
      alert('Nothing to undo yet.');
      return;
    }
    this.redoStack.push(deepClone(this.captureState()));
    this.restoreState(this.history.pop());
  }

  redo() {
    if (!this.redoStack.length) {
      alert('Nothing to redo yet.');
      return;
    }
    this.history.push(deepClone(this.captureState()));
    this.restoreState(this.redoStack.pop());
  }

  exitReconnectMode() {
    this.reconnectModeEdgeId = null;
  }

  applyReconnectTarget(targetId) {
    const edge = this.reconnectModeEdgeId ? this.edges.get(this.reconnectModeEdgeId) : null;
    if (!edge || !this.nodes.get(targetId)) {
      this.exitReconnectMode();
      return;
    }
    this.edges.update({ id: edge.id, to: targetId });
    this.recomputeEdgeSmooth();
    this.exitReconnectMode();
    this.syncFormFromEditor();
    this.refresh(false);
  }

  scheduleLoadFromForm() {
    clearTimeout(this.formSyncTimer);
    this.formSyncTimer = setTimeout(() => {
      if (!this.isRestoring) {
        this.pushHistory();
        this.loadFromForm();
      }
    }, 250);
  }

  makeNodeLabel(raw, isStart, isAccepting) {
    let label = raw;
    if (isStart) label = `→ ${label}`;
    if (isAccepting) label = `${label} *`;
    return label;
  }

  getNode(id) {
    return this.nodes.get(id);
  }

  edgeLabel(symbols) {
    return uniqueSorted(symbols).join(',');
  }

  findEdge(from, to) {
    return this.edges.get().find(edge => edge.from === from && edge.to === to);
  }

  recomputeEdgeSmooth() {
    const positions = this.network.getPositions(this.nodes.getIds());
    const updated = applyBidirectionalSmooth(this.edges.get().map(edge => ({ ...edge })), positions);
    this.edges.update(updated);
  }

  removeSymbolFromOtherEdges(from, symbol, exceptEdgeId = null) {
    this.edges.get().forEach(edge => {
      if (edge.from !== from || edge.id === exceptEdgeId) return;
      const symbols = (edge.symbols || []).filter(item => item !== symbol);
      if (!symbols.length) {
        this.edges.remove(edge.id);
      } else if (symbols.length !== (edge.symbols || []).length) {
        this.edges.update({ id: edge.id, symbols, label: this.edgeLabel(symbols) });
      }
    });
  }

  addSymbolsToEdge(from, to, symbols, edgeId = null) {
    let edge = edgeId ? this.edges.get(edgeId) : this.findEdge(from, to);
    const symbolSet = new Set(edge && edge.symbols ? edge.symbols : []);
    symbols.forEach(symbol => {
      this.removeSymbolFromOtherEdges(from, symbol, edge ? edge.id : null);
      symbolSet.add(symbol);
    });
    const cleanSymbols = uniqueSorted(Array.from(symbolSet));
    if (edge) {
      this.edges.update({
        id: edge.id,
        from,
        to,
        symbols: cleanSymbols,
        label: this.edgeLabel(cleanSymbols),
        arrows: 'to',
      });
    } else {
      this.edges.add({
        id: `edge_${Date.now()}_${Math.random().toString(16).slice(2)}`,
        from,
        to,
        symbols: cleanSymbols,
        label: this.edgeLabel(cleanSymbols),
        arrows: 'to',
      });
    }
    this.recomputeEdgeSmooth();
  }

  loadFromForm() {
    // Rebuild the graph from the structured text fields.
    const dfa = dfaFromForm(this.form);
    const previousPositions = {};
    this.nodes.get().forEach(node => {
      const pos = this.network.getPositions([node.id])[node.id];
      if (pos) previousPositions[node.id] = pos;
    });
    this.nodes.clear();
    this.edges.clear();
    (dfa.states || []).forEach((state, index) => {
      const pos = previousPositions[state] || { x: index * 120, y: 0 };
      this.nodes.add({
        id: state,
        rawLabel: state,
        isStart: state === dfa.start,
        isAccepting: (dfa.accepting || []).includes(state),
        label: this.makeNodeLabel(state, state === dfa.start, (dfa.accepting || []).includes(state)),
        shape: 'box',
        x: pos.x,
        y: pos.y,
      });
    });
    const grouped = new Map();
    Object.entries(dfa.transitions || {}).forEach(([src, mapping]) => {
      Object.entries(mapping).forEach(([sym, dst]) => {
        const key = `${src}=>${dst}`;
        if (!grouped.has(key)) grouped.set(key, { from: src, to: dst, symbols: [] });
        grouped.get(key).symbols.push(sym);
      });
    });
    Array.from(grouped.values()).forEach(edge => {
      this.edges.add({
        id: `edge_${Date.now()}_${Math.random().toString(16).slice(2)}`,
        from: edge.from,
        to: edge.to,
        symbols: uniqueSorted(edge.symbols),
        label: this.edgeLabel(edge.symbols),
        arrows: 'to',
      });
    });
    this.recomputeEdgeSmooth();
    this.refresh(false);
    renderValidation(this.form);
  }

  getDfa() {
    const nodes = this.nodes.get();
    const states = nodes.map(node => node.rawLabel || node.id);
    const accepting = nodes.filter(node => node.isAccepting).map(node => node.rawLabel || node.id);
    const startNode = nodes.find(node => node.isStart);
    const transitions = Object.create(null);
    this.edges.get().forEach(edge => {
      const source = this.getNode(edge.from);
      const target = this.getNode(edge.to);
      const src = source ? (source.rawLabel || source.id) : edge.from;
      const dst = target ? (target.rawLabel || target.id) : edge.to;
      (edge.symbols || parseCsv(edge.label || '')).forEach(symbol => {
        transitions[src] = transitions[src] || Object.create(null);
        transitions[src][symbol] = dst;
      });
    });
    return {
      states,
      alphabet: parseCsv(this.form.querySelector('[name="alphabet"]').value || ''),
      start: startNode ? (startNode.rawLabel || startNode.id) : '',
      accepting,
      transitions,
    };
  }

  syncFormFromEditor() {
    // Write graph changes back into the structured text fields.
    const dfa = this.getDfa();
    fillFormFromDfa(this.form, dfa);
    renderValidation(this.form);
  }

  refresh(doFit = false) {
    this.network.redraw();
    if (doFit) {
      try { this.network.fit({ animation: false, maxZoomLevel: 1.1 }); } catch (e) {}
    }
  }

  addState() {
    // Add a new state with a unique label.
    const raw = prompt('New state name (for example q2)');
    const name = (raw || '').trim();
    if (!name) return;
    if (this.nodes.get(name)) {
      alert('That state already exists.');
      return;
    }
    this.pushHistory();
    this.nodes.add({ id: name, rawLabel: name, isStart: false, isAccepting: false, label: name, shape: 'box' });
    if (!this.form.querySelector('[name="start"]').value.trim()) {
      this.setStartById(name);
    }
    this.syncFormFromEditor();
    this.refresh();
  }

  editNode(nodeId) {
    const node = this.getNode(nodeId);
    if (!node) return;
    const raw = prompt('Rename state', node.rawLabel || node.id);
    const name = (raw || '').trim();
    if (!name || name === node.id) return;
    if (this.nodes.get(name)) {
      alert('Another state already uses that name.');
      return;
    }
    this.pushHistory();
    const connected = this.edges.get().map(edge => {
      if (edge.from === node.id) edge.from = name;
      if (edge.to === node.id) edge.to = name;
      return edge;
    });
    this.nodes.remove(node.id);
    this.nodes.add({ ...node, id: name, rawLabel: name, label: this.makeNodeLabel(name, node.isStart, node.isAccepting) });
    this.edges.clear();
    this.edges.add(applyBidirectionalSmooth(connected));
    this.syncFormFromEditor();
  }

  selectedNodeIds() {
    return this.network.getSelectedNodes();
  }

  selectedEdgeIds() {
    return this.network.getSelectedEdges();
  }

  setStartById(nodeId) {
    this.nodes.get().forEach(node => {
      this.nodes.update({ id: node.id, isStart: node.id === nodeId, label: this.makeNodeLabel(node.rawLabel || node.id, node.id === nodeId, !!node.isAccepting) });
    });
    this.syncFormFromEditor();
  }

  setStart() {
    const ids = this.selectedNodeIds();
    if (ids.length !== 1) {
      alert('Select exactly one state, then click “Set start”.');
      return;
    }
    this.pushHistory();
    this.setStartById(ids[0]);
  }

  toggleAccepting() {
    const ids = this.selectedNodeIds();
    if (!ids.length) {
      alert('Select one or more states, then click “Toggle accepting”.');
      return;
    }
    this.pushHistory();
    ids.forEach(id => {
      const node = this.getNode(id);
      const next = !node.isAccepting;
      this.nodes.update({ id, isAccepting: next, label: this.makeNodeLabel(node.rawLabel || node.id, !!node.isStart, next) });
    });
    this.syncFormFromEditor();
  }

  connectSelected() {
    // Create or update a transition between selected states.
    const ids = this.selectedNodeIds();
    if (!ids.length || ids.length > 2) {
      alert('Select one state for a self-loop or two states for a transition.');
      return;
    }
    const from = ids[0];
    const to = ids[1] || ids[0];
    const raw = prompt('Symbol label(s), comma-separated', 'a');
    const symbols = uniqueSorted(parseCsv(raw || ''));
    if (!symbols.length) return;
    this.pushHistory();
    this.addSymbolsToEdge(from, to, symbols);
    this.syncFormFromEditor();
    this.refresh(false);
  }

  editEdge(edgeId) {
    const edge = this.edges.get(edgeId);
    if (!edge) return;
    const raw = prompt('Edit symbol label(s), comma-separated', (edge.symbols || []).join(','));
    if (raw === null) return;
    const symbols = uniqueSorted(parseCsv(raw));
    this.pushHistory();
    if (!symbols.length) {
      this.edges.remove(edgeId);
    } else {
      edge.symbols = [];
      this.edges.update(edge);
      this.addSymbolsToEdge(edge.from, edge.to, symbols, edgeId);
    }
    this.syncFormFromEditor();
  }

  editSelectedTransition() {
    const ids = this.selectedEdgeIds();
    if (ids.length !== 1) {
      alert('Select exactly one transition, then click “Edit transition”.');
      return;
    }
    this.editEdge(ids[0]);
  }

  deleteSelected() {
    const edgeIds = this.selectedEdgeIds();
    const nodeIds = this.selectedNodeIds();
    if (!edgeIds.length && !nodeIds.length) {
      alert('Select a state or transition to delete.');
      return;
    }
    this.pushHistory();
    this.edges.remove(edgeIds);
    this.edges.get().forEach(edge => {
      if (nodeIds.includes(edge.from) || nodeIds.includes(edge.to)) this.edges.remove(edge.id);
    });
    this.nodes.remove(nodeIds);
    if (!this.nodes.get().some(node => node.isStart) && this.nodes.length) {
      const first = this.nodes.get()[0];
      this.nodes.update({ id: first.id, isStart: true, label: this.makeNodeLabel(first.rawLabel || first.id, true, !!first.isAccepting) });
    }
    this.syncFormFromEditor();
    this.refresh(false);
  }

  reconnectSelected() {
    const edgeIds = this.selectedEdgeIds();
    if (edgeIds.length !== 1) {
      alert('Select exactly one transition, then click “Reconnect transition”.');
      return;
    }
    if (this.reconnectModeEdgeId === edgeIds[0]) {
      this.exitReconnectMode();
      return;
    }
    const activate = () => {
      this.pushHistory();
      this.reconnectModeEdgeId = edgeIds[0];
    };
    showReconnectHintDialog(activate);
  }

  saveAction() {
    this.syncFormFromEditor();
    this.saveReloadBridge();
    if (this.form.requestSubmit) this.form.requestSubmit();
    else this.form.submit();
  }

  autoLayout() {
    this.pushHistory();
    const enabled = { physics: { enabled: true, barnesHut: { springLength: 180, springConstant: 0.02 } } };
    this.network.setOptions(enabled);
    this.network.once('stabilizationIterationsDone', () => {
      this.network.setOptions({ physics: false });
      this.recomputeEdgeSmooth();
      this.syncFormFromEditor();
      this.refresh(true);
    });
    this.network.stabilize(120);
  }

  clearAll() {
    this.pushHistory();
    this.nodes.clear();
    this.edges.clear();
    this.form.querySelector('[name="states"]').value = '';
    setFormAlphabet(this.form, '');
    this.form.querySelector('[name="start"]').value = '';
    this.form.querySelector('[name="accepting"]').value = '';
    this.form.querySelector('[name="transitions"]').value = '';
    renderValidation(this.form);
    this.refresh(false);
  }
}

// Attach previews, validation and the visual editor to one form.
function initDfaEditor(form) {
  if (!form || form._dfaEditor) return;
  const editorId = form.dataset.editorId;
  if (editorId) {
    form._dfaEditor = new DfaVisualEditor(form, editorId);
  }
  form.querySelectorAll('[data-dfa-sync]').forEach(field => {
    field.addEventListener('input', () => {
      renderValidation(form);
      if (form._dfaEditor && !form._dfaEditor.isRestoring) form._dfaEditor.scheduleLoadFromForm();
    });
    field.addEventListener('change', () => {
      renderValidation(form);
      if (form._dfaEditor && !form._dfaEditor.isRestoring) form._dfaEditor.scheduleLoadFromForm();
    });
  });
  form.querySelectorAll('[data-source-input]').forEach(field => {
    field.addEventListener('input', () => scheduleSourcePreview(form));
    field.addEventListener('change', () => scheduleSourcePreview(form, true));
  });
  form.addEventListener('submit', () => {
    const sourceSelect = form.querySelector('[data-source-kind]');
    const derived = sourceSelect && (sourceSelect.value || 'dfa') !== 'dfa';
    if (form._dfaEditor && !derived) {
      form._dfaEditor.syncFormFromEditor();
      form._dfaEditor.saveReloadBridge();
    }
  });
  renderValidation(form);
}


// Button wrappers keep the HTML templates simple.
function previewFromButton(btn) {
  const form = btn.form || btn.closest('form');
  if (form && form._dfaEditor) form._dfaEditor.syncFormFromEditor();
  const card = btn.closest('.card');
  const tabBtn = card ? card.querySelectorAll('.tab-btn')[1] : null;
  if (tabBtn) showBuilderTab('preview-tab', tabBtn);
  renderDfaPreviewFromForm(form, form.dataset.previewId, form.dataset.jsonId);
}

function addStateFromButton(btn) { btn.form._dfaEditor.addState(); }
function setStartFromButton(btn) { btn.form._dfaEditor.setStart(); }
function toggleAcceptingFromButton(btn) { btn.form._dfaEditor.toggleAccepting(); }
function connectStatesFromButton(btn) { btn.form._dfaEditor.connectSelected(); }
function editTransitionFromButton(btn) { btn.form._dfaEditor.editSelectedTransition(); }
function reconnectTransitionFromButton(btn) { btn.form._dfaEditor.reconnectSelected(); }
function deleteSelectionFromButton(btn) { btn.form._dfaEditor.deleteSelected(); }
function autoLayoutFromButton(btn) { btn.form._dfaEditor.autoLayout(); }
function undoEditorFromButton(btn) { btn.form._dfaEditor.undo(); }
function redoEditorFromButton(btn) { btn.form._dfaEditor.redo(); }
function clearAllFromButton(btn) { btn.form._dfaEditor.clearAll(); }
function saveEditorFromButton(btn) { btn.form._dfaEditor.saveAction(); }
function syncVisualToText(btn) { btn.form._dfaEditor.syncFormFromEditor(); }
function syncTextToVisual(btn) { btn.form._dfaEditor.pushHistory(); btn.form._dfaEditor.loadFromForm(); }

window.addEventListener('load', function() {
  document.querySelectorAll('form[data-dfa-form="true"]').forEach(form => {
    initDfaEditor(form);
    updateSourceKindUI(form);
  });
});
