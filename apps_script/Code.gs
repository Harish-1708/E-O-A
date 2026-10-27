/**
 * Unsubscribe page for HTML campaigns (Google Apps Script web app).
 *
 * WHAT IT DOES
 *   A link in an HTML email opens this page. The person sees which address is being removed, can say why (optional), confirms, and is thanked. The request is written as one row
 *   to the "Unsubscribes" tab of your shared Google Sheet. The engine reads that tab before every HTML send and skips the address for that BRAND.
 *   A mail app's one-tap "Unsubscribe" (RFC 8058) posts to the same address and is recorded the same way.
 *
 * SAFETY
 *   Every link is signed by the engine with a secret. This page checks the signature first and refuses anything altered. Opening the page changes nothing: only the
 *   confirm button (or a mail app's one-tap post) writes a row. Text typed by a visitor is cleaned before it is saved. A test link (from "Send me a test") is accepted but records nothing.
 *
 * SET-UP (about ten minutes, once)
 *   1. In the Google account that owns your shared Sheet: script.google.com > New project. Paste this whole file into Code.gs, replacing what is there. Save.
 *   2. Project Settings (the gear) > Script properties > Add script property, twice:
 *        UNSUBSCRIBE_SECRET = a long random text, at least 16 characters (the same text goes into GitHub, below)
 *        SHEET_ID           = the ID of your shared Sheet (the long text between /d/ and /edit in its address)
 *   3. In the editor, choose the function  setupCheck  and press Run. Allow the permissions it asks for. It should say PASS. Then run  selfTest  and check it says PASS.
 *   4. Deploy > New deployment > type: Web app. Execute as: Me. Who has access: Anyone (NOT "Only myself", NOT "Anyone within your organisation"). Press Deploy and copy the Web app URL.
 *      It ends in /exec. Never use the "Test deployments" address: it ends in /dev and opens only for you. Copy it from the Deploy dialog (or Manage deployments), NEVER from your browser's
 *      address bar: when you are signed in to several Google accounts the address bar shows /u/6/ (your account's position), which means nothing to anyone else.
 *   5. In GitHub > Settings > Secrets and variables > Actions, add two secrets:  UNSUBSCRIBE_URL = that Web app URL,  UNSUBSCRIBE_SECRET = the same secret text as in step 2.
 *   6. CHECK FROM OUTSIDE: open the Web app URL on its own (nothing after /exec) in a private (incognito) window where you are not signed in to Google. It must show a card saying
 *      "This link is not valid". If Google shows "unable to open the file" instead, the access setting or the address is wrong: go back to step 4.
 *   7. Send yourself a test email from the app. The run's summary also opens the page from GitHub and says OK or PROBLEM. Then click Unsubscribe in the email (a test link records nothing).
 *
 * CAMPAIGN CODES: the links carry a short code ('~' and 10 characters) instead of your campaign's name, so recipients cannot read your campaign names. The engine lists each code next to its name
 *   in a tab called "Campaign codes" (made for you the first time a campaign sends); this script only READS that tab, to write the name into the Campaign column. If a code is not listed
 *   there, the Campaign column shows the code: the "Campaign codes" tab always tells you which campaign it stands for.
 *
 * TROUBLESHOOTING (a link opens for you but a recipient sees Google's "unable to open the file"):
 *   Add a script property DIAGNOSTICS = on, have the recipient click the link again, then open the "Unsubscribe diagnostics" tab of your Sheet:
 *     no new row            = the request never reached this script (Google refused it first: an access or sign-in problem, not this code);
 *     "opened" only         = the script answered but the page did not finish loading in their browser;
 *     "opened" + "rendered" = the page opened fine in their browser (the error came from something else they clicked).
 *   It records no email address, no lead and no link. Delete the property afterwards: while it is on, every visit adds a row.
 *   THE PAGE ON GITHUB PAGES: recipients are sent to a small static page (docs/unsubscribe.html in the repo, shown by GitHub Pages) that asks THIS script for the data with a plain request
 *   (?api=info&t=...) and sends the confirmation as JSON. No Google sign-in is involved, which is why it works for people signed in to several Google accounts. The Google page below it
 *   (HtmlService) stays as a fallback and for the mail app's one-tap unsubscribe.
 *   When you change this code later: Deploy > Manage deployments > Edit > New version > Deploy (the address stays the same).
 */

