const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

// Exercise the real editor methods without a browser or the external graph library.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.value = ''; this.textContent = '';
    this.classList = { toggle() {}, add() {}, remove() {} };
  }
  set innerHTML(value) { throw new Error('Messages must not enter an HTML parser'); }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren(...children) { this.children = children; }
}
class DataSet {
  constructor() { this.data = new Map(); }
  get(id) { return id === undefined ? [...this.data.values()] : this.data.get(id); }
  getIds() { return [...this.data.keys()]; }
  add(items) { for (const item of Array.isArray(items) ? items : [items]) this.data.set(item.id, {...item}); }
  update(items) { this.add(items); }
  clear() { this.data.clear(); }
}
const panels = new Map();
const context = vm.createContext({
  assert, Element, DataSet, panels,
  window: { addEventListener() {} },
  document: { getElementById(id) { return panels.get(id); }, createElement(tag) { return new Element(tag); } },
});
vm.runInContext(fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8'), context);
vm.runInContext(`
function formFor(fixed) {
  const fields = Object.fromEntries(['states','alphabet','start','accepting','transitions'].map(name => [name,new Element('input')]));
  const dataset = fixed === undefined ? {} : {fixedAlphabet:fixed};
  return {dataset, fields, querySelector(selector) { return fields[selector.match(/name="([^"]+)"/)[1]]; }};
}
function editorFor(form) {
  const editor = Object.create(DfaVisualEditor.prototype);
  Object.assign(editor, {form, nodes:new DataSet(), edges:new DataSet(), history:[], redoStack:[], isRestoring:false,
    network:{getPositions() {return {};}, redraw() {}}, refresh() {}, recomputeEdgeSmooth() {}});
  return editor;
}
const sample = {states:['q0'], alphabet:['a','b'], start:'q0', accepting:['q0'], transitions:{q0:{a:'q0',b:'q0'}}};
for (const fixed of ['a,b', '']) {
  const form = formFor(fixed); const editor = editorFor(form);
  fillFormFromDfa(form, sample); editor.loadFromForm();
  assert.equal(form.fields.alphabet.value, fixed);
  editor.clearAll(); assert.equal(form.fields.alphabet.value, fixed);
  editor.undo(); assert.equal(form.fields.alphabet.value, fixed); assert.equal(form.fields.states.value,'q0');
  editor.redo(); assert.equal(form.fields.alphabet.value, fixed); assert.equal(form.fields.states.value,'');
  fillFormFromDfa(form, {...sample, alphabet:['x']});
  assert.equal(form.fields.alphabet.value, fixed);
  const snapshot = editor.captureState(); snapshot.form.alphabet = 'x';
  editor.restoreState(snapshot); assert.equal(form.fields.alphabet.value, fixed);
  form.fields.alphabet.value='tampered'; editor.loadFromForm();
  assert.equal(form.fields.alphabet.value, fixed);
}
const teacher=formFor(); fillFormFromDfa(teacher,sample); editorFor(teacher).clearAll();
assert.equal(teacher.fields.alphabet.value,'');
const messages = formFor(); fillFormFromDfa(messages,sample);
messages.dataset.validationId='validation'; messages.dataset.sourceStatusId='source';
panels.set('validation',new Element()); panels.set('source',new Element());
const payload='<img src=x onerror="bad()">'; messages.fields.accepting.value=payload;
renderValidation(messages); setSourceStatus(messages,'error',payload,[payload]);
function allText(node) { return node.textContent + node.children.map(allText).join(''); }
function allTags(node) { return [node.tagName,...node.children.flatMap(allTags)]; }
assert.ok(allText(panels.get('validation')).includes(payload));
assert.ok(allText(panels.get('source')).includes(payload));
assert.ok(!allTags(panels.get('validation')).includes('img'));
for (const text of ['q0,a=q1=extra','q0,a,b=q1','q0,a=q1,extra']) {
  const parsed=parseTransitionsDetailed(text); assert.ok(parsed.errors.length); assert.deepEqual(Object.keys(parsed.transitions),[]);
}
const parsed=parseTransitionsDetailed('__proto__,a=__proto__');
assert.equal(parsed.transitions['__proto__'].a,'__proto__');
for (const alphabet of [['ab'],['ε'],['='],['a','a']]) {
  assert.ok(validationSummary({...sample,alphabet},'').fieldErrors.alphabet.length);
}
assert.equal(validationSummary({...sample,alphabet:[],transitions:{}},'').errors.length,0);
`, context);
console.log('Frontend regressions passed: fixed alphabets, clear/undo/redo/import/restore, text messages, delimiters and symbols.');
