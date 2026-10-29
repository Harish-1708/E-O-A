/* The unsubscribe page's behaviour. Everything the server sends is put on the page as TEXT (textContent), never as markup. The requests carry no cookies and no Google sign-in. */
(function () {
  'use strict';
  var API = String(window.UNSUBSCRIBE_API || '');
  var API_OK = /^https:\/\/script\.google\.com\/macros\/s\/[A-Za-z0-9_-]+\/exec$/.test(API);
  var FALLBACK_REASONS = ['Too many emails', 'Not relevant to me', 'I never signed up', 'Other'];
  var MAX_DETAILS = 500;
  var token = new URLSearchParams(window.location.search).get('t') || '';
  var topBar = document.getElementById('top');
  var body = document.getElementById('bd');
  var foot = document.getElementById('ft');

  function make(tag, className, text) {
    var node = document.createElement(tag);
    if (className) { node.className = className; }
    if (text !== undefined) { node.textContent = text; }
    return node;
  }
  function clear(node) { node.removeAttribute('aria-busy'); while (node.firstChild) { node.removeChild(node.firstChild); } }
  function say(kind, title, lines) {
    clear(body);
    body.appendChild(make('div', kind, kind === 'tick' ? '\u2713' : '!'));
    body.appendChild(make('h1', '', title));
    lines.forEach(function (line) { body.appendChild(make('p', '', line)); });
  }
  function reply() { return 'Please reply unsubscribe to the email you received and we will remove you.'; }
  function unavailable() { say('warn', 'Unsubscribe is not available right now', [reply()]); }
  function invalid() {
    say('warn', 'This link is not valid', ['The unsubscribe link is incomplete or was changed.', 'To stop these emails, reply unsubscribe to the email you received and we will remove you.']);
    foot.hidden = false; foot.textContent = 'No information was recorded.';
  }
  function words(brand) { return brand ? 'emails like this one from ' + brand : 'emails like this one'; }

  function request(url, options) {
    var controller = typeof AbortController === 'function' ? new AbortController() : null;
    var timer = controller ? setTimeout(function () { controller.abort(); }, 20000) : null;
    var settings = Object.assign({ credentials: 'omit', redirect: 'follow', referrerPolicy: 'no-referrer', cache: 'no-store' }, options || {});
    if (controller) { settings.signal = controller.signal; }
    return fetch(url, settings).then(function (response) {
      if (timer) { clearTimeout(timer); }
      if (!response.ok) { throw new Error('HTTP ' + response.status); }
      return response.json();
    }, function (error) { if (timer) { clearTimeout(timer); } throw error; });
  }

  function alreadyPage(data) {
    var brand = typeof data.brand === 'string' ? data.brand : '';
    topBar.textContent = brand ? brand.toUpperCase() : 'UNSUBSCRIBE';
    var line = [brand, typeof data.address === 'string' ? data.address : ''].filter(Boolean).join(' \u00b7 ');
    if (line) { foot.hidden = false; foot.textContent = line; }
    say('tick', 'You are already unsubscribed', [(typeof data.masked === 'string' ? data.masked : 'This address') + ' has already been removed from this mailing list. You will not receive any more ' + words(brand) + '.',
                                                 'Unsubscribed by mistake? Reply to any of our emails and we will add you back.']);
  }

  function confirmPage(data) {
    var reasons = Array.isArray(data.reasons) && data.reasons.length && data.reasons.every(function (r) { return typeof r === 'string'; }) ? data.reasons : FALLBACK_REASONS;
    var brand = typeof data.brand === 'string' ? data.brand : '';
    topBar.textContent = brand ? brand.toUpperCase() : 'UNSUBSCRIBE';
    var line = [brand, typeof data.address === 'string' ? data.address : ''].filter(Boolean).join(' \u00b7 ');
    if (line) { foot.hidden = false; foot.textContent = line; }
    clear(body);
    if (data.test === true) { body.appendChild(make('div', 'test', 'This is a test link from your team. Nothing will be recorded.')); }
    body.appendChild(make('h1', '', 'Unsubscribe'));
    var intro = make('p', '');
    intro.appendChild(document.createTextNode('You are about to stop receiving emails from '));
    if (brand) { intro.appendChild(make('b', '', brand)); } else { intro.appendChild(document.createTextNode('this sender')); }
    intro.appendChild(document.createTextNode(' at:'));
    body.appendChild(intro);
    body.appendChild(make('div', 'em', typeof data.masked === 'string' ? data.masked : '***'));
    var question = make('div', 'q', 'Why are you leaving? ');
    question.appendChild(make('span', 'small', '(optional)'));
    body.appendChild(question);
    reasons.forEach(function (reason) {
      var label = make('label', 'opt');
      var input = document.createElement('input');
      input.type = 'radio'; input.name = 'reason'; input.value = reason;
      label.appendChild(input);
      label.appendChild(document.createTextNode(' ' + reason));
      body.appendChild(label);
    });
    var details = document.createElement('textarea');
    details.maxLength = MAX_DETAILS; details.placeholder = 'Tell us more (optional)'; details.id = 'details';
    body.appendChild(details);
    var go = make('button', 'btn', 'Unsubscribe me'); go.type = 'button'; go.id = 'go';
    var stay = make('button', 'link', 'No, keep me subscribed'); stay.type = 'button'; stay.id = 'stay';
    var err = make('div', 'err'); err.id = 'err';
    body.appendChild(go); body.appendChild(stay); body.appendChild(err);

    stay.onclick = function () { say('tick', 'You are still subscribed', ['No problem. Nothing has changed.']); };
    go.onclick = function () {
      go.disabled = true; err.textContent = '';
      var chosen = document.querySelector('input[name=reason]:checked');
      var reason = chosen ? chosen.value : '';
      request(API, { method: 'POST', headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body: JSON.stringify({ api: 'confirm', t: token, reason: reason, details: details.value, defer: true }) }).then(function (result) {
        if (!result || result.ok !== true) { throw new Error('refused'); }
        var lines = ['We have removed ' + (typeof result.masked === 'string' ? result.masked : 'your address') + ' from this mailing list. You will not receive any more ' + words(typeof result.brand === 'string' ? result.brand : '') + '.'];
        if (reason) { lines.push('Thank you for telling us why. It helps us send better email.'); }
        lines.push('Unsubscribed by mistake? Reply to any of our emails and we will add you back.');
        if (result.test === true) { lines.unshift('Test only: nothing was recorded.'); }
        say('tick', 'You are unsubscribed', lines);
        if (result.test !== true) {                         // the second half, in the background: the person is already told, so they do not wait for it
          request(API, { method: 'POST', headers: { 'Content-Type': 'text/plain;charset=utf-8' }, body: JSON.stringify({ api: 'sync', t: token }), keepalive: true }).catch(function () {});
        }
      }).catch(function () {
        go.disabled = false;
        err.textContent = 'Something went wrong. ' + reply();
      });
    };
  }

  if (!API_OK) { unavailable(); return; }
  if (!token || token.length > 2000) { invalid(); return; }
  request(API + '?api=info&t=' + encodeURIComponent(token)).then(function (data) {
    if (data && data.ok === true && (data.state === 'valid' || data.state === 'test')) { if (data.already === true) { alreadyPage(data); } else { confirmPage(data); } }
    else if (data && data.state === 'not set up') { unavailable(); }
    else { invalid(); }
  }).catch(function () { say('warn', 'We could not open this page just now', ['Please try again in a moment. If it keeps happening, ' + reply().charAt(0).toLowerCase() + reply().slice(1)]); });
}());
