/* Tests for Code.gs, the Google Apps Script unsubscribe page. Run:  node apps_script/test_unsubscribe.js
 * The script is loaded with SIMULATED Google services (Sheets, locks, properties, HMAC, HTML output), so everything it decides is tested here. What cannot be tested here is Google
 * itself: run setupCheck and selfTest inside Google's editor, then click through a test email (see the set-up steps at the top of Code.gs). */
'use strict';
const assert = require('assert');
const crypto = require('crypto');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const CODE = fs.readFileSync(path.join(__dirname, 'Code.gs'), 'utf8');
const SECRET = 'test-secret-1234567890';
const PLAIN = 'eyJhIjoiMTIgRXhhbXBsZSBTdHJlZXQsIE5ldyBZb3JrLCBOWSIsImIiOiJLZWxzb24gQWdlbmN5IiwiYyI6IlNwcmluZ19MYXVuY2giLCJlIjoiYW5uQGV4YW1wbGUuY29tIiwiayI6ImtlbHNvbmFnZW5jeSIsImwiOiJMLTE3IiwidiI6MX0.KTXW_9O4mzmdoFM_vQyaNHSGio7Q71CUfhZwxGNpCY0';
const UNICODE_TEST = 'eyJhIjoiUnVlIGRlIGwnw4l0w6kgMyIsImIiOiJDYWbDqSDDnG7Dr2NvZGUg4piVIiwiYyI6IkNhZsOpX8OJdMOpIiwiZSI6Inpvw6tAZXhhbXBsZS5jb20iLCJrIjoiY2FmZXVuaWNvZGUiLCJsIjoiTC0xOCIsInQiOjEsInYiOjF9.1FZsRhrf_nyhBH6RIbwX_5qcvbp6jFiVNnJYHUccPdc';

// ---- an independent implementation of the engine's link, so the checks are not circular
function b64url(buffer) { return Buffer.from(buffer).toString('base64').replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); }
function makeToken(secret, fields) {
  const payload = Object.assign({ v: 1, c: 'Spring_Launch', l: 'L-1', e: 'ann@example.com', b: 'Kelson Agency', k: 'kelsonagency', a: '12 Example Street' }, fields || {});
  const sorted = {}; Object.keys(payload).sort().forEach((key) => { sorted[key] = payload[key]; });
  const body = b64url(Buffer.from(JSON.stringify(sorted), 'utf8'));
  return body + '.' + b64url(crypto.createHmac('sha256', secret).update(body, 'ascii').digest());
}

