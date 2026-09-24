// Run with: node --test gui_v2/js_tests/*.test.cjs
// Exercise the real script with deterministic network/timer/DOM boundaries.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const script = readFileSync(join(__dirname, '../static/gui_v2/digitization_file_picker.js'), 'utf8');
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness() {
  const listeners = new Map(), timers = new Map(), requests = [];
  let current, timerId = 0;
  const document = {
    addEventListener(type, listener, options) {
      const list = listeners.get(type) || [];
      list.push({listener, capture: options?.capture || false});
      listeners.set(type, list);
    },
    querySelectorAll(selector) {
      if (!current?.isConnected) return [];
      if (selector.includes(':not')) return current.dataset.pickerAuto === 'true' ? [] : [current];
      if (selector.includes('picker-auto')) return current.dataset.pickerAuto === 'true' ? [current] : [];
      return [current];
    },
    createElement() { return {dataset: {}, setAttribute() {}}; },
  };
  const context = vm.createContext({
    window: {}, document, location: {origin: 'http://localhost'}, URL, URLSearchParams, AbortController,
    FormData: class { constructor(form) {this.form = form;} *[Symbol.iterator]() {for (const [key, input] of Object.entries(this.form.elements)) yield [key, input.value];} },
    DOMParser: class {parseFromString(section) {return {querySelector: () => section};}},
    fetch(url, options) {
      return new Promise(resolve => requests.push({url, options, resolve}));
    },
    setTimeout(fn) {const id = ++timerId; timers.set(id, fn); return id;},
    clearTimeout(id) {timers.delete(id);},
  });
  function section(batch = 'one', path = '.', names = ['A1.wav', 'A2.wav']) {
    const fields = {root_key: {value: 'masters'}, relative_path: {value: path}, file_q: {value: ''}};
    const count = {}, register = {}, selectAll = {}, kept = [];
    const getForm = {elements: fields, closest: () => node};
    const postForm = {
      querySelectorAll: () => kept.slice(),
      querySelector: selector => selector.includes('count') ? count : register,
      append(input) {input.remove = () => kept.splice(kept.indexOf(input), 1); kept.push(input);},
    };
    const inputs = names.map(value => ({value, checked: false, disabled: false}));
    const node = {
      id: 'master-picker', isConnected: false, hidden: false,
      dataset: {browseUrl: `/batch/${batch}/files/`, pickerAuto: 'false'},
      inputs, fields, count, register, selectAll, kept, replacements: 0,
      querySelector(selector) {
        if (selector.includes("method='get'")) return getForm;
        if (selector.includes('method=post')) return postForm;
        if (selector.includes('select-all')) return selectAll;
        if (selector.includes('picker-search')) return {value: fields.file_q.value, focus() {}, setSelectionRange() {}};
        return null;
      },
      querySelectorAll(selector) {return selector.includes(':not(:disabled)') ? inputs.filter(input => !input.disabled) : inputs;},
      setAttribute() {}, removeAttribute() {}, append(error) {node.error = error;},
      replaceWith(replacement) {node.replacements++; node.isConnected = false; replacement.isConnected = true; current = replacement;},
      form: {...getForm, closest: selector => selector.includes('form') ? node.form : node},
      search: {form: getForm, matches: () => true},
      link: {href: `http://localhost/batch/${batch}/?relative_path=other&root_key=masters`, closest: selector => selector.includes('picker-folder') ? node.link : node},
    };
    return node;
  }
  return {
    section, requests, listeners, timers,
    load() {vm.runInContext(script, context);},
    show(node) {if (current) current.isConnected = false; current = node; node.isConnected = true;},
    emit(type, target, extra = {}) {
      const event = {type, target, button: 0, defaultPrevented: false, preventDefault() {this.defaultPrevented = true;}, ...extra};
      for (const {listener} of [...(listeners.get(type) || [])].sort((a,b) => Number(b.capture) - Number(a.capture))) listener(event);
      return event;
    },
    bubble(type, listener) {document.addEventListener(type, listener);},
    runTimers() {const tasks = [...timers.values()]; timers.clear(); tasks.forEach(fn => fn());},
    async respond(index, replacement) {requests[index].resolve({ok: true, text: async () => replacement}); await flush();},
  };
}