var TAB_NAME = 'Unsubscribes';
var COLUMNS = ['Timestamp', 'Email', 'Brand', 'BrandKey', 'Campaign', 'LeadID', 'Reason', 'Details', 'Source'];
var REASONS = ['Too many emails', 'Not relevant to me', 'I never signed up', 'Other'];
var CODES_TAB = 'Campaign codes';
var CAMPAIGN_CODE_PATTERN = /^~[A-Za-z0-9_-]{10}$/;
var DIAG_TAB = 'Unsubscribe diagnostics';
var DIAG_COLUMNS = ['Time', 'Event', 'State', 'Brand', 'Campaign', 'QueryLength', 'TokenLength', 'ParamNames'];
var DIAG_STATES = ['valid', 'invalid', 'test', 'not set up'];
var MAX_REASON = 60;
var MAX_DETAILS = 500;
var MIN_SECRET_LENGTH = 16;
var TOKEN_VERSION = 1;
var REQUIRED_FIELDS = ['c', 'l', 'e', 'b', 'k', 'a'];

// Known-answer links made by the engine's Python code (tests/test_unsubscribe_logic.py holds the same two). selfTest() checks this script reads them exactly.
var SELF_TEST_SECRET = 'test-secret-1234567890';
var SELF_TEST_PLAIN = 'eyJhIjoiMTIgRXhhbXBsZSBTdHJlZXQsIE5ldyBZb3JrLCBOWSIsImIiOiJLZWxzb24gQWdlbmN5IiwiYyI6IlNwcmluZ19MYXVuY2giLCJlIjoiYW5uQGV4YW1wbGUuY29tIiwiayI6ImtlbHNvbmFnZW5jeSIsImwiOiJMLTE3IiwidiI6MX0.KTXW_9O4mzmdoFM_vQyaNHSGio7Q71CUfhZwxGNpCY0';
var SELF_TEST_UNICODE_TEST = 'eyJhIjoiUnVlIGRlIGwnw4l0w6kgMyIsImIiOiJDYWbDqSDDnG7Dr2NvZGUg4piVIiwiYyI6IkNhZsOpX8OJdMOpIiwiZSI6Inpvw6tAZXhhbXBsZS5jb20iLCJrIjoiY2FmZXVuaWNvZGUiLCJsIjoiTC0xOCIsInQiOjEsInYiOjF9.1FZsRhrf_nyhBH6RIbwX_5qcvbp6jFiVNnJYHUccPdc';


/* ------------------------------------------------------------------ small pure helpers */

