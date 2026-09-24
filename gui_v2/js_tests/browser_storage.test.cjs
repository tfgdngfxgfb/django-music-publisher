const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const read = name => readFileSync(join(__dirname, '../static/gui_v2', name), 'utf8');

test('denied storage access returns defaults without stopping application initialization', () => {
  const window = {};
  for (const name of ['localStorage', 'sessionStorage']) Object.defineProperty(window, name, {get() {throw new Error('Denied');}});
  vm.runInNewContext(read('browser_storage.js'), {window});
  for (const store of Object.values(window.P7_V2.storage)) {
    assert.equal(store.getItem('volume'), null);
    assert.equal(store.setItem('volume', '1'), false);
    assert.equal(store.removeItem('volume'), false);
  }
});

test('quota errors are reported while readable preferences remain available', () => {
  const window = {localStorage: {getItem: () => '0.5', setItem() {throw new Error('Quota exceeded');}, removeItem() {}}};
  vm.runInNewContext(read('browser_storage.js'), {window});
  assert.equal(window.P7_V2.storage.local.getItem('volume'), '0.5');
  assert.equal(window.P7_V2.storage.local.setItem('volume', '1'), false);
  assert.equal(window.P7_V2.storage.local.removeItem('volume'), true);
});

test('personal settings reject invalid saved volumes while preserving mute', () => {
  for (const saved of ['broken', 'Infinity', '-0.5', '1.5', null, '0', '0.5']) {
    const output = {}, volume = {addEventListener() {}}, form = {
      dataset: {userScope: '1'}, elements: {theme: {}, volume, auto_next: {}},
      querySelector: selector => selector.includes('volume') ? output : {}, addEventListener() {},
    };
    vm.runInNewContext(read('settings.js'), {
      document: {querySelector: () => form, documentElement: {dataset: {v2Theme: 'dark'}}},
      localStorage: {getItem: key => key.includes('volume') ? saved : null},
    });
    assert.equal(volume.value, saved === '0' ? 0 : saved === '0.5' ? 50 : 100);
  }
});