// ---- simulated Google services
function makeWorld(options) {
  options = options || {};
  const world = { sheets: {}, opened: [], locks: { waited: 0, released: 0 }, logs: [], failWrite: false };
  const signed = (buffer) => Array.from(buffer, (b) => (b > 127 ? b - 256 : b));
  const unsigned = (bytes) => Buffer.from(bytes.map((b) => b & 255));
  function makeSheet(rows) {
    const sheet = { rows, frozen: 0 };
    sheet.appendRow = (row) => { if (world.failWrite) { throw new Error('write failed'); } sheet.rows.push(row.slice()); };
    sheet.getDataRange = () => ({ getValues: () => (sheet.rows.length ? sheet.rows.map((r) => r.slice()) : [['']]) });
    sheet.getRange = (r, c) => ({ setValue: (v) => { sheet.rows[r - 1][c - 1] = v; } });
    sheet.setFrozenRows = (n) => { sheet.frozen = n; };
    return sheet;
  }
  world.makeSheet = makeSheet;
  const properties = options.props === undefined ? { UNSUBSCRIBE_SECRET: SECRET, SHEET_ID: 'SHEET123' } : options.props;
  const context = {
    Utilities: {
      computeHmacSha256Signature: (value, key) => signed(crypto.createHmac('sha256', key).update(value, 'utf8').digest()),
      base64EncodeWebSafe: (bytes) => unsigned(bytes).toString('base64').replace(/\+/g, '-').replace(/\//g, '_'),
      base64DecodeWebSafe: (text) => {
        if (text.length % 4 !== 0) { throw new Error('Invalid base64: padding required'); }          // be as strict as a decoder may be, so missing padding cannot go unnoticed
        return signed(Buffer.from(text.replace(/-/g, '+').replace(/_/g, '/'), 'base64'));
      },
      newBlob: (bytes) => ({ getDataAsString: () => unsigned(bytes).toString('utf8') }),
      formatDate: (date) => date.toISOString().slice(0, 19).replace('T', ' '),
    },
    PropertiesService: { getScriptProperties: () => ({ getProperty: (key) => (key in properties ? properties[key] : null) }) },
    SpreadsheetApp: {
      openById: (id) => {
        world.opened.push(id);
        return { getSheetByName: (name) => world.sheets[name] || null, insertSheet: (name) => { world.sheets[name] = makeSheet([]); return world.sheets[name]; } };
      },
    },
    LockService: { getScriptLock: () => ({ waitLock: () => { world.locks.waited++; }, releaseLock: () => { world.locks.released++; } }) },
    HtmlService: { createHtmlOutput: (html) => { const out = { html, title: '', meta: {}, setTitle(t) { out.title = t; return out; }, addMetaTag(k, v) { out.meta[k] = v; return out; }, getContent: () => html }; return out; } },
    ContentService: { createTextOutput: (text) => ({ getContent: () => text }) },
    Logger: { log: (message) => world.logs.push(message) },
    console,
  };
  world.run = vm.createContext(context);
  vm.runInContext(CODE, world.run);
  world.call = (name, ...args) => world.run[name](...args);
  return world;
}

const HEADER = ['Timestamp', 'Email', 'Brand', 'BrandKey', 'Campaign', 'LeadID', 'Reason', 'Details', 'Source'];
function withTab(world, rows) { world.sheets.Unsubscribes = world.makeSheet([HEADER.slice()].concat(rows || [])); return world.sheets.Unsubscribes; }
function plain(value) { return JSON.parse(JSON.stringify(value)); }      // arrays made inside the simulated Google sandbox are compared by content
function rowsOf(world) { return world.sheets.Unsubscribes ? world.sheets.Unsubscribes.rows.slice(1) : []; }
function get(world, token) { return world.call('doGet', { parameter: token === undefined ? {} : { t: token } }).getContent(); }
function submit(world, token, reason, details) { return world.call('submitUnsubscribe', token, reason, details); }
function post(world, token, extra) { return world.call('doPost', { parameter: Object.assign({ t: token, 'List-Unsubscribe': 'One-Click' }, extra || {}) }).getContent(); }

const tests = [];
function test(name, fn) { tests.push([name, fn]); }

// ================================================================ the links
test('the two known-answer links made by the Python engine are read exactly', () => {
  const w = makeWorld();
  const plain = w.call('verifyToken_', PLAIN, SECRET);
  assert.deepStrictEqual(JSON.parse(JSON.stringify(plain)), { v: 1, c: 'Spring_Launch', l: 'L-17', e: 'ann@example.com', b: 'Kelson Agency', k: 'kelsonagency', a: '12 Example Street, New York, NY' });
  const unicode = w.call('verifyToken_', UNICODE_TEST, SECRET);
  assert.strictEqual(unicode.b, 'Café Ünïcode ☕'); assert.strictEqual(unicode.a, "Rue de l'Été 3"); assert.strictEqual(unicode.t, 1);
});
test('a link made by an independent JavaScript implementation is read too', () => {
  const w = makeWorld(); const payload = w.call('verifyToken_', makeToken(SECRET, { b: 'Dube Robe', k: 'duberobe', l: 'L-9' }), SECRET);
  assert.strictEqual(payload.b, 'Dube Robe'); assert.strictEqual(payload.l, 'L-9');
});
test('the Python and JavaScript implementations make the very same link', () => {
  assert.strictEqual(makeToken(SECRET, { l: 'L-17', c: 'Spring_Launch', b: 'Kelson Agency', k: 'kelsonagency', a: '12 Example Street, New York, NY' }), PLAIN);
});
['flip_payload', 'flip_signature', 'drop_signature', 'extra_part', 'empty', 'no_dot', 'swap', 'truncate', 'append'].forEach((damage) => {
  test('an altered link is refused: ' + damage, () => {
    const [body, signature] = PLAIN.split('.');
    const altered = { flip_payload: body.slice(0, 10) + (body[10] === 'A' ? 'B' : 'A') + body.slice(11) + '.' + signature, flip_signature: body + '.' + (signature[0] === 'A' ? 'B' : 'A') + signature.slice(1),
      drop_signature: body + '.', extra_part: PLAIN + '.x', empty: '', no_dot: body + signature, swap: signature + '.' + body, truncate: PLAIN.slice(0, -3), append: PLAIN + 'A' }[damage];
    assert.strictEqual(makeWorld().call('verifyToken_', altered, SECRET), null);
  });
});
test('a link signed with another secret, or garbage, is refused and never throws', () => {
  const w = makeWorld();
  [[PLAIN, 'another-secret-0987654321'], [null, SECRET], [undefined, SECRET], [5, SECRET], ['a.b', SECRET], ['....', SECRET], [PLAIN, ''], ['x'.repeat(5000), SECRET]].forEach(([token, secret]) => assert.strictEqual(w.call('verifyToken_', token, secret), null));
});
test('a correctly signed record of the wrong shape or version is refused', () => {
  const w = makeWorld();
  [{ v: 2 }, { v: '1' }, { e: 5 }, { c: null }, { k: undefined }].forEach((bad) => assert.strictEqual(w.call('verifyToken_', makeToken(SECRET, bad), SECRET), null));
});

// ================================================================ opening the page
test('opening a valid link shows the confirm page with the masked address, the brand and the four reasons, and records nothing', () => {
  const w = makeWorld(); withTab(w);
  const html = get(w, PLAIN);
  assert.ok(html.includes('a***@example.com') && html.includes('<b>Kelson Agency</b>') && html.includes('KELSON AGENCY'));
  ['Too many emails', 'Not relevant to me', 'I never signed up', 'Other'].forEach((reason) => assert.ok(html.includes('value="' + reason + '"')));
  assert.ok(html.includes('Unsubscribe me') && html.includes('No, keep me subscribed') && html.includes('12 Example Street, New York, NY'));
  assert.ok(!html.includes('ann@example.com') && rowsOf(w).length === 0 && w.locks.waited === 0);
});
test('the page sets its title and a mobile viewport', () => {
  const w = makeWorld(); const out = w.call('doGet', { parameter: { t: PLAIN } });
  assert.strictEqual(out.title, 'Unsubscribe'); assert.ok(out.meta.viewport.includes('width=device-width'));
});
test('a brand name full of markup is shown as plain text', () => {
  const w = makeWorld(); const html = get(w, makeToken(SECRET, { b: '<script>alert(1)</script>"><img src=x onerror=1>', a: '</div><b>x</b>' }));
  const lower = html.toLowerCase();          // the top band shows the brand in capitals, so look for it in either case
  assert.ok(!lower.includes('<script>alert(1)') && !lower.includes('<img src=x') && !html.includes('</div><b>x</b>') && html.includes('&lt;script&gt;alert(1)&lt;/script&gt;') && html.includes('&lt;SCRIPT&gt;ALERT(1)&lt;/SCRIPT&gt;'));
});
test('a link with no brand uses neutral wording', () => {
  const w = makeWorld(); const html = get(w, makeToken(SECRET, { b: '', a: '' }));
  assert.ok(html.includes('stop receiving emails from this sender at') && html.includes('>UNSUBSCRIBE<') && !html.includes('class="ft"'));
});
test('an invalid, missing or wrongly signed link shows the not-valid page and records nothing', () => {
  const w = makeWorld(); withTab(w);
  [undefined, '', 'garbage', PLAIN.slice(0, -2), makeToken('another-secret-0987654321', {})].forEach((token) => {
    const html = get(w, token);
    assert.ok(html.includes('This link is not valid') && html.includes('reply <b>unsubscribe</b>') && html.includes('No information was recorded.'));
  });
  assert.strictEqual(rowsOf(w).length, 0);
});
test('a page that is not set up says so and offers the email way', () => {
  [undefined, {}, { UNSUBSCRIBE_SECRET: SECRET }, { SHEET_ID: 'S' }, { UNSUBSCRIBE_SECRET: 'short', SHEET_ID: 'S' }].forEach((props) => {
    const w = makeWorld({ props: props || {} }); const html = get(w, PLAIN);
    assert.ok(html.includes('not available right now') && html.includes('reply <b>unsubscribe</b>') && !html.includes('Unsubscribe me'));
  });
});
test('a test link shows a clear banner', () => {
  const w = makeWorld(); const html = get(w, UNICODE_TEST);
  assert.ok(html.includes('This is a test link from your team. Nothing will be recorded.'));
  assert.ok(!get(w, PLAIN).includes('This is a test link'));
});
test('the confirm button calls the server with the signed link and writes the answer with plain text, not markup', () => {
  const w = makeWorld(); const html = get(w, PLAIN);
  assert.ok(html.includes('google.script.run') && html.includes('.submitUnsubscribe(TOKEN,reason,el("details").value)') && html.includes('var TOKEN="' + PLAIN + '"'));
  assert.ok(html.includes('textContent') && !html.includes('innerHTML'));
});
test('even a token holding script-closing text cannot break out of the page script', () => {
  const w = makeWorld(); const html = w.call('confirmBody_', { b: 'B', e: 'a@x.com', a: '' }, '</script><script>alert(1)</script>');
  assert.ok(!html.includes('</script><script>alert(1)') && html.includes('\\u003c/script') && html.split('</script>').length === 2);
});
test('the token placed in the page cannot break out of its script', () => {
  const w = makeWorld(); const html = get(w, PLAIN);
  assert.ok(!/var TOKEN="[^"]*[<>'\\]/.test(html));
});

// ================================================================ confirming
test('confirming records one row with every column, creating the tab and its header when missing', () => {
  const w = makeWorld();
  const result = submit(w, PLAIN, 'Too many emails', 'Weekly is too much');
  assert.deepStrictEqual(JSON.parse(JSON.stringify(result)), { ok: true, masked: 'a***@example.com', brand: 'Kelson Agency', address: '12 Example Street, New York, NY', already: false, test: false });
  assert.deepStrictEqual(plain(w.sheets.Unsubscribes.rows[0]), HEADER); assert.strictEqual(w.sheets.Unsubscribes.frozen, 1); assert.deepStrictEqual(w.opened, ['SHEET123']);
  const [row] = rowsOf(w);
  assert.ok(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(row[0]));
  assert.deepStrictEqual(plain(row.slice(1)), ['ann@example.com', 'Kelson Agency', 'kelsonagency', 'Spring_Launch', 'L-17', 'Too many emails', 'Weekly is too much', 'page']);
});
test('the reason is optional', () => {
  const w = makeWorld(); withTab(w); submit(w, PLAIN, '', ''); submit(w, makeToken(SECRET, { e: 'b@x.com' }), undefined, undefined);
  assert.deepStrictEqual(rowsOf(w).map((r) => [r[1], r[6], r[7]]), [['ann@example.com', '', ''], ['b@x.com', '', '']]);
});
test('a reason that is not one of the four is ignored, not saved', () => {
  const w = makeWorld(); withTab(w); submit(w, PLAIN, 'Because I said so', 'x');
  assert.strictEqual(rowsOf(w)[0][6], '');
});
test('a repeat for the same address and brand adds no row, whatever the address case', () => {
  const w = makeWorld(); withTab(w);
  submit(w, makeToken(SECRET, { e: 'Ann@Example.com' }), '', '');
  const again = submit(w, makeToken(SECRET, { e: 'ann@example.COM', l: 'L-99' }), '', '');
  assert.strictEqual(rowsOf(w).length, 1); assert.strictEqual(again.already, true);
});
test('a repeat can fill in a reason that was left empty but never overwrites one', () => {
  const w = makeWorld(); withTab(w); submit(w, PLAIN, '', '');
  submit(w, PLAIN, 'I never signed up', 'not me');
  assert.deepStrictEqual([rowsOf(w)[0][6], rowsOf(w)[0][7]], ['I never signed up', 'not me']);
  submit(w, PLAIN, 'Too many emails', 'changed my mind');
  assert.deepStrictEqual([rowsOf(w)[0][6], rowsOf(w)[0][7]], ['I never signed up', 'not me']);
  assert.strictEqual(rowsOf(w).length, 1);
});
test('the same address opting out of a different brand is a different row', () => {
  const w = makeWorld(); withTab(w);
  submit(w, makeToken(SECRET, { k: 'duberobe', b: 'DubeRobe' }), '', '');
  const second = submit(w, makeToken(SECRET, { k: 'swoveralls', b: 'Swoveralls' }), '', '');
  assert.strictEqual(rowsOf(w).length, 2); assert.strictEqual(second.already, false);
  assert.deepStrictEqual(rowsOf(w).map((r) => r[3]), ['duberobe', 'swoveralls']);
});
test('a test link works fully but records nothing and never opens the sheet', () => {
  const w = makeWorld();
  const result = submit(w, UNICODE_TEST, 'Other', 'x');
  assert.strictEqual(result.test, true); assert.strictEqual(result.masked, 'z***@example.com');
  assert.strictEqual(w.opened.length, 0); assert.strictEqual(w.locks.waited, 0); assert.ok(!w.sheets.Unsubscribes);
});
test('typed text that would run as a spreadsheet formula is saved as plain text', () => {
  const w = makeWorld(); withTab(w);
  submit(w, makeToken(SECRET, { b: '=HYPERLINK("http://evil","x")', c: '+cmd', l: '@SUM(1)', e: '-1@x.com' }), 'Other', '=IMPORTXML("http://evil")');
  const row = rowsOf(w)[0];
  assert.ok(row[2].startsWith("'=") && row[4].startsWith("'+") && row[5].startsWith("'@") && row[1].startsWith("'-") && row[7].startsWith("'="));
});
test('typed text is cleaned of control characters and limited in length', () => {
  const w = makeWorld(); withTab(w);
  submit(w, PLAIN, 'Other', 'line one\nline two\u0000\u202e\t  spaced   ' + 'x'.repeat(900));
  const details = rowsOf(w)[0][7];
  assert.ok(details.length <= 500 && !/[\u0000-\u001f\u202e]/.test(details) && details.startsWith('line one line two spaced'));
});
test('the write happens inside the lock and the lock is released even if the write fails', () => {
  const w = makeWorld(); withTab(w); submit(w, PLAIN, '', '');
  assert.deepStrictEqual(w.locks, { waited: 1, released: 1 });
  w.failWrite = true;
  assert.throws(() => submit(w, makeToken(SECRET, { e: 'b@x.com' }), '', ''), /write failed/);
  assert.deepStrictEqual(w.locks, { waited: 2, released: 2 });
});
test('a bad link is refused when confirming and nothing is written', () => {
  const w = makeWorld(); withTab(w);
  assert.throws(() => submit(w, PLAIN.slice(0, -1), '', ''), /not valid/); assert.throws(() => submit(w, undefined, '', ''), /not valid/);
  assert.strictEqual(rowsOf(w).length, 0);
});
test('confirming when the page is not set up fails clearly', () => {
  const w = makeWorld({ props: {} }); assert.throws(() => submit(w, PLAIN, '', ''), /not set up/);
});
test('a tab with a missing column is refused rather than written into wrongly', () => {
  const w = makeWorld(); w.sheets.Unsubscribes = w.makeSheet([['Timestamp', 'Brand']]);
  assert.throws(() => submit(w, PLAIN, '', ''), /no "Email" column/); assert.strictEqual(w.sheets.Unsubscribes.rows.length, 1); assert.deepStrictEqual(w.locks, { waited: 1, released: 1 });
});
test('columns are found by name, so a tab with extra or re-ordered columns still works', () => {
  const w = makeWorld(); w.sheets.Unsubscribes = w.makeSheet([['Notes'].concat(HEADER.slice().reverse()).concat(['Extra'])]);
  submit(w, PLAIN, 'Other', 'd');
  const row = w.sheets.Unsubscribes.rows[1]; const header = w.sheets.Unsubscribes.rows[0];
  assert.strictEqual(row[header.indexOf('Email')], 'ann@example.com'); assert.strictEqual(row[header.indexOf('Source')], 'page'); assert.strictEqual(row.length, header.length);
});

// ================================================================ the mail app's one-tap unsubscribe
test('a mail app one-tap post records a row marked as one-click', () => {
  const w = makeWorld(); withTab(w);
  assert.strictEqual(post(w, PLAIN), 'Unsubscribed');
  assert.deepStrictEqual(plain(rowsOf(w)[0].slice(1)), ['ann@example.com', 'Kelson Agency', 'kelsonagency', 'Spring_Launch', 'L-17', '', '', 'one-click']);
});
test('one-tap posts are idempotent and per brand', () => {
  const w = makeWorld(); withTab(w); post(w, PLAIN); post(w, PLAIN); post(w, makeToken(SECRET, { k: 'other' }));
  assert.strictEqual(rowsOf(w).length, 2);
});
test('a post with a bad link, or without the one-click marker, records nothing', () => {
  const w = makeWorld(); withTab(w);
  assert.strictEqual(post(w, PLAIN.slice(0, -1)), 'Invalid link'); assert.strictEqual(post(w, undefined), 'Invalid link');
  assert.strictEqual(w.call('doPost', { parameter: { t: PLAIN } }).getContent(), 'Unsupported request');
  assert.strictEqual(w.call('doPost', { parameter: { t: PLAIN, 'List-Unsubscribe': 'something else' } }).getContent(), 'Unsupported request');
  assert.strictEqual(w.call('doPost', {}).getContent(), 'Invalid link'); assert.strictEqual(w.call('doPost', undefined).getContent(), 'Invalid link');
  assert.strictEqual(rowsOf(w).length, 0);
});
test('a one-tap post for a test link records nothing and still answers', () => {
  const w = makeWorld(); assert.strictEqual(post(w, UNICODE_TEST), 'Unsubscribed'); assert.ok(!w.sheets.Unsubscribes);
});
test('a one-tap post never throws, even when the sheet fails', () => {
  const w = makeWorld(); withTab(w); w.failWrite = true;
  assert.strictEqual(post(w, PLAIN), 'Error'); assert.deepStrictEqual(w.locks, { waited: 1, released: 1 });
});
test('a one-tap post when the page is not set up answers plainly', () => {
  assert.strictEqual(post(makeWorld({ props: {} }), PLAIN), 'Not set up');
});

// ================================================================ the helpers
test('an address is masked the same way the engine masks it', () => {
  const w = makeWorld();
  [['ann@gmail.com', 'a***@gmail.com'], ['a@x.com', 'a***@x.com'], ['zoë@x.com', 'z***@x.com'], ['', '***'], ['nobody', '***'], ['@x.com', '***'], ['a@', '***'], [null, '***']].forEach(([input, shown]) => assert.strictEqual(w.call('maskEmail_', input), shown));
});
test('a cell that starts like a formula gets an apostrophe and nothing else does', () => {
  const w = makeWorld();
  ['=1', '+1', '-1', '@a', '\tx', '\rx'].forEach((text) => assert.strictEqual(w.call('safeCell_', text), "'" + text));
  ['hello', 'a=b', '', 'x+y'].forEach((text) => assert.strictEqual(w.call('safeCell_', text), text));
  assert.strictEqual(w.call('safeCell_', null), '');
});
test('html escaping covers every character that matters', () => {
  assert.strictEqual(makeWorld().call('escapeHtml_', `<a href="x" onclick='y'>&</a>`), '&lt;a href=&quot;x&quot; onclick=&#39;y&#39;&gt;&amp;&lt;/a&gt;');
});
test('the tab, columns and reasons match the Python engine exactly', () => {
  const w = makeWorld(); const py = fs.readFileSync(path.join(__dirname, '..', 'unsubscribe_logic.py'), 'utf8');
  assert.strictEqual(w.run.TAB_NAME, 'Unsubscribes'); assert.deepStrictEqual(Array.from(w.run.COLUMNS), HEADER); assert.deepStrictEqual(Array.from(w.run.REASONS), ['Too many emails', 'Not relevant to me', 'I never signed up', 'Other']);
  assert.ok(py.includes('UNSUBSCRIBE_TAB = "Unsubscribes"') && py.includes(JSON.stringify(HEADER).replace(/","/g, '", "')) && py.includes(JSON.stringify(Array.from(w.run.REASONS)).replace(/","/g, '", "')));
});

// ================================================================ the checks run by hand in Google's editor
test('selfTest passes and logs it', () => {
  const w = makeWorld(); assert.ok(w.call('selfTest').startsWith('PASS')); assert.ok(w.logs[0].startsWith('PASS'));
});
test('setupCheck passes with a good set-up and creates the tab', () => {
  const w = makeWorld(); assert.ok(w.call('setupCheck').startsWith('PASS')); assert.ok(w.sheets.Unsubscribes);
});
test('setupCheck says exactly what is missing', () => {
  assert.ok(makeWorld({ props: {} }).call('setupCheck').includes('UNSUBSCRIBE_SECRET is missing or shorter than 16') && makeWorld({ props: {} }).call('setupCheck').includes('SHEET_ID is missing'));
  assert.ok(makeWorld({ props: { UNSUBSCRIBE_SECRET: 'short', SHEET_ID: 'S' } }).call('setupCheck').startsWith('PROBLEM'));
  const w = makeWorld(); w.sheets.Unsubscribes = w.makeSheet([['Timestamp']]); assert.ok(w.call('setupCheck').includes('has no "Email" column'));
});

let failed = 0;
tests.forEach(([name, fn]) => {
  try { fn(); console.log('PASS  ' + name); } catch (error) { failed++; console.log('FAIL  ' + name + '\n      ' + String(error && error.message).split('\n')[0]); }
});
console.log('\n' + (tests.length - failed) + ' of ' + tests.length + ' passed');
process.exit(failed ? 1 : 0);