test('re-entering digitization installs one set of handlers and makes one request', () => {
  const h = harness(), section = h.section(); h.show(section);
  h.load(); h.load(); h.load();
  assert.equal(h.listeners.get('submit').length, 1);
  assert.equal(h.listeners.get('digitization:step').length, 1);
  h.emit('submit', section.form);
  assert.equal(h.requests.length, 1);
});

test('picker owns search and folder navigation even when global navigation loaded first', () => {
  const h = harness(), section = h.section(); h.show(section);
  let fullNavigations = 0;
  for (const type of ['click', 'submit']) h.bubble(type, event => {if (!event.defaultPrevented) fullNavigations++;});
  h.load();
  h.emit('submit', section.form);
  h.emit('click', section.link);
  assert.equal(h.requests.length, 2);
  assert.equal(fullNavigations, 0);
  assert.equal(h.requests[0].options.signal.aborted, true);
});

test('submitting search cancels its pending debounce', () => {
  const h = harness(), section = h.section(); h.show(section); h.load();
  h.emit('input', section.search);
  h.emit('submit', section.form);
  h.runTimers();
  assert.equal(h.requests.length, 1);
});

test('opening a folder cancels the old pending search', () => {
  const h = harness(), section = h.section(); h.show(section); h.load();
  h.emit('input', section.search);
  h.emit('click', section.link);
  h.runTimers();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].url.searchParams.get('relative_path'), 'other');
});

test('late aborted responses never replace a newer folder', async () => {
  const h = harness(), section = h.section(); h.show(section); h.load();
  h.emit('submit', section.form);
  h.emit('click', section.link);
  const fresh = h.section('one', 'other');
  await h.respond(1, fresh);
  await h.respond(0, h.section());
  assert.equal(section.replacements, 1);
  assert.equal(fresh.isConnected, true);
});

test('leaving the workspace aborts requests and discards delayed searches', async () => {
  const h = harness(), section = h.section(); h.show(section); h.load();
  h.emit('submit', section.form);
  h.emit('input', section.search);
  h.show(h.section('two')); h.emit('p7:page-changed'); h.runTimers();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].options.signal.aborted, true);
  await h.respond(0, h.section());
  assert.equal(section.replacements, 0);
});

test('selection survives filtering but is isolated between batches', async () => {
  const h = harness(), section = h.section(); h.show(section); h.load();
  section.inputs[0].checked = true;
  h.emit('submit', section.form);
  const filtered = h.section('one', '.', ['A2.wav']);
  await h.respond(0, filtered);
  assert.equal(filtered.kept[0].value, 'A1.wav');
  assert.equal(filtered.register.disabled, false);
  const other = h.section('two', '.', ['A2.wav']); h.show(other); h.load();
  assert.equal(other.kept.length, 0);
  assert.equal(other.register.disabled, true);
});

test('select-all reflects partial selection and excludes unavailable files', () => {
  const h = harness(), section = h.section();
  section.inputs.push({value: 'reserved.wav', checked: false, disabled: true});
  section.inputs[0].checked = true;
  h.show(section); h.load();
  assert.equal(section.selectAll.indeterminate, true);
  section.inputs[1].checked = true; h.load();
  assert.equal(section.selectAll.indeterminate, false);
  assert.equal(section.selectAll.checked, true);
});

test('modifier clicks preserve native browser behavior', () => {
  const h = harness(), section = h.section(); h.show(section); h.load();
  assert.equal(h.emit('click', section.link, {ctrlKey: true}).defaultPrevented, false);
  assert.equal(h.requests.length, 0);
});