function escapeHtml_(value) {
  return String(value === null || value === undefined ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function maskEmail_(email) {
  var text = String(email || '');
  var at = text.indexOf('@');
  if (at < 1 || at === text.length - 1) { return '***'; }
  return text.charAt(0) + '***' + text.substring(at);
}

function cleanText_(value, max) {
  var text = String(value === null || value === undefined ? '' : value).replace(/[\u0000-\u001f\u007f\u200e\u200f\u202a-\u202e\u2066-\u2069]/g, ' ').replace(/\s+/g, ' ').trim();
  return text.length > max ? text.substring(0, max).trim() : text;
}

// A spreadsheet treats a cell that starts with = + - @ as a formula. Anything that comes from outside gets a leading apostrophe so it is read as plain text.
function safeCell_(value) {
  var text = String(value === null || value === undefined ? '' : value);
  return /^[=+\-@\t\r]/.test(text) ? "'" + text : text;
}

function constantTimeEquals_(a, b) {
  var x = String(a), y = String(b);
  if (x.length !== y.length) { return false; }
  var diff = 0;
  for (var i = 0; i < x.length; i++) { diff |= x.charCodeAt(i) ^ y.charCodeAt(i); }
  return diff === 0;
}

function base64UrlNoPad_(bytes) {
  return Utilities.base64EncodeWebSafe(bytes).replace(/=+$/, '');
}

function padBase64_(text) {
  var out = String(text);
  while (out.length % 4 !== 0) { out += '='; }
  return out;
}

/** The link's record when its signature is right and its shape is the expected one, otherwise null. Never throws. */
function verifyToken_(token, secret) {
  try {
    if (typeof token !== 'string' || !secret) { return null; }
    var parts = token.split('.');
    if (parts.length !== 2 || !parts[0] || !parts[1]) { return null; }
    var expected = base64UrlNoPad_(Utilities.computeHmacSha256Signature(parts[0], secret));
    if (!constantTimeEquals_(parts[1], expected)) { return null; }
    var payload = JSON.parse(Utilities.newBlob(Utilities.base64DecodeWebSafe(padBase64_(parts[0]))).getDataAsString('UTF-8'));
    if (!payload || typeof payload !== 'object' || payload.v !== TOKEN_VERSION) { return null; }
    for (var i = 0; i < REQUIRED_FIELDS.length; i++) {
      if (typeof payload[REQUIRED_FIELDS[i]] !== 'string') { return null; }
    }
    return payload;
  } catch (error) {
    return null;
  }
}

function readProps_() {
  var props = PropertiesService.getScriptProperties();
  var secret = String(props.getProperty('UNSUBSCRIBE_SECRET') || '').trim();
  var sheetId = String(props.getProperty('SHEET_ID') || '').trim();
  if (secret.length < MIN_SECRET_LENGTH || !sheetId) { return { ok: false, secret: secret, sheetId: sheetId }; }
  return { ok: true, secret: secret, sheetId: sheetId };
}


/* ------------------------------------------------------------------ campaign codes */

/** The campaign's NAME, for the Campaign column. A link carries a short code ('~' and 10 characters) instead of the name, so a recipient cannot read your campaign names; the ENGINE lists each code
 *  next to its name in the 'Campaign codes' tab (made automatically) and this script only reads it. A link from before codes existed carries the name itself. Never throws: if the code is not
 *  listed or the tab cannot be read, the code itself is recorded, which still identifies the campaign. */
function campaignLabel_(value, sheetId) {
  var text = String(value || '');
  if (!CAMPAIGN_CODE_PATTERN.test(text)) { return text; }
  try {
    var sheet = SpreadsheetApp.openById(sheetId).getSheetByName(CODES_TAB);
    if (sheet) {
      var values = sheet.getDataRange().getValues();
      for (var row = 1; row < values.length; row++) {
        if (String(values[row][0]).trim() === text && String(values[row][1] || '').trim()) { return String(values[row][1]).trim(); }
      }
    }
  } catch (error) {
    // fall back to the code
  }
  return text;
}


/* ------------------------------------------------------------------ troubleshooting log (off unless the DIAGNOSTICS property is "on") */

function diagnosticsOn_() {
  return String(PropertiesService.getScriptProperties().getProperty('DIAGNOSTICS') || '').trim().toLowerCase() === 'on';
}

/** One row per visit, with NO address, lead or link in it: only what the visit looked like. Never throws, never waits long, never changes what the visitor sees. */
function logDiag_(eventName, state, payload, e) {
  try {
    if (!diagnosticsOn_()) { return; }
    var sheetId = readProps_().sheetId;
    if (!sheetId) { return; }
    var parameter = e && e.parameter ? e.parameter : null;
    var token = parameter && parameter.t ? String(parameter.t) : '';
    var lock = LockService.getScriptLock();
    if (!lock.tryLock(5000)) { return; }
    try {
      var spreadsheet = SpreadsheetApp.openById(sheetId);
      var sheet = spreadsheet.getSheetByName(DIAG_TAB);
      if (!sheet) {
        sheet = spreadsheet.insertSheet(DIAG_TAB);
        sheet.appendRow(DIAG_COLUMNS);
        sheet.setFrozenRows(1);
      }
      sheet.appendRow([Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd HH:mm:ss'), eventName, state, safeCell_(cleanText_(payload ? payload.b : '', 120)), safeCell_(cleanText_(payload ? campaignLabel_(payload.c, sheetId) : '', 120)),
        e && e.queryString !== undefined ? String(e.queryString).length : '', e ? token.length : '', parameter ? Object.keys(parameter).join(',') : '']);
    } finally {
      lock.releaseLock();
    }
  } catch (error) {
    // diagnostics must never break the page
  }
}

/** Called by the page itself, only while diagnostics are on, once its content has actually run in the visitor's browser. */
function recordRendered(state) {
  if (DIAG_STATES.indexOf(String(state)) < 0) { return false; }
  logDiag_('rendered', String(state), null, null);
  return true;
}


/* ------------------------------------------------------------------ the Sheet */

function getTab_(sheetId) {
  var spreadsheet = SpreadsheetApp.openById(sheetId);
  var sheet = spreadsheet.getSheetByName(TAB_NAME);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(TAB_NAME);
    sheet.appendRow(COLUMNS);
    sheet.setFrozenRows(1);
  }
  return sheet;
}

function columnIndexes_(headerRow) {
  var index = {};
  for (var i = 0; i < COLUMNS.length; i++) {
    var position = headerRow.map(function (cell) { return String(cell).trim(); }).indexOf(COLUMNS[i]);
    if (position < 0) { throw new Error('The "' + TAB_NAME + '" tab has no "' + COLUMNS[i] + '" column.'); }
    index[COLUMNS[i]] = position;
  }
  return index;
}

/**
 * Writes one opt-out. One row per address per brand: a repeat adds nothing (it may fill in a reason that was left empty). A test link records nothing.
 * Returns {ok, masked, brand, address, already, test}.
 */
function record_(payload, source, reason, details, props) {
  var result = { ok: true, masked: maskEmail_(payload.e), brand: payload.b, address: payload.a, already: false, test: payload.t === 1 };
  if (result.test) { return result; }
  var cleanReason = REASONS.indexOf(String(reason || '')) >= 0 ? String(reason) : '';
  var cleanDetails = cleanText_(details, MAX_DETAILS);
  var lock = LockService.getScriptLock();
  lock.waitLock(20000);
  try {
    var sheet = getTab_(props.sheetId);
    var values = sheet.getDataRange().getValues();
    var index = columnIndexes_(values[0]);
    var email = String(payload.e).trim().toLowerCase();
    for (var row = 1; row < values.length; row++) {
      if (String(values[row][index.Email]).trim().toLowerCase() === email && String(values[row][index.BrandKey]).trim() === payload.k) {
        result.already = true;
        if (cleanReason && !String(values[row][index.Reason]).trim()) {
          sheet.getRange(row + 1, index.Reason + 1).setValue(cleanReason);
          if (cleanDetails) { sheet.getRange(row + 1, index.Details + 1).setValue(safeCell_(cleanDetails)); }
        }
        return result;
      }
    }
    var line = [];
    for (var column = 0; column < values[0].length; column++) { line.push(''); }
    line[index.Timestamp] = Utilities.formatDate(new Date(), 'UTC', 'yyyy-MM-dd HH:mm:ss');
    line[index.Email] = safeCell_(cleanText_(payload.e, 254));
    line[index.Brand] = safeCell_(cleanText_(payload.b, 120));
    line[index.BrandKey] = safeCell_(cleanText_(payload.k, 120));
    line[index.Campaign] = safeCell_(cleanText_(campaignLabel_(payload.c, props.sheetId), 120));
    line[index.LeadID] = safeCell_(cleanText_(payload.l, 80));
    line[index.Reason] = cleanReason;
    line[index.Details] = safeCell_(cleanDetails);
    line[index.Source] = source;
    sheet.appendRow(line);
    return result;
  } finally {
    lock.releaseLock();
  }
}


/* ------------------------------------------------------------------ what the web app answers */

/** The data interface used by the page on GitHub Pages (a plain request from the visitor's browser with no Google sign-in involved). Reads only: changes nothing. */
function info_(e) {
  var props = readProps_();
  if (!props.ok) { logDiag_('opened', 'not set up', null, e); return { ok: false, state: 'not set up' }; }
  var payload = verifyToken_(e && e.parameter ? e.parameter.t : '', props.secret);
  if (!payload) { logDiag_('opened', 'invalid', null, e); return { ok: false, state: 'invalid' }; }
  var state = payload.t === 1 ? 'test' : 'valid';
  logDiag_('opened', state, payload, e);
  return { ok: true, state: state, brand: payload.b, masked: maskEmail_(payload.e), address: payload.a, reasons: REASONS.slice(), test: payload.t === 1 };
}

/** Records an opt-out for the page on GitHub Pages. `body` is the request's JSON. Never throws; says what happened in plain fields. */
function confirmApi_(body) {
  try {
    var props = readProps_();
    if (!props.ok) { return { ok: false, error: 'not set up' }; }
    var payload = verifyToken_(body.t, props.secret);
    if (!payload) { return { ok: false, error: 'invalid' }; }
    var result = record_(payload, 'page', body.reason, body.details, props);
    logDiag_('confirmed', result.test ? 'test' : 'valid', payload, null);
    return result;
  } catch (error) {
    return { ok: false, error: 'failed' };
  }
}

function parseBody_(e) {
  try {
    if (!e || !e.postData || !e.postData.contents) { return null; }
    var parsed = JSON.parse(e.postData.contents);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : null;
  } catch (error) {
    return null;
  }
}

function json_(object) {
  return ContentService.createTextOutput(JSON.stringify(object)).setMimeType(ContentService.MimeType.JSON);
}

/** Opening the link: shows the page. Changes nothing. (With api=info it answers with data instead of a page: see info_.) */
function doGet(e) {
  if (e && e.parameter && e.parameter.api === 'info') { return json_(info_(e)); }
  var props = readProps_();
  if (!props.ok) { logDiag_('opened', 'not set up', null, e); return page_(shell_('UNSUBSCRIBE', unavailableBody_(), '', 'not set up')); }
  var payload = verifyToken_(e && e.parameter ? e.parameter.t : '', props.secret);
  if (!payload) { logDiag_('opened', 'invalid', null, e); return page_(shell_('UNSUBSCRIBE', invalidBody_(), 'No information was recorded.', 'invalid')); }
  var state = payload.t === 1 ? 'test' : 'valid';
  logDiag_('opened', state, payload, e);
  return page_(shell_(payload.b ? payload.b.toUpperCase() : 'UNSUBSCRIBE', confirmBody_(payload, e.parameter.t), footerLine_(payload), state));
}

/** A mail app's one-tap Unsubscribe (RFC 8058): a POST with List-Unsubscribe=One-Click to the link's own address. */
function doPost(e) {
  try {
    var body = parseBody_(e);
    if (body && body.api === 'confirm') { return json_(confirmApi_(body)); }
    var params = (e && e.parameter) || {};
    var props = readProps_();
    if (!props.ok) { return text_('Not set up'); }
    var payload = verifyToken_(params.t, props.secret);
    if (!payload) { logDiag_('one-tap', 'invalid', null, e); return text_('Invalid link'); }
    if (String(params['List-Unsubscribe'] || '') !== 'One-Click') { return text_('Unsupported request'); }
    record_(payload, 'one-click', '', '', props);
    logDiag_('one-tap', payload.t === 1 ? 'test' : 'valid', payload, e);
    return text_('Unsubscribed');
  } catch (error) {
    return text_('Error');
  }
}

/** Called by the page's confirm button (google.script.run). Checks the signature again, then records. */
function submitUnsubscribe(token, reason, details) {
  var props = readProps_();
  if (!props.ok) { throw new Error('The unsubscribe page is not set up.'); }
  var payload = verifyToken_(token, props.secret);
  if (!payload) { throw new Error('This link is not valid.'); }
  var result = record_(payload, 'page', reason, details, props);
  logDiag_('confirmed', result.test ? 'test' : 'valid', payload, null);
  return result;
}

function page_(html) {
  return HtmlService.createHtmlOutput(html).setTitle('Unsubscribe').addMetaTag('viewport', 'width=device-width, initial-scale=1');
}

function text_(message) {
  return ContentService.createTextOutput(message);
}


/* ------------------------------------------------------------------ the pages */

var PAGE_CSS = '*{box-sizing:border-box}body{margin:0;background:#eef0f4;font-family:Arial,Helvetica,sans-serif;color:#111827;padding:28px 16px}' +
  '.card{max-width:420px;margin:0 auto;background:#fff;border-radius:14px;box-shadow:0 6px 24px rgba(17,25,40,.12);overflow:hidden}' +
  '.top{background:#111827;color:#fff;padding:16px 24px;font-weight:700;letter-spacing:.06em;font-size:15px}.bd{padding:26px 26px 22px}' +
  'h1{font-size:22px;margin:0 0 8px}p{font-size:14.5px;line-height:1.55;color:#374151;margin:0 0 14px}.em{background:#f3f4f6;border-radius:8px;padding:10px 14px;font-weight:700;font-size:15px;margin:4px 0 18px}' +
  '.q{font-weight:700;font-size:14.5px;margin:6px 0 8px}.opt{display:flex;gap:10px;align-items:center;border:1.5px solid #d1d5db;border-radius:10px;padding:10px 12px;margin-bottom:8px;font-size:14px;cursor:pointer}' +
  '.opt input{margin:0}textarea{width:100%;border:1.5px solid #d1d5db;border-radius:10px;padding:10px 12px;height:64px;font:inherit;font-size:13.5px;margin:2px 0 16px;resize:vertical}' +
  '.btn{display:block;width:100%;text-align:center;background:#2563eb;color:#fff;font-weight:700;border:0;border-radius:10px;padding:13px;font-size:15px;cursor:pointer}.btn[disabled]{opacity:.6;cursor:default}' +
  '.link{display:block;text-align:center;color:#6b7280;font-size:13px;text-decoration:underline;margin-top:12px;cursor:pointer;background:none;border:0;width:100%}' +
  '.ft{background:#f8f9fb;border-top:1px solid #eef0f3;color:#6b7280;font-size:11.5px;padding:12px 24px;text-align:center;line-height:1.5}' +
  '.tick{width:54px;height:54px;border-radius:50%;background:#dcfce7;color:#15803d;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:800;margin:6px 0 16px}' +
  '.warn{width:54px;height:54px;border-radius:50%;background:#fff4e5;color:#b45309;display:flex;align-items:center;justify-content:center;font-size:30px;font-weight:800;margin:6px 0 16px}' +
  '.small{font-size:12.5px;color:#6b7280}.test{background:#fff4e5;color:#92400e;border-radius:8px;padding:10px 12px;font-size:13px;margin:0 0 16px}.err{color:#b91c1c;font-size:13.5px;margin:10px 0 0}';

function shell_(heading, body, footer, diagState) {
  var beacon = diagState && diagnosticsOn_() ? '<script>try{google.script.run.withFailureHandler(function(){}).recordRendered(' + JSON.stringify(String(diagState)) + ');}catch(e){}</script>' : '';
  return '<!doctype html><html><head><meta charset="utf-8"><style>' + PAGE_CSS + '</style></head><body><div class="card"><div class="top">' + escapeHtml_(heading) + '</div><div class="bd" id="bd">' + body + '</div>' +
    (footer ? '<div class="ft">' + escapeHtml_(footer) + '</div>' : '') + '</div>' + beacon + '</body></html>';
}

function footerLine_(payload) {
  var parts = [];
  if (payload.b) { parts.push(payload.b); }
  if (payload.a) { parts.push(payload.a); }
  return parts.join(' \u00b7 ');
}

function senderWords_(payload) {
  return payload.b ? '<b>' + escapeHtml_(payload.b) + '</b>' : 'this sender';
}

function invalidBody_() {
  return '<div class="warn">!</div><h1>This link is not valid</h1><p>The unsubscribe link is incomplete or was changed.</p>' +
    '<p>To stop these emails, reply <b>unsubscribe</b> to the email you received and we will remove you.</p>';
}

function unavailableBody_() {
  return '<div class="warn">!</div><h1>Unsubscribe is not available right now</h1><p>Please reply <b>unsubscribe</b> to the email you received and we will remove you.</p>';
}

function confirmBody_(payload, token) {
  var options = '';
  for (var i = 0; i < REASONS.length; i++) {
    options += '<label class="opt"><input type="radio" name="reason" value="' + escapeHtml_(REASONS[i]) + '"> ' + escapeHtml_(REASONS[i]) + '</label>';
  }
  var testNote = payload.t === 1 ? '<div class="test">This is a test link from your team. Nothing will be recorded.</div>' : '';
  var tokenJs = JSON.stringify(String(token)).replace(/</g, '\\u003c');
  return testNote + '<h1>Unsubscribe</h1><p>You are about to stop receiving emails from ' + senderWords_(payload) + ' at:</p><div class="em">' + escapeHtml_(maskEmail_(payload.e)) + '</div>' +
    '<div class="q">Why are you leaving? <span class="small" style="font-weight:400">(optional)</span></div>' + options +
    '<textarea id="details" maxlength="' + MAX_DETAILS + '" placeholder="Tell us more (optional)"></textarea>' +
    '<button class="btn" id="go" type="button">Unsubscribe me</button><button class="link" id="stay" type="button">No, keep me subscribed</button><div class="err" id="err"></div>' +
    '<script>(function(){var TOKEN=' + tokenJs + ';' +
    'function el(id){return document.getElementById(id);}' +
    'function say(title,lines,kind){var bd=el("bd");while(bd.firstChild){bd.removeChild(bd.firstChild);}' +
    'var mark=document.createElement("div");mark.className=kind;mark.textContent=kind==="tick"?"\\u2713":"!";bd.appendChild(mark);' +
    'var h=document.createElement("h1");h.textContent=title;bd.appendChild(h);' +
    'lines.forEach(function(text){var p=document.createElement("p");p.textContent=text;bd.appendChild(p);});}' +
    'el("stay").onclick=function(){say("You are still subscribed",["No problem. Nothing has changed."],"tick");};' +
    'el("go").onclick=function(){var button=el("go");button.disabled=true;el("err").textContent="";' +
    'var chosen=document.querySelector("input[name=reason]:checked");var reason=chosen?chosen.value:"";' +
    'google.script.run.withSuccessHandler(function(result){' +
    'var who=result.brand?"emails like this one from "+result.brand:"emails like this one";' +
    'var lines=["We have removed "+result.masked+" from this mailing list. You will not receive any more "+who+"."];' +
    'if(reason){lines.push("Thank you for telling us why. It helps us send better email.");}' +
    'lines.push("Unsubscribed by mistake? Reply to any of our emails and we will add you back.");' +
    'if(result.test){lines.unshift("Test only: nothing was recorded.");}' +
    'say("You are unsubscribed",lines,"tick");})' +
    '.withFailureHandler(function(){button.disabled=false;el("err").textContent="Something went wrong. Please reply unsubscribe to the email you received.";})' +
    '.submitUnsubscribe(TOKEN,reason,el("details").value);};})();</script>';
}


/* ------------------------------------------------------------------ checks you run by hand in the editor */

/** Run this once after pasting the script and setting the two properties. It does not change your data except to create the Unsubscribes tab if it is missing. */
function setupCheck() {
  var props = readProps_();
  var problems = [];
  if (props.secret.length < MIN_SECRET_LENGTH) { problems.push('UNSUBSCRIBE_SECRET is missing or shorter than ' + MIN_SECRET_LENGTH + ' characters.'); }
  if (!props.sheetId) { problems.push('SHEET_ID is missing.'); }
  if (!problems.length) {
    try {
      var sheet = getTab_(props.sheetId);
      columnIndexes_(sheet.getDataRange().getValues()[0]);
    } catch (error) {
      problems.push('The Sheet could not be used: ' + error.message);
    }
  }
  var message = problems.length ? 'PROBLEM: ' + problems.join(' ') : 'PASS: the secret and the Sheet are set and the "' + TAB_NAME + '" tab is ready. Web app address (after you deploy): see Deploy > Manage deployments.';
  Logger.log(message);
  return message;
}

/** Run this to prove the script reads the links exactly as the engine makes them, on Google's own servers. */
function selfTest() {
  var failures = [];
  var plain = verifyToken_(SELF_TEST_PLAIN, SELF_TEST_SECRET);
  if (!plain || plain.b !== 'Kelson Agency' || plain.e !== 'ann@example.com' || plain.k !== 'kelsonagency' || plain.t) { failures.push('the plain link was not read correctly'); }
  var unicode = verifyToken_(SELF_TEST_UNICODE_TEST, SELF_TEST_SECRET);
  if (!unicode || unicode.b !== 'Caf\u00e9 \u00dcn\u00efcode \u2615' || unicode.t !== 1) { failures.push('the accented test link was not read correctly'); }
  if (verifyToken_(SELF_TEST_PLAIN, 'another-secret-0987654321') !== null) { failures.push('a link was accepted with the wrong secret'); }
  var parts = SELF_TEST_PLAIN.split('.');
  if (verifyToken_(parts[0] + '.' + (parts[1].charAt(0) === 'A' ? 'B' : 'A') + parts[1].substring(1), SELF_TEST_SECRET) !== null) { failures.push('an altered link was accepted'); }
  var message = failures.length ? 'FAIL: ' + failures.join('; ') : 'PASS: the links made by the engine are read correctly.';
  Logger.log(message);
  if (failures.length) { throw new Error(message); }
  return message;
}
