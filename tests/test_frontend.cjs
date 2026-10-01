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
const namedDead={states:['q0','__dead__'],alphabet:['a'],start:'q0',accepting:[],transitions:{q0:{a:'__dead__'},__dead__:{a:'__dead__'}}};
assert.ok(partitionSnapshotsJs(namedDead).groups.some(group=>group.includes('__dead__')&&group.includes('q0')));
`, context);
context.setTimeout=setTimeout; context.clearTimeout=clearTimeout;
vm.runInContext(`
(async()=>{
  const alerts=[];
  globalThis.alert=message=>alerts.push(message);
  globalThis.FileReader=class {readAsText(file){this.onload({target:{result:file.data}});}};
  const imported=formFor(); fillFormFromDfa(imported,sample);
  const before=JSON.stringify(Object.fromEntries(Object.entries(imported.fields).map(([key,field])=>[key,field.value])));
  const invalid={states:['changed'],alphabet:['a'],start:'changed',accepting:[],transitions:{changed:null}};
  loadDfaJsonIntoForm({files:[{data:JSON.stringify(invalid)}],value:'selected'},imported);
  assert.ok(alerts.length);
  assert.equal(JSON.stringify(Object.fromEntries(Object.entries(imported.fields).map(([key,field])=>[key,field.value]))),before);
  const fixed=formFor('a,b'); fillFormFromDfa(fixed,sample);
  loadDfaJsonIntoForm({files:[{data:JSON.stringify({...sample,alphabet:['x']})}],value:'selected'},fixed);
  assert.equal(fixed.fields.alphabet.value,'a,b');
  loadDfaJsonIntoForm({files:[{data:JSON.stringify(sample)}],value:'selected'},imported);
  assert.equal(imported.fields.states.value,'q0');

  const preview=formFor(); const select={value:'regex'}, source={value:'a'}, pending=[];
  const baseQuery=preview.querySelector.bind(preview);
  preview.querySelector=selector=>selector==='[data-source-kind]'?select:selector.startsWith('[data-source-input=')?source:baseQuery(selector);
  setDerivedMode=()=>{}; setSourceStatus=()=>{}; renderDfaPreview=()=>{};
  globalThis.fetch=()=>new Promise(resolve=>pending.push(resolve));
  const first=requestSourcePreview(preview);
  source.value='b'; const second=requestSourcePreview(preview);
  const response=alphabet=>({ok:true,json:async()=>({ok:true,dfa:{states:['q0'],alphabet:[alphabet],start:'q0',accepting:[],transitions:{}},summary:{title:'Preview',notes:[]}})});
  pending[1](response('b')); await second;
  pending[0](response('a')); await first;
  assert.equal(preview.fields.alphabet.value,'b','An older preview must not overwrite the current source');
})()
`, context).then(()=>console.log('Frontend regressions passed: editor history, fixed alphabets, safe messages, JSON import, named states and asynchronous source previews.')).catch(error=>{console.error(error);process.exitCode=1;});
