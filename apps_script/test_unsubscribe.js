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
  const world = { sheets: {}, opened: [], locks: { waited: 0, tried: 0, released: 0 }, logs: [], failWrite: false, lockBusy: false };
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
    LockService: { getScriptLock: () => ({ waitLock: () => { world.locks.waited++; }, tryLock: () => { world.locks.tried++; return !world.lockBusy; }, releaseLock: () => { world.locks.released++; } }) },
    HtmlService: { createHtmlOutput: (html) => { const out = { html, title: '', meta: {}, setTitle(t) { out.title = t; return out; }, addMetaTag(k, v) { out.meta[k] = v; return out; }, getContent: () => html }; return out; } },
    ContentService: { createTextOutput: (text) => ({ mime: '', getContent: () => text, setMimeType(mime) { this.mime = mime; return this; } }), MimeType: { JSON: 'JSON' } },
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
test('the page opens for ANY recipient address: the recipient never decides whether it works', () => {
  const w = makeWorld(); withTab(w);
  ['ann@example.com', 'first.last+tag@sub.example.co.uk', 'UPPER@EXAMPLE.COM', 'zoë@exämple.com', 'x@y.io', 'a'.repeat(60) + '@' + 'b'.repeat(60) + '.com', "o'brien@example.com", 'a_b-c@example.org'].forEach((address) => {
    const html = get(w, makeToken(SECRET, { e: address, l: 'L-' + address.length }));
    assert.ok(html.includes('Unsubscribe me') && html.includes('This link is not valid') === false, address);
    assert.strictEqual(submit(w, makeToken(SECRET, { e: address }), '', '').ok, true);
  });
  assert.strictEqual(rowsOf(w).length, 8);
});
test('the bare address with nothing after it shows the not-valid card, never an error, so it can be used to check the deployment from outside', () => {
  const w = makeWorld(); withTab(w);
  [{ parameter: {} }, {}, undefined, { parameter: { t: '' } }, { parameter: null }, { parameter: { other: 'x' } }].forEach((request) => {
    const html = w.call('doGet', request).getContent();
    assert.ok(html.includes('This link is not valid') && html.includes('reply <b>unsubscribe</b>') && !html.includes('Unsubscribe me'));
  });
  assert.strictEqual(rowsOf(w).length, 0);
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
  assert.deepStrictEqual(plain(w.sheets.Unsubscribes.rows[0]), HEADER); assert.strictEqual(w.sheets.Unsubscribes.frozen, 1); assert.ok(w.opened.length > 0 && w.opened.every((id) => id === 'SHEET123'));
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
  assert.deepStrictEqual(plain(w.locks), { waited: 1, tried: 0, released: 1 });
  w.failWrite = true;
  assert.throws(() => submit(w, makeToken(SECRET, { e: 'b@x.com' }), '', ''), /write failed/);
  assert.deepStrictEqual(plain(w.locks), { waited: 2, tried: 0, released: 2 });
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
  assert.throws(() => submit(w, PLAIN, '', ''), /no "Email" column/); assert.strictEqual(w.sheets.Unsubscribes.rows.length, 1); assert.deepStrictEqual(plain(w.locks), { waited: 1, tried: 0, released: 1 });
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
  assert.strictEqual(post(w, PLAIN), 'Error'); assert.deepStrictEqual(plain(w.locks), { waited: 1, tried: 0, released: 1 });
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

// ================================================================ the optional troubleshooting log (DIAGNOSTICS = on)
const DIAG_HEADER = ['Time', 'Event', 'State', 'Brand', 'Campaign', 'QueryLength', 'TokenLength', 'ParamNames'];
function diagRows(world) { return world.sheets['Unsubscribe diagnostics'] ? world.sheets['Unsubscribe diagnostics'].rows.slice(1).map((r) => plain(r)) : []; }
function diagWorld(extra) { return makeWorld({ props: Object.assign({ UNSUBSCRIBE_SECRET: SECRET, SHEET_ID: 'SHEET123', DIAGNOSTICS: 'on' }, extra || {}) }); }
function open(world, token, queryString) { return world.call('doGet', { parameter: token === undefined ? {} : { t: token }, queryString: queryString === undefined ? (token === undefined ? '' : 't=' + token) : queryString }); }

test('diagnostics are OFF by default: opening the page writes no sheet and takes no lock, and adds no beacon', () => {
  const w = makeWorld(); const html = open(w, PLAIN).getContent();
  assert.deepStrictEqual(Object.keys(w.sheets), []); assert.deepStrictEqual(plain(w.locks), { waited: 0, tried: 0, released: 0 }); assert.ok(!w.sheets['Unsubscribe diagnostics'] && !html.includes('recordRendered'));
});
test('any value but "on" keeps diagnostics off', () => {
  ['', 'off', 'ON?', 'yes', 'true', '0'].forEach((value) => { const w = diagWorld({ DIAGNOSTICS: value }); open(w, PLAIN); assert.strictEqual(diagRows(w).length, 0, value); });
  const w = diagWorld({ DIAGNOSTICS: '  On ' }); open(w, PLAIN); assert.strictEqual(diagRows(w).length, 1);
});
test('with diagnostics on, opening a valid link adds one row that says what the visit looked like', () => {
  const w = diagWorld(); open(w, PLAIN);
  assert.deepStrictEqual(plain(w.sheets['Unsubscribe diagnostics'].rows[0]), DIAG_HEADER);
  const [row] = diagRows(w); assert.ok(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(row[0]));
  assert.deepStrictEqual(row.slice(1), ['opened', 'valid', 'Kelson Agency', 'Spring_Launch', ('t=' + PLAIN).length, PLAIN.length, 't']);
});
test('a test link, an invalid link and the bare address are each recorded with their own state', () => {
  const w = diagWorld(); open(w, UNICODE_TEST); open(w, PLAIN.slice(0, -2)); open(w, undefined);
  assert.deepStrictEqual(diagRows(w).map((r) => [r[1], r[2], r[3], r[4], r[6], r[7]]), [['opened', 'test', 'Café Ünïcode ☕', 'Café_Été', UNICODE_TEST.length, 't'], ['opened', 'invalid', '', '', PLAIN.length - 2, 't'], ['opened', 'invalid', '', '', 0, '']]);
});
test('a page that is not set up is recorded as such when a Sheet is known', () => {
  const w = makeWorld({ props: { SHEET_ID: 'SHEET123', DIAGNOSTICS: 'on' } }); open(w, PLAIN);
  assert.deepStrictEqual(diagRows(w).map((r) => [r[1], r[2]]), [['opened', 'not set up']]);
  const none = makeWorld({ props: { DIAGNOSTICS: 'on' } }); assert.doesNotThrow(() => open(none, PLAIN)); assert.deepStrictEqual(none.opened, []);
});
test('the query length reveals a link that arrived shorter than it was sent', () => {
  const w = diagWorld(); open(w, PLAIN, 't=' + PLAIN.slice(0, 40));
  assert.strictEqual(diagRows(w)[0][5], 42);
});
test('the log never holds an address, a lead, a link or anything typed', () => {
  const w = diagWorld(); open(w, PLAIN); submit(w, PLAIN, 'Too many emails', 'my private comment'); post(w, makeToken(SECRET, { e: 'zed@secret.example', l: 'L-SECRET' }));
  const everything = JSON.stringify(w.sheets['Unsubscribe diagnostics'].rows);
  ['ann@example.com', 'zed@secret.example', 'L-17', 'L-SECRET', 'my private comment', 'Too many emails', PLAIN.slice(0, 30), '12 Example Street'].forEach((secret) => assert.ok(!everything.includes(secret), secret));
});
test('the page carries a beacon only while diagnostics are on, and the beacon records that the page really ran', () => {
  const w = diagWorld(); const html = open(w, PLAIN).getContent();
  assert.ok(html.includes('google.script.run.withFailureHandler(function(){}).recordRendered("valid")'));
  assert.ok(open(w, UNICODE_TEST).getContent().includes('recordRendered("test")') && open(w, 'garbage').getContent().includes('recordRendered("invalid")'));
  assert.strictEqual(w.call('recordRendered', 'valid'), true);
  assert.deepStrictEqual(diagRows(w).filter((r) => r[1] === 'rendered').map((r) => [r[2], r[3], r[4]]), [['valid', '', '']]);
});
test('the beacon cannot be used to write anything but the four known states', () => {
  const w = diagWorld();
  ['<script>', 'x', '', undefined, null, 'VALID', 'valid; DROP'].forEach((state) => assert.strictEqual(w.call('recordRendered', state), false));
  assert.strictEqual(diagRows(w).length, 0);
  ['valid', 'invalid', 'test', 'not set up'].forEach((state) => assert.strictEqual(w.call('recordRendered', state), true));
  assert.strictEqual(diagRows(w).length, 4);
});
test('confirming and the one-tap post are logged as events of their own', () => {
  const w = diagWorld(); withTab(w); submit(w, PLAIN, '', ''); post(w, makeToken(SECRET, { e: 'b@x.com' })); submit(w, UNICODE_TEST, '', ''); post(w, UNICODE_TEST); post(w, 'garbage');
  assert.deepStrictEqual(diagRows(w).map((r) => [r[1], r[2]]), [['confirmed', 'valid'], ['master', 'no tab'], ['one-tap', 'valid'], ['master', 'no tab'], ['confirmed', 'test'], ['one-tap', 'test'], ['one-tap', 'invalid']]);
});
test('a diagnostics failure never changes what the visitor gets or what is recorded', () => {
  const w = diagWorld(); withTab(w); w.failWrite = true;
  assert.ok(open(w, PLAIN).getContent().includes('Unsubscribe me'));
  w.failWrite = false; const real = submit(w, PLAIN, 'Other', 'x'); assert.strictEqual(real.ok, true); assert.strictEqual(rowsOf(w).length, 1);
  w.failWrite = true; assert.strictEqual(post(w, UNICODE_TEST), 'Unsubscribed');
});
test('a busy lock skips the log row quietly and the page is still served', () => {
  const w = diagWorld(); w.lockBusy = true;
  assert.ok(open(w, PLAIN).getContent().includes('Unsubscribe me')); assert.strictEqual(diagRows(w).length, 0); assert.strictEqual(w.locks.tried, 1);
  assert.strictEqual(w.locks.released, 0);
});
test('the log tab and its header are made once and the Unsubscribes tab is never touched by it', () => {
  const w = diagWorld(); open(w, PLAIN); open(w, PLAIN); open(w, 'x');
  assert.strictEqual(w.sheets['Unsubscribe diagnostics'].rows.length, 4); assert.ok(!w.sheets.Unsubscribes); assert.strictEqual(w.sheets['Unsubscribe diagnostics'].frozen, 1);
});
test('a lock taken for the log is always released, even when the log write fails', () => {
  const w = diagWorld(); w.failWrite = true; open(w, PLAIN);
  assert.strictEqual(w.locks.tried, 1); assert.strictEqual(w.locks.released, 1);
});
test('the troubleshooting steps are written into the script', () => {
  assert.ok(CODE.includes('DIAGNOSTICS = on') && CODE.includes('no new row            = the request never reached this script') && CODE.includes('"opened" + "rendered"'));
});

// ================================================================ the JSON interface used by the page on GitHub Pages
function info(world, token, extra) { return world.call('doGet', { parameter: Object.assign(token === undefined ? { api: 'info' } : { api: 'info', t: token }, extra || {}), queryString: 'api=info' + (token === undefined ? '' : '&t=' + token) }); }
function infoJson(world, token) { return JSON.parse(info(world, token).getContent()); }
function confirmCall(world, body, type) { return world.call('doPost', { parameter: {}, postData: { contents: typeof body === 'string' ? body : JSON.stringify(body), type: type || 'text/plain' } }); }
function confirmJson(world, body) { return JSON.parse(confirmCall(world, body).getContent()); }

test('info answers with data, as JSON, never as a page', () => {
  const w = makeWorld(); withTab(w); const out = info(w, PLAIN);
  assert.strictEqual(out.mime, 'JSON'); assert.ok(!out.getContent().includes('<'));
  assert.deepStrictEqual(JSON.parse(out.getContent()), { ok: true, state: 'valid', brand: 'Kelson Agency', masked: 'a***@example.com', address: '12 Example Street, New York, NY', reasons: ['Too many emails', 'Not relevant to me', 'I never signed up', 'Other'], test: false, already: false });
});
test('info never reveals the full address, the lead or the link, and never changes anything', () => {
  const w = makeWorld(); withTab(w); const text = info(w, PLAIN).getContent();
  ['ann@example.com', 'L-17', PLAIN.slice(0, 30), 'Spring_Launch'].forEach((secret) => assert.ok(!text.includes(secret), secret));
  assert.strictEqual(rowsOf(w).length, 0); assert.deepStrictEqual(plain(w.locks), { waited: 0, tried: 0, released: 0 });
});
test('info marks a test link as a test', () => {
  const j = infoJson(makeWorld(), UNICODE_TEST); assert.strictEqual(j.state, 'test'); assert.strictEqual(j.test, true); assert.strictEqual(j.brand, 'Café Ünïcode ☕');
});
test('info says invalid, with no details, for a wrong, missing or empty link', () => {
  const w = makeWorld();
  [undefined, '', 'garbage', PLAIN.slice(0, -2), makeToken('another-secret-0987654321', {})].forEach((token) => assert.deepStrictEqual(infoJson(w, token), { ok: false, state: 'invalid' }));
});
test('info says "not set up" when the script is not configured', () => {
  assert.deepStrictEqual(infoJson(makeWorld({ props: {} }), PLAIN), { ok: false, state: 'not set up' });
});
test('info has the same brand and wording data for every brand, taken from the signed link only', () => {
  const j = infoJson(makeWorld(), makeToken(SECRET, { b: '<b>Evil</b>', a: '"><img>', e: 'zed@secret.example' }));
  assert.strictEqual(j.brand, '<b>Evil</b>'); assert.strictEqual(j.masked, 'z***@secret.example');                         // returned as DATA: the page shows it as text
});
test('confirm records one row from the page, like the Google page does', () => {
  const w = makeWorld(); withTab(w);
  const j = confirmJson(w, { api: 'confirm', t: PLAIN, reason: 'Too many emails', details: 'weekly is too much' });
  assert.deepStrictEqual(j, { ok: true, masked: 'a***@example.com', brand: 'Kelson Agency', address: '12 Example Street, New York, NY', already: false, test: false });
  assert.deepStrictEqual(plain(rowsOf(w)[0].slice(1)), ['ann@example.com', 'Kelson Agency', 'kelsonagency', 'Spring_Launch', 'L-17', 'Too many emails', 'weekly is too much', 'page']);
});
test('confirming twice adds no second row and a later reason fills an empty one', () => {
  const w = makeWorld(); withTab(w);
  confirmJson(w, { api: 'confirm', t: PLAIN }); const again = confirmJson(w, { api: 'confirm', t: PLAIN, reason: 'Other', details: 'x' });
  assert.strictEqual(again.already, true); assert.strictEqual(rowsOf(w).length, 1); assert.strictEqual(rowsOf(w)[0][6], 'Other');
});
test('confirm is per brand and ignores a reason that is not one of the four', () => {
  const w = makeWorld(); withTab(w);
  confirmJson(w, { api: 'confirm', t: makeToken(SECRET, { k: 'duberobe' }), reason: 'Because' }); confirmJson(w, { api: 'confirm', t: makeToken(SECRET, { k: 'swoveralls' }) });
  assert.deepStrictEqual(plain(rowsOf(w).map((r) => [r[3], r[6]])), [['duberobe', ''], ['swoveralls', '']]);
});
test('confirming a test link records nothing', () => {
  const w = makeWorld(); const j = confirmJson(w, { api: 'confirm', t: UNICODE_TEST, reason: 'Other' });
  assert.strictEqual(j.ok, true); assert.strictEqual(j.test, true); assert.ok(!w.sheets.Unsubscribes); assert.strictEqual(w.opened.length, 0);
});
test('confirm refuses a wrong link and records nothing', () => {
  const w = makeWorld(); withTab(w);
  [PLAIN.slice(0, -1), '', undefined, 5, null, makeToken('another-secret-0987654321', {})].forEach((t) => assert.deepStrictEqual(confirmJson(w, { api: 'confirm', t }), { ok: false, error: 'invalid' }));
  assert.strictEqual(rowsOf(w).length, 0);
});
test('confirm when the script is not set up says so', () => {
  assert.deepStrictEqual(confirmJson(makeWorld({ props: {} }), { api: 'confirm', t: PLAIN }), { ok: false, error: 'not set up' });
});
test('a sheet failure while confirming is reported as failed, never thrown, and the lock is released', () => {
  const w = makeWorld(); withTab(w); w.failWrite = true;
  assert.deepStrictEqual(confirmJson(w, { api: 'confirm', t: PLAIN }), { ok: false, error: 'failed' }); assert.deepStrictEqual(plain(w.locks), { waited: 1, tried: 0, released: 1 });
});
test('typed text from the page is cleaned exactly like the Google page cleans it', () => {
  const w = makeWorld(); withTab(w);
  confirmJson(w, { api: 'confirm', t: makeToken(SECRET, { b: '=HYPERLINK("x")' }), reason: 'Other', details: '=IMPORTXML("x")\n\u0000\u202e ' + 'x'.repeat(900) });
  const row = rowsOf(w)[0]; assert.ok(row[2].startsWith("'=") && row[7].startsWith("'=") && row[7].length <= 502 && !/[\u0000\u202e]/.test(row[7]));
});
test('a body that is not the page\'s own request falls through to the mail app path and records nothing', () => {
  const w = makeWorld(); withTab(w);
  ['not json', '[]', '{"api":"other","t":"x"}', '', '{"api":"confirm"'].forEach((body) => assert.strictEqual(confirmCall(w, body).getContent(), 'Invalid link'));
  assert.strictEqual(w.call('doPost', { parameter: { t: PLAIN }, postData: { contents: 'List-Unsubscribe=One-Click', type: 'application/x-www-form-urlencoded' } }).getContent(), 'Unsupported request');
  assert.strictEqual(rowsOf(w).length, 0);
});
test('the mail app one-tap post still works next to the page interface', () => {
  const w = makeWorld(); withTab(w);
  assert.strictEqual(w.call('doPost', { parameter: { t: PLAIN, 'List-Unsubscribe': 'One-Click' }, postData: { contents: 'List-Unsubscribe=One-Click', type: 'application/x-www-form-urlencoded' } }).getContent(), 'Unsubscribed');
  assert.strictEqual(rowsOf(w)[0][8], 'one-click');
});
test('opening the Google page without api=info still shows the page, exactly as before', () => {
  const w = makeWorld(); const html = get(w, PLAIN); assert.ok(html.includes('Unsubscribe me') && html.includes('<!doctype html>'));
  assert.strictEqual(w.call('doGet', { parameter: { api: 'other', t: PLAIN } }).getContent().includes('Unsubscribe me'), true);
});
test('with diagnostics on, the page interface is logged like any other visit and holds nothing personal', () => {
  const w = diagWorld(); withTab(w); info(w, PLAIN); info(w, 'garbage'); confirmJson(w, { api: 'confirm', t: PLAIN, reason: 'Other', details: 'private words' });
  assert.deepStrictEqual(diagRows(w).map((r) => [r[1], r[2]]), [['opened', 'valid'], ['opened', 'invalid'], ['confirmed', 'valid'], ['master', 'no tab']]);
  const everything = JSON.stringify(w.sheets['Unsubscribe diagnostics'].rows); ['ann@example.com', 'private words', 'L-17'].forEach((x) => assert.ok(!everything.includes(x)));
});
test('the page-interface calls are written into the script\'s own description', () => {
  assert.ok(CODE.includes('THE PAGE ON GITHUB PAGES') && CODE.includes('?api=info&t=') && CODE.includes('docs/unsubscribe.html'));
});

// ================================================================ campaign codes: the link carries a code, the Sheet shows the name
const CCODE = '~abcDEF_123';
function withCodes(world, rows) { world.sheets['Campaign codes'] = world.makeSheet([['Code', 'Campaign']].concat(rows)); }
function coded(extra) { return makeToken(SECRET, Object.assign({ c: CCODE }, extra || {})); }
function recordFrom(world, token) { return confirmJson(world, { api: 'confirm', t: token, reason: '', details: '' }); }

test('a listed code is written to the Campaign column as the campaign\'s NAME', () => {
  const w = makeWorld(); withTab(w); withCodes(w, [[CCODE, 'Kelson_Creators_Licensing_17SEP']]);
  assert.strictEqual(recordFrom(w, coded()).ok, true); assert.strictEqual(rowsOf(w)[0][4], 'Kelson_Creators_Licensing_17SEP');
});
test('the name is written by every way an opt-out can arrive: the page, the Google page and the one-tap post', () => {
  const w = makeWorld(); withTab(w); withCodes(w, [[CCODE, 'Spring_Launch']]);
  recordFrom(w, coded({ e: 'a@x.com' })); submit(w, coded({ e: 'b@x.com' }), '', ''); post(w, coded({ e: 'c@x.com' }));
  assert.deepStrictEqual(rowsOf(w).map((r) => r[4]), ['Spring_Launch', 'Spring_Launch', 'Spring_Launch']);
});
test('a code that is not listed is written as the code itself', () => {
  const w = makeWorld(); withTab(w); recordFrom(w, coded());
  assert.strictEqual(rowsOf(w)[0][4], CCODE);
  withCodes(w, [['~someOtherCd', 'Other']]); recordFrom(w, coded({ e: 'z@x.com' })); assert.strictEqual(rowsOf(w)[1][4], CCODE);
});
test('a link from before codes existed still records the name it carries', () => {
  const w = makeWorld(); withTab(w); withCodes(w, [[CCODE, 'Spring_Launch']]); recordFrom(w, makeToken(SECRET, { c: 'Older_Campaign_Name' }));
  assert.strictEqual(rowsOf(w)[0][4], 'Older_Campaign_Name');
});
test('only a value shaped exactly like a code is looked up', () => {
  ['~short', '~abcDEF_1234', 'abcDEF_1234a', '~abc DEF_12', '~abcDEF_12!', '~~abcDEF_123', ' ~abcDEF_123'].forEach((value, i) => {
    const w = makeWorld(); withTab(w); withCodes(w, [[value.trim(), 'LOOKED_UP']]); recordFrom(w, makeToken(SECRET, { c: value, e: `u${i}@x.com` }));
    assert.notStrictEqual(rowsOf(w)[0][4], 'LOOKED_UP', JSON.stringify(value));
  });
});
test('a listed name with nothing in it falls back to the code, and a later row with a name is used', () => {
  const blank = makeWorld(); withTab(blank); withCodes(blank, [[CCODE, ''], [CCODE, '   ']]); recordFrom(blank, coded()); assert.strictEqual(rowsOf(blank)[0][4], CCODE);
  const later = makeWorld(); withTab(later); withCodes(later, [[CCODE, ''], [CCODE, 'Real_Name']]); recordFrom(later, coded()); assert.strictEqual(rowsOf(later)[0][4], 'Real_Name');
});
test('the code and the name are matched after trimming spaces', () => {
  const w = makeWorld(); withTab(w); withCodes(w, [['  ' + CCODE + ' ', '  Spring_Launch  ']]); recordFrom(w, coded());
  assert.strictEqual(rowsOf(w)[0][4], 'Spring_Launch');
});
test('a tab that cannot be read never stops an opt-out being recorded', () => {
  const w = makeWorld(); withTab(w); w.sheets['Campaign codes'] = { getDataRange() { throw new Error('boom'); } };
  const j = recordFrom(w, coded()); assert.strictEqual(j.ok, true); assert.strictEqual(rowsOf(w).length, 1); assert.strictEqual(rowsOf(w)[0][4], CCODE);
});
test('a name read from the tab is cleaned like anything else written to the Sheet', () => {
  const w = makeWorld(); withTab(w); withCodes(w, [[CCODE, '=HYPERLINK("http://evil","x")']]); recordFrom(w, coded());
  assert.ok(rowsOf(w)[0][4].startsWith("'=")); const long = makeWorld(); withTab(long); withCodes(long, [[CCODE, 'N'.repeat(500)]]); recordFrom(long, coded()); assert.strictEqual(rowsOf(long)[0][4].length, 120);
});
test('the tab is only read for a real opt-out: a test link and a bare look read nothing', () => {
  const w = makeWorld(); let reads = 0; w.sheets['Campaign codes'] = { getDataRange() { reads++; return { getValues: () => [['Code', 'Campaign']] }; } };
  recordFrom(w, coded({ t: 1 })); info(w, coded()); get(w, coded()); assert.strictEqual(reads, 0);
});
test('a code is looked up inside the same lock as the write and the lock is still released', () => {
  const w = makeWorld(); withTab(w); withCodes(w, [[CCODE, 'Spring_Launch']]); recordFrom(w, coded());
  assert.deepStrictEqual(plain(w.locks), { waited: 1, tried: 0, released: 1 });
});
test('the data the page receives holds neither the code nor the campaign\'s name', () => {
  const w = makeWorld(); withCodes(w, [[CCODE, 'Kelson_Creators_Licensing_17SEP']]); const text = info(w, coded()).getContent() + confirmCall(w, { api: 'confirm', t: coded({ t: 1 }) }).getContent();
  [CCODE, 'Kelson_Creators', '17SEP'].forEach((secret) => assert.ok(!text.includes(secret), secret));
});
test('the diagnostics log shows the name when the code is listed and the code when it is not', () => {
  const w = diagWorld(); withTab(w); withCodes(w, [[CCODE, 'Spring_Launch']]); info(w, coded()); info(w, coded({ c: '~unlistedCd1' }));
  assert.deepStrictEqual(diagRows(w).map((r) => [r[1], r[4]]), [['opened', 'Spring_Launch'], ['opened', '~unlistedCd1']]);
});
test('the code list is described in the script\'s own steps', () => {
  assert.ok(CODE.includes('CAMPAIGN CODES:') && CODE.includes('"Campaign codes"') && CODE.includes('this script only READS that tab'));
});

// ================================================================ opening the link again after unsubscribing, and the campaign's own master sheet
const MHEADER = ['LeadID', 'Email', 'FirstName', 'IntroSentAt', 'Status', 'LastActionAt', 'ReplyStatus'];
const MASTER = 'Spring_Launch Master Sheet';
function withMaster(world, rows, header, tab) { world.sheets[tab || MASTER] = world.makeSheet([(header || MHEADER).slice()].concat(rows)); return world.sheets[tab || MASTER]; }
function masterOf(world, tab) { return world.sheets[tab || MASTER].rows.slice(1).map((r) => plain(r)); }
const lead = (email, status, extra) => ['L-1', email, 'Ann', '2026-10-01 10:00:00', status || '', (extra && extra.at) || '', ''];
const optedOutRow = (email, key) => ['2026-10-01 09:00:00', email, 'Kelson Agency', key, 'Spring_Launch', 'L-17', '', '', 'page'];

test('info says already: false before an opt-out and already: true after it, for the same address and brand', () => {
  const w = makeWorld(); withTab(w);
  assert.strictEqual(infoJson(w, PLAIN).already, false); recordFrom(w, PLAIN); assert.strictEqual(infoJson(w, PLAIN).already, true);
});
test('already is per brand: the same address is not "already" for another brand', () => {
  const w = makeWorld(); withTab(w, [optedOutRow('ann@example.com', 'kelsonagency')]);
  assert.strictEqual(infoJson(w, PLAIN).already, true); assert.strictEqual(infoJson(w, makeToken(SECRET, { k: 'swoveralls', b: 'Swoveralls' })).already, false);
});
test('a hand-added row with no brand applies to every brand', () => {
  const w = makeWorld(); withTab(w, [optedOutRow('ann@example.com', '')]);
  assert.strictEqual(infoJson(w, PLAIN).already, true); assert.strictEqual(infoJson(w, makeToken(SECRET, { k: 'anything' })).already, true);
});
test('already is matched ignoring case and surrounding spaces, and never for another address', () => {
  const w = makeWorld(); withTab(w, [optedOutRow('  ANN@Example.COM ', 'kelsonagency')]);
  assert.strictEqual(infoJson(w, PLAIN).already, true); assert.strictEqual(infoJson(w, makeToken(SECRET, { e: 'bob@example.com' })).already, false);
});
test('a test link is never "already", even when the address has opted out', () => {
  const w = makeWorld(); withTab(w, [optedOutRow('ann@example.com', 'kelsonagency')]);
  assert.strictEqual(infoJson(w, makeToken(SECRET, { t: 1 })).already, false); assert.ok(get(w, makeToken(SECRET, { t: 1 })).includes('Unsubscribe me'));
});
test('a missing or unreadable Unsubscribes tab means "not already" and never an error', () => {
  const none = makeWorld(); assert.strictEqual(infoJson(none, PLAIN).already, false);
  const broken = makeWorld(); broken.sheets.Unsubscribes = { getDataRange() { throw new Error('boom'); } }; assert.strictEqual(infoJson(broken, PLAIN).already, false); assert.ok(get(broken, PLAIN).includes('Unsubscribe me'));
});
test('a tab with a missing column means "not already" and never an error', () => {
  const w = makeWorld(); w.sheets.Unsubscribes = w.makeSheet([['Timestamp', 'Email'], ['x', 'ann@example.com']]); assert.strictEqual(infoJson(w, PLAIN).already, false);
});
test('the Google page shows "You are already unsubscribed" with no questions and no buttons', () => {
  const w = makeWorld(); withTab(w, [optedOutRow('ann@example.com', 'kelsonagency')]); const html = get(w, PLAIN);
  assert.ok(html.includes('You are already unsubscribed') && html.includes('a***@example.com has already been removed from this mailing list') && html.includes('emails like this one from Kelson Agency'));
  assert.ok(html.includes('Unsubscribed by mistake? Reply to any of our emails and we will add you back.'));
  ['Why are you leaving', 'Unsubscribe me', 'type="radio"', '<textarea', '<button', '<input', 'google.script.run', 'No, keep me subscribed'].forEach((x) => assert.ok(!html.includes(x), x));
});
test('the Google page still asks the questions for someone who has not unsubscribed', () => {
  const html = get(makeWorld(), PLAIN); assert.ok(html.includes('Why are you leaving') && html.includes('Unsubscribe me') && !html.includes('already unsubscribed'));
});
test('the already page holds nothing but the masked address and the brand', () => {
  const w = makeWorld(); withTab(w, [optedOutRow('ann@example.com', 'kelsonagency')]); const html = get(w, PLAIN);
  ['ann@example.com', 'L-17', 'Spring_Launch', PLAIN.slice(0, 30)].forEach((x) => assert.ok(!html.includes(x), x));
});
test('opening the link reads the Unsubscribes tab but writes nothing and takes no lock', () => {
  const w = makeWorld(); withTab(w, [optedOutRow('ann@example.com', 'kelsonagency')]); const before = JSON.stringify(w.sheets.Unsubscribes.rows);
  info(w, PLAIN); get(w, PLAIN); assert.strictEqual(JSON.stringify(w.sheets.Unsubscribes.rows), before); assert.deepStrictEqual(plain(w.locks), { waited: 0, tried: 0, released: 0 });
});

test('an opt-out marks the person\'s row in THAT campaign\'s master tab as Stopped - Unsubscribed, with the time', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [lead('bob@example.com'), lead('ann@example.com'), lead('cy@example.com')]); recordFrom(w, PLAIN);
  const rows = masterOf(w); assert.deepStrictEqual([rows[0][4], rows[2][4]], ['', '']); assert.strictEqual(rows[1][4], 'Stopped - Unsubscribed'); assert.ok(/^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$/.test(rows[1][5]));
  assert.deepStrictEqual([rows[0][5], rows[2][5]], ['', '']);
});
test('only the campaign the person unsubscribed from is touched', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [lead('ann@example.com')]); withMaster(w, [lead('ann@example.com')], MHEADER, 'Autumn_Launch Master Sheet'); recordFrom(w, PLAIN);
  assert.strictEqual(masterOf(w)[0][4], 'Stopped - Unsubscribed'); assert.strictEqual(masterOf(w, 'Autumn_Launch Master Sheet')[0][4], '');
});
test('the master sheet is updated whichever way the opt-out arrives: the page, the Google page or the one-tap post', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [lead('a@x.com'), lead('b@x.com'), lead('c@x.com')]);
  recordFrom(w, makeToken(SECRET, { e: 'a@x.com' })); submit(w, makeToken(SECRET, { e: 'b@x.com' }), '', ''); post(w, makeToken(SECRET, { e: 'c@x.com' }));
  assert.deepStrictEqual(masterOf(w).map((r) => r[4]), ['Stopped - Unsubscribed', 'Stopped - Unsubscribed', 'Stopped - Unsubscribed']);
});
test('a code is turned into the campaign\'s name to find its master tab, and an unlisted code touches nothing', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [lead('ann@example.com')]); withCodes(w, [[CCODE, 'Spring_Launch']]); recordFrom(w, coded());
  assert.strictEqual(masterOf(w)[0][4], 'Stopped - Unsubscribed');
  const unlisted = makeWorld(); withTab(unlisted); withMaster(unlisted, [lead('ann@example.com')]); const j = recordFrom(unlisted, coded());
  assert.strictEqual(j.ok, true); assert.strictEqual(masterOf(unlisted)[0][4], ''); assert.strictEqual(rowsOf(unlisted).length, 1);
});
['Stopped - Replied', 'Stopped - Bounced', 'Stopped - Rejected', 'Stopped - Manual', 'Removed', 'Stopped - Unsubscribed'].forEach((status) => {
  test(`a lead whose outcome is already decided (${status}) is left exactly as it is`, () => {
    const w = makeWorld(); withTab(w); withMaster(w, [lead('ann@example.com', status, { at: '2026-09-01 08:00:00' })]); recordFrom(w, PLAIN);
    assert.deepStrictEqual(masterOf(w)[0].slice(4, 6), [status, '2026-09-01 08:00:00']);
  });
});
['', 'Paused', 'Completed', 'Active', 'In sequence', 'something else'].forEach((status) => {
  test(`a lead that is ${JSON.stringify(status)} becomes Stopped - Unsubscribed`, () => {
    const w = makeWorld(); withTab(w); withMaster(w, [lead('ann@example.com', status)]); recordFrom(w, PLAIN);
    assert.strictEqual(masterOf(w)[0][4], 'Stopped - Unsubscribed');
  });
});
test('the address is matched ignoring case and spaces, and every row for it is updated', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [lead('  ANN@Example.com '), lead('bob@example.com'), lead('ann@example.com', 'Stopped - Replied'), lead('ann@example.com')]); recordFrom(w, PLAIN);
  assert.deepStrictEqual(masterOf(w).map((r) => r[4]), ['Stopped - Unsubscribed', '', 'Stopped - Replied', 'Stopped - Unsubscribed']);
});
test('the time written is UTC in the format the engine writes', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [lead('ann@example.com')]); recordFrom(w, PLAIN);
  const stamp = masterOf(w)[0][5]; assert.ok(Math.abs(Date.parse(stamp.replace(' ', 'T') + 'Z') - Date.now()) < 60000, stamp);
});
test('a master tab with no LastActionAt column still gets its status', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [['L-1', 'ann@example.com', '']], ['LeadID', 'Email', 'Status']); recordFrom(w, PLAIN);
  assert.deepStrictEqual(masterOf(w)[0], ['L-1', 'ann@example.com', 'Stopped - Unsubscribed']);
});
test('a master tab with no Email or no Status column is left alone and the opt-out is still recorded', () => {
  ['Email', 'Status'].forEach((missing) => {
    const header = MHEADER.filter((name) => name !== missing); const w = makeWorld(); withTab(w); withMaster(w, [header.map(() => 'ann@example.com')], header);
    const before = JSON.stringify(masterOf(w)); assert.strictEqual(recordFrom(w, PLAIN).ok, true); assert.strictEqual(JSON.stringify(masterOf(w)), before); assert.strictEqual(rowsOf(w).length, 1);
  });
});
test('a campaign with no master tab leaves the opt-out recorded and changes nothing else', () => {
  const w = makeWorld(); withTab(w); const j = recordFrom(w, PLAIN); assert.strictEqual(j.ok, true); assert.strictEqual(rowsOf(w).length, 1); assert.ok(!w.sheets[MASTER]);
});
test('a master tab that cannot be written never breaks the opt-out, and the lock is released', () => {
  const w = makeWorld(); withTab(w); w.sheets[MASTER] = { getDataRange: () => ({ getValues: () => [MHEADER.slice(), lead('ann@example.com')] }), getRange: () => ({ setValue() { throw new Error('boom'); } }) };
  const j = recordFrom(w, PLAIN); assert.strictEqual(j.ok, true); assert.strictEqual(rowsOf(w).length, 1); assert.deepStrictEqual(plain(w.locks), { waited: 1, tried: 0, released: 1 });
});
test('a master tab that cannot be read never breaks the opt-out', () => {
  const w = makeWorld(); withTab(w); w.sheets[MASTER] = { getDataRange() { throw new Error('boom'); } }; assert.strictEqual(recordFrom(w, PLAIN).ok, true); assert.strictEqual(rowsOf(w).length, 1);
});
test('repeating the opt-out catches up a master sheet that could not be updated the first time', () => {
  const w = makeWorld(); withTab(w); recordFrom(w, PLAIN); withMaster(w, [lead('ann@example.com')]);
  assert.strictEqual(masterOf(w)[0][4], ''); const again = recordFrom(w, PLAIN); assert.strictEqual(again.already, true); assert.strictEqual(masterOf(w)[0][4], 'Stopped - Unsubscribed'); assert.strictEqual(rowsOf(w).length, 1);
});
test('a test link never reads or writes the master sheet', () => {
  const w = makeWorld(); let reads = 0; w.sheets[MASTER] = { getDataRange() { reads++; return { getValues: () => [MHEADER.slice()] }; }, getRange() { throw new Error('written'); } };
  recordFrom(w, makeToken(SECRET, { t: 1 })); submit(w, makeToken(SECRET, { t: 1 }), '', ''); post(w, makeToken(SECRET, { t: 1 })); assert.strictEqual(reads, 0);
});
test('what the visitor is told is exactly what it was: nothing about the master sheet reaches them', () => {
  const w = makeWorld(); withTab(w); withMaster(w, [lead('ann@example.com')]); const j = recordFrom(w, PLAIN);
  assert.deepStrictEqual(Object.keys(j).sort(), ['address', 'already', 'brand', 'masked', 'ok', 'test']); assert.ok(!JSON.stringify(j).includes('master'));
});
test('with diagnostics on, what happened in the master sheet is logged in a word, without anything personal', () => {
  const states = [];
  [['updated', (w) => withMaster(w, [lead('ann@example.com')])], ['none', (w) => withMaster(w, [lead('bob@example.com')])], ['no tab', () => {}],
   ['no columns', (w) => withMaster(w, [['x']], ['Other'])], ['no code', (w) => { withMaster(w, [lead('ann@example.com')]); }]].forEach(([expected, setup], i) => {
    const w = diagWorld(); withTab(w); setup(w); recordFrom(w, expected === 'no code' ? coded() : PLAIN); const row = diagRows(w).find((r) => r[1] === 'master'); states.push([expected, row && row[2]]);
    assert.ok(!JSON.stringify(w.sheets['Unsubscribe diagnostics'].rows).includes('ann@example.com'), String(i));
  });
  assert.deepStrictEqual(states, [['updated', 'updated'], ['none', 'none'], ['no tab', 'no tab'], ['no columns', 'no columns'], ['no code', 'no code']]);
});
test('the master-sheet rule and the already-unsubscribed card are written into the script\'s own description', () => {
  assert.ok(CODE.includes("THE CAMPAIGN'S OWN SHEET:") && CODE.includes('Stopped - Unsubscribed') && CODE.includes('You are already unsubscribed'));
});

let failed = 0;
tests.forEach(([name, fn]) => {
  try { fn(); console.log('PASS  ' + name); } catch (error) { failed++; console.log('FAIL  ' + name + '\n      ' + String(error && error.message).split('\n')[0]); }
});
console.log('\n' + (tests.length - failed) + ' of ' + tests.length + ' passed');
process.exit(failed ? 1 : 0);
