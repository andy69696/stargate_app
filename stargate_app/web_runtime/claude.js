/* Stargate on Databricks Apps: stands in for the claude.ai artifact runtime so the workspace pages run unchanged.
 *
 * The pages call claude.use('user' | 'db' | 'sample' | 'downloads').  Here each one is backed by the FastAPI server:
 *   user       GET  /api/me                 the signed-in Databricks user (X-Forwarded-Email from the Apps proxy)
 *   db         GET  /api/db, PUT/DELETE /api/db/{collection}/{id}
 *                                            a Firestore-style store (collection / doc / onSnapshot) kept in a Delta table
 *   sample     POST /api/sample             the Explain and Ask features, answered by the Databricks model serving endpoint
 *   downloads  in the browser               saves a file with a Blob link
 * Served at /_stargate/claude.js and injected into every page's <head> by server.py; the page files stay byte-identical.
 */
(function () {
  'use strict';
  if (window.claude && window.claude.__stargate) return;

  var POLL_MS = 15000;

  function api(method, path, body, signal) {
    var init = {method: method, headers: {}, cache: 'no-store', credentials: 'same-origin'};
    if (body !== undefined) { init.headers['Content-Type'] = 'application/json'; init.body = JSON.stringify(body); }
    if (signal) init.signal = signal;
    return fetch(path, init).then(function (r) {
      return r.text().then(function (t) {
        var j = null; try { j = t ? JSON.parse(t) : null; } catch (e) { j = {error: t}; }
        if (!r.ok) { var err = new Error((j && j.error) || ('HTTP ' + r.status)); err.status = r.status; err.body = j; throw err; }
        return j;
      });
    });
  }

  // ---------------------------------------------------------------- db: Firestore-style API over /api/db
  var store = {};            // collection -> {id: data}
  var loaded = false;
  var listeners = [];        // {kind: 'col'|'doc', col, id, order, dir, lim, cb, err, last}
  var pollTimer = null;
  var inflight = null;

  function clone(v) { return v === undefined ? undefined : JSON.parse(JSON.stringify(v)); }
  function docSnap(col, id) {
    var has = !!(store[col] && Object.prototype.hasOwnProperty.call(store[col], id));
    var data = has ? store[col][id] : undefined;
    return {id: id, exists: has, ref: makeDoc(col, id), data: function () { return clone(data); }};
  }
  function colSnap(l) {
    var c = store[l.col] || {};
    var ids = Object.keys(c);
    if (l.order) {
      ids.sort(function (a, b) {
        var x = c[a] && c[a][l.order], y = c[b] && c[b][l.order];
        if (x === y) return 0; if (x === undefined || x === null) return 1; if (y === undefined || y === null) return -1;
        return (x < y ? -1 : 1) * (l.dir === 'desc' ? -1 : 1);
      });
    }
    if (l.lim) ids = ids.slice(0, l.lim);
    var docs = ids.map(function (id) { return docSnap(l.col, id); });
    return {docs: docs, size: docs.length, empty: !docs.length, forEach: function (f) { docs.forEach(f); }};
  }
  function fingerprint(l) {
    if (l.kind === 'doc') { var c = store[l.col] || {}; return JSON.stringify(c[l.id] === undefined ? null : c[l.id]); }
    return JSON.stringify(colSnap(l).docs.map(function (d) { return [d.id, store[l.col][d.id]]; }));
  }
  function notify(force) {
    listeners.forEach(function (l) {
      var f = fingerprint(l);
      if (!force && l.last === f) return;
      l.last = f;
      try { l.cb(l.kind === 'doc' ? docSnap(l.col, l.id) : colSnap(l)); } catch (e) { console.error(e); }
    });
  }
  function refresh() {
    if (inflight) return inflight;
    inflight = api('GET', '/api/db').then(function (all) {
      store = (all && all.collections) || {}; loaded = true; inflight = null; notify(false);
    }, function (e) {
      inflight = null;
      listeners.forEach(function (l) { if (l.err && !loaded) { try { l.err(e); } catch (x) {} } });
    });
    return inflight;
  }
  function ensurePolling() {
    if (pollTimer) return;
    pollTimer = setInterval(function () { if (listeners.length && !document.hidden) refresh(); }, POLL_MS);
  }
  function listen(l) {
    listeners.push(l); ensurePolling();
    if (loaded) { l.last = fingerprint(l); setTimeout(function () { l.cb(l.kind === 'doc' ? docSnap(l.col, l.id) : colSnap(l)); }, 0); }
    else refresh();
    return function unsubscribe() { var i = listeners.indexOf(l); if (i >= 0) listeners.splice(i, 1); };
  }
  function splitPath(p) {
    var parts = String(p).split('/').filter(Boolean);
    if (parts.length !== 2) throw new Error('Only collection/doc paths are supported: ' + p);
    return parts;
  }
  function makeDoc(col, id) {
    return {
      id: id, path: col + '/' + id,
      get: function () { return refresh().then(function () { return docSnap(col, id); }); },
      set: function (data) {
        var prev = store[col] && store[col][id];
        (store[col] = store[col] || {})[id] = clone(data); notify(false);
        return api('PUT', '/api/db/' + encodeURIComponent(col) + '/' + encodeURIComponent(id), {data: data}).catch(function (e) {
          if (prev === undefined) delete store[col][id]; else store[col][id] = prev; notify(false); throw e;
        });
      },
      delete: function () {
        var prev = store[col] && store[col][id];
        if (store[col]) delete store[col][id]; notify(false);
        return api('DELETE', '/api/db/' + encodeURIComponent(col) + '/' + encodeURIComponent(id)).catch(function (e) {
          if (prev !== undefined) (store[col] = store[col] || {})[id] = prev; notify(false); throw e;
        });
      },
      onSnapshot: function (cb, err) { return listen({kind: 'doc', col: col, id: id, cb: cb, err: err}); }
    };
  }
  function makeQuery(col, order, dir, lim) {
    return {
      orderBy: function (f, d) { return makeQuery(col, f, d || 'asc', lim); },
      limit: function (n) { return makeQuery(col, order, dir, n); },
      get: function () { return refresh().then(function () { return colSnap({col: col, order: order, dir: dir, lim: lim}); }); },
      onSnapshot: function (cb, err) { return listen({kind: 'col', col: col, order: order, dir: dir, lim: lim, cb: cb, err: err}); }
    };
  }
  function makeCollection(col) {
    var q = makeQuery(col, null, null, null);
    q.id = col;
    q.doc = function (id) { return makeDoc(col, id); };
    return q;
  }
  var db = {
    collection: makeCollection,
    doc: function (p) { var a = splitPath(p); return makeDoc(a[0], a[1]); }
  };

  // ---------------------------------------------------------------- user
  var mePromise = null;
  function me() { return mePromise || (mePromise = api('GET', '/api/me').catch(function () { return {}; })); }
  var user = {
    id: function () { return me().then(function (m) { return (m && m.id) || null; }); },
    name: function () { return me().then(function (m) { return (m && (m.name || m.id)) || null; }); },
    can: function () { return Promise.resolve(true); }
  };

  // ---------------------------------------------------------------- sample (Explain and Ask)
  function sample(input, opts) {
    opts = opts || {};
    return api('POST', '/api/sample', {input: input, max_tokens: opts.max_tokens || opts.maxTokens || null}, opts.signal).then(function (r) {
      var text = (r && r.text) || '';
      if (opts.onText) { try { opts.onText({text: text}); } catch (e) {} }
      return {text: text, truncated: !!(r && r.truncated)};
    }, function (e) {
      if (e && e.name === 'AbortError') { var c = new Error('cancelled'); c.code = 'cancelled'; throw c; }
      var x = new Error((e && e.message) || 'error'); x.code = (e && e.body && e.body.code) || 'error'; x.text = ''; throw x;
    });
  }
  sample.limits = function () { return Promise.resolve({tools: false}); };

  // ---------------------------------------------------------------- downloads
  var downloads = {
    save: function (o) {
      o = o || {};
      var blob = o.data instanceof Blob ? o.data : new Blob([o.data == null ? '' : o.data], {type: o.mimeType || o.type || 'application/octet-stream'});
      var a = document.createElement('a'); a.href = URL.createObjectURL(blob); a.download = o.filename || 'download';
      document.body.appendChild(a); a.click();
      setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 1000);
      return Promise.resolve(true);
    }
  };

  // ---------------------------------------------------------------- claude.use
  var status = null;
  function getStatus() { return status || (status = api('GET', '/api/status').catch(function () { return {}; })); }
  window.claude = {
    __stargate: true,
    use: function (name) {
      switch (name) {
        case 'user': return Promise.resolve(user);
        case 'db': return Promise.resolve(db);
        case 'downloads': return Promise.resolve(downloads);
        case 'sample': return getStatus().then(function (s) { return s && s.llm && s.llm.enabled === false ? null : sample; });
        default: return Promise.resolve(null);
      }
    }
  };
})();
