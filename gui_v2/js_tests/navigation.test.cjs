const {test} = require('node:test');
const assert = require('node:assert/strict');
const {readFileSync} = require('node:fs');
const {join} = require('node:path');
const vm = require('node:vm');
const flush = () => new Promise(resolve => setImmediate(resolve));

test('a stale response body cannot overwrite a later navigation or redirect', async () => {
  const requests = [], parsed = [], redirects = [];
  const element = {setAttribute() {}, removeAttribute() {}};
  const window = {};
  const location = {href: 'http://localhost/v2/', origin: 'http://localhost', pathname: '/v2/', search: '', assign(url) {redirects.push(url);}};
  vm.runInNewContext(readFileSync(join(__dirname, '../static/gui_v2/navigation.js'), 'utf8'), {
    window, location, URL, AbortController, performance: {now: () => 0}, console,
    setTimeout: () => 1, clearTimeout() {}, addEventListener() {},
    document: {createElement: () => ({...element}), body: {append() {}}, querySelector: () => element, addEventListener() {}},
    fetch(url) {return new Promise(resolve => requests.push({url, resolve}));},
    DOMParser: class {parseFromString(html) {parsed.push(html); return {body: {dataset: {}}, querySelector: () => null};}},
  });
  let releaseOldBody;
  const oldBody = new Promise(resolve => {releaseOldBody = resolve;});
  const response = text => ({ok: true, headers: {get: () => 'text/html'}, text});
  const oldNavigation = window.P7_V2.navigate('/v2/old/');
  requests[0].resolve(response(() => oldBody));
  await flush();
  const newNavigation = window.P7_V2.navigate('/v2/new/');
  requests[1].resolve(response(async () => 'new page'));
  await newNavigation;
  releaseOldBody('old page');
  await oldNavigation;
  assert.deepEqual(parsed, ['new page']);
  assert.deepEqual(redirects, ['http://localhost/v2/new/']);
});
