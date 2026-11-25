/* Algora dashboard: the trader's desk.
   Real data only: the IST clock, exchange calendars, and what the user saved in this browser
   (bots, broker account labels, backtest runs, activity). Nothing here is simulated. */
(function () {
  "use strict";
  var root = document.getElementById("tab-dashboard");
  if (!root) return;
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var $$ = function (s, r) { return Array.prototype.slice.call((r || document).querySelectorAll(s)); };
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  function el(tag, cls, text) { var e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }
  function pad(n) { return (n < 10 ? "0" : "") + n; }
  var DOW = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var fmt0 = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
  function rs(v) { return (v < 0 ? "−₹" : "₹") + fmt0.format(Math.abs(v)); }

  /* ---------- Time in IST, independent of the device's zone ---------- */
  function ist() { return new Date(Date.now() + 330 * 60000); } /* read with getUTC* */
  function keyOf(d) { return d.getUTCFullYear() + "-" + pad(d.getUTCMonth() + 1) + "-" + pad(d.getUTCDate()); }
  function dayFromKey(k) { var p = k.split("-"); return new Date(Date.UTC(+p[0], +p[1] - 1, +p[2])); }
  function addDays(d, n) { return new Date(d.getTime() + n * 86400000); }
  function nowMin() { var d = ist(); return d.getUTCHours() * 60 + d.getUTCMinutes() + d.getUTCSeconds() / 60; }
  function hm(m) { m = Math.round(m); return pad(Math.floor(m / 60) % 24) + ":" + pad(m % 60); }
  function dur(mins) {
    mins = Math.max(0, Math.round(mins));
    var d = Math.floor(mins / 1440), h = Math.floor((mins % 1440) / 60), m = mins % 60;
    return d ? d + "d " + h + "h" : h ? h + "h " + pad(m) + "m" : m + "m";
  }
  function label(d) { return DOW[d.getUTCDay()] + " " + d.getUTCDate() + " " + MON[d.getUTCMonth()]; }

  /* ---------- Exchange calendar ----------
     NSE/BSE equity and F&O holidays 2026: NSE/CMTR/71775, NSE/FAOP/71777 (12 Dec 2025) and NSE/CMTR/72260 (15 Jan added).
     The 2027 list is not published yet; weekdays in 2027 are shown as open and flagged. */
  var HOL = {
    "2026-01-15": "Municipal elections", "2026-01-26": "Republic Day", "2026-03-03": "Holi", "2026-03-26": "Ram Navami",
    "2026-03-31": "Mahavir Jayanti", "2026-04-03": "Good Friday", "2026-04-14": "Ambedkar Jayanti", "2026-05-01": "Maharashtra Day",
    "2026-05-28": "Bakri Id", "2026-06-26": "Muharram", "2026-09-14": "Ganesh Chaturthi", "2026-10-02": "Gandhi Jayanti",
    "2026-10-20": "Dussehra", "2026-11-10": "Diwali Balipratipada", "2026-11-24": "Guru Nanak Jayanti", "2026-12-25": "Christmas"
  };
  var KNOWN_YEARS = { 2026: 1 };
  var MCX_FULL = { "2026-01-26": 1, "2026-04-03": 1, "2026-10-02": 1, "2026-12-25": 1 }; /* MCX closed both sessions */
  function isWeekend(d) { var w = d.getUTCDay(); return w === 0 || w === 6; }
  function nseOpenDay(d) { return !isWeekend(d) && !HOL[keyOf(d)]; }
  function prevTrading(d) { var x = d, i = 0; while (!nseOpenDay(x) && i++ < 10) x = addDays(x, -1); return x; }
  /* US daylight saving: 2nd Sunday of March to 1st Sunday of November. MCX closes 23:30 then, 23:55 otherwise. */
  function nthSunday(y, m, n) { var d = new Date(Date.UTC(y, m, 1)); var add = (7 - d.getUTCDay()) % 7; return Date.UTC(y, m, 1 + add + 7 * (n - 1)); }
  function usDst(d) { var y = d.getUTCFullYear(), t = d.getTime(); return t > nthSunday(y, 2, 2) && t <= nthSunday(y, 10, 1); }
  /* F&O close moves to 15:45 from 7 Dec 2026 (SEBI circular of 15 Oct 2026); 15:40 before that. */
  var FNO_NEW = Date.UTC(2026, 11, 7);
  function sessions(d) {
    var k = keyOf(d), wk = isWeekend(d), hol = HOL[k];
    var mcxClose = usDst(d) ? 23 * 60 + 30 : 23 * 60 + 55;
    var out = { key: k, weekend: wk, holiday: hol || null, nse: null, fno: null, mcx: null };
    if (!wk && !hol) {
      out.nse = { pre: [540, 555], open: [555, 930] };
      out.fno = { open: [555, d.getTime() >= FNO_NEW ? 945 : 940] };
    }
    if (!wk && !MCX_FULL[k]) out.mcx = { open: [hol ? 1020 : 540, mcxClose], evening: !!hol };
    return out;
  }
  /* Expiries: NSE contracts on Tuesday (Nifty weekly, all others monthly = last Tuesday);
     BSE on Thursday (Sensex weekly, others monthly = last Thursday). A holiday moves expiry to the previous trading day. */
  function expiriesFor(d) {
    var res = [], wd = d.getUTCDay(), k = keyOf(d);
    [[2, "NSE", "NIFTY"], [4, "BSE", "SENSEX"]].forEach(function (x) {
      /* nominal weekday in this week (Mon–Sun) on or after d, within 6 days */
      for (var i = 0; i < 7; i++) {
        var n = addDays(d, i);
        if (n.getUTCDay() !== x[0]) continue;
        var adj = prevTrading(n);
        if (keyOf(adj) === k) {
          var monthly = addDays(n, 7).getUTCMonth() !== n.getUTCMonth();
          res.push({ ex: x[1], idx: x[2], monthly: monthly });
        }
      }
    });
    return res;
  }
  function nextExpiry(idx) {
    var t = dayFromKey(keyOf(ist())), late = nowMin() >= 930;
    for (var i = 0; i < 21; i++) {
      var d = addDays(t, i);
      if (!nseOpenDay(d)) continue;
      var hit = expiriesFor(d).filter(function (e) { return e.idx === idx; })[0];
      if (hit && !(i === 0 && late)) return { d: d, monthly: hit.monthly, days: i };
    }
    return null;
  }
  function isMcxInst(inst) { return /^(CRUDEOIL|CRUDEOILM|GOLD|GOLDM|GOLDPETAL|SILVER|SILVERM|NATURALGAS|NATGASMINI|COPPER|ZINC|ALUMINIUM|LEAD|NICKEL)$/.test(inst); }

  /* ---------- Market state sentence ---------- */
  function marketState() {
    var today = dayFromKey(keyOf(ist())), m = nowMin(), s = sessions(today);
    var st = { tone: "off", k: "Market closed", h: "", sub: "" };
    function nextNseOpen() { for (var i = 0; i < 15; i++) { var d = addDays(today, i); if (nseOpenDay(d) && (i > 0 || m < 555)) return d; } return null; }
    var mcxOpen = s.mcx && m >= s.mcx.open[0] && m < s.mcx.open[1];
    if (s.nse && m >= 555 && m < 930) {
      st.tone = "on"; st.k = "NSE open"; st.h = "NSE is open. Cash closes in " + dur(930 - m) + ".";
      st.sub = "F&O trades until " + hm(s.fno.open[1]) + ". MCX is open until " + hm(s.mcx.open[1]) + ".";
    } else if (s.nse && m >= 540 && m < 555) {
      st.tone = "warn"; st.k = "Pre-open"; st.h = "Pre-open. NSE opens in " + dur(555 - m) + ".";
      st.sub = "Orders placed now match at 09:15. MCX opened at 09:00.";
    } else if (s.fno && m >= 930 && m < s.fno.open[1]) {
      st.tone = "warn"; st.k = "F&O only"; st.h = "Cash is closed. F&O trades for " + dur(s.fno.open[1] - m) + " more.";
      st.sub = "MCX is open until " + hm(s.mcx.open[1]) + ".";
    } else {
      var n = nextNseOpen();
      var when = n ? (keyOf(n) === keyOf(today) ? "today" : keyOf(n) === keyOf(addDays(today, 1)) ? "tomorrow" : label(n)) : "";
      st.h = "NSE is closed. It opens " + when + " at 09:15.";
      if (mcxOpen) { st.tone = "warn"; st.k = "MCX open"; st.sub = "MCX trades until " + hm(s.mcx.open[1]) + ", " + dur(s.mcx.open[1] - m) + " from now."; }
      else st.sub = s.holiday ? "Today is a trading holiday: " + s.holiday + "." : s.weekend ? "Weekend. Exchanges reopen on the next trading day." : "Both NSE and MCX are closed right now.";
      if (n) st.untilOpen = (Math.round((n.getTime() - today.getTime()) / 60000)) + 555 - m;
    }
    return st;
  }

  /* ---------- Data from the app ---------- */
  var A = null;
  function data() {
    var store = A && A.store;
    var runs = store ? store.get("runs") : null, log = store ? store.get("log") : null, risk = store ? store.get("risk") : null;
    var accts = [], acc = A ? A.accounts() : {};
    (A ? A.brokers : []).forEach(function (b) { (acc[b[0]] || []).forEach(function (l) { accts.push({ id: b[0], name: b[1], api: b[2], label: l }); }); });
    return { bots: A ? A.bots() : [], accts: accts, runs: Array.isArray(runs) ? runs : [], log: Array.isArray(log) ? log : [], risk: risk && typeof risk === "object" ? risk : {} };
  }

  /* ---------- Motion helpers ---------- */
  var GLYPH = "0123456789ABCDEFGHJKLMNPQRSTUVWXYZ";
  function scramble(node, text) {
    if (reduce || !node) { if (node) node.textContent = text; return; }
    var t0 = performance.now(), D = 520;
    (function f(now) {
      var p = Math.min(1, (now - t0) / D), n = Math.floor(text.length * p), out = text.slice(0, n);
      for (var i = n; i < text.length; i++) out += text[i] === " " ? " " : GLYPH[(Math.random() * GLYPH.length) | 0];
      node.textContent = out;
      if (p < 1) requestAnimationFrame(f); else node.textContent = text;
    })(t0);
  }
  function words(h, text) {
    if (h.getAttribute("data-text") === text) return;
    h.setAttribute("data-text", text); h.setAttribute("aria-label", text); h.innerHTML = "";
    text.split(" ").forEach(function (w, i) {
      var o = el("span", "dk-w"), inn = el("span", null, w);
      inn.style.setProperty("--i", i); inn.setAttribute("aria-hidden", "true");
      o.appendChild(inn); h.appendChild(o); h.appendChild(document.createTextNode(" "));
    });
    if (!reduce) { h.classList.remove("is-in"); void h.offsetWidth; } h.classList.add("is-in");
  }
  function countUp(node, to, fmt) {
    if (reduce || !isFinite(to)) { node.textContent = fmt(to); return; }
    var t0 = performance.now(), D = 800;
    (function f(now) { var p = Math.min(1, (now - t0) / D), e = 1 - Math.pow(1 - p, 3); node.textContent = fmt(to * e); if (p < 1) requestAnimationFrame(f); })(t0);
  }

  /* ---------- Header + clock ---------- */
  function renderHead() {
    var st = marketState(), d = ist();
    $("#dk-mkt-dot").setAttribute("data-s", st.tone);
    var k = $("#dk-mkt-k"); if (k.textContent !== st.k) scramble(k, st.k);
    words($("#dk-h1"), st.h);
    $("#dk-sub").textContent = st.sub;
    $("#dk-date").textContent = DOW[d.getUTCDay()] + " " + d.getUTCDate() + " " + MON[d.getUTCMonth()] + " " + d.getUTCFullYear() + " · IST";
  }
  function tickClock() { var d = ist(); $("#dk-clock").textContent = pad(d.getUTCHours()) + ":" + pad(d.getUTCMinutes()) + ":" + pad(d.getUTCSeconds()); }

  /* ---------- Session timeline (08:30 to 24:00 IST) ---------- */
  var T0 = 510, T1 = 1440;
  function pct(m) { return ((Math.max(T0, Math.min(T1, m)) - T0) / (T1 - T0) * 100).toFixed(3) + "%"; }
  function renderTimeline(D) {
    var box = $("#dk-tl"), today = dayFromKey(keyOf(ist())), s = sessions(today);
    box.innerHTML = "";
    var axis = el("div", "dk-tl__axis");
    for (var h = 9; h <= 24; h += 1) {
      if (h % 3 && h !== 9) continue;
      var t = el("span", null, h === 24 ? "24:00" : pad(h) + ":00"); t.style.left = pct(h * 60); axis.appendChild(t);
    }
    var rows = [
      ["NSE cash", s.nse ? [["pre", s.nse.pre, "Pre-open"], ["open", s.nse.open, "09:15–15:30"]] : null, "nse"],
      ["NSE F&O", s.fno ? [["open", s.fno.open, "09:15–" + hm(s.fno.open[1])]] : null, "nse"],
      ["MCX", s.mcx ? [["open", s.mcx.open, (s.mcx.evening ? "Evening " : "") + hm(s.mcx.open[0]) + "–" + hm(s.mcx.open[1])]] : null, "mcx"]
    ];
    var grid = el("div", "dk-tl__rows");
    rows.forEach(function (r, ri) {
      var row = el("div", "dk-tl__row"); row.appendChild(el("span", "dk-tl__name", r[0]));
      var lane = el("div", "dk-tl__lane");
      if (!r[1]) {
        lane.appendChild(el("span", "dk-tl__closed", s.holiday ? "Closed · " + s.holiday : s.weekend ? "Closed · weekend" : "Closed"));
      } else r[1].forEach(function (seg) {
        var b = el("span", "dk-tl__seg dk-tl__seg--" + seg[0]);
        b.style.left = pct(seg[1][0]); b.style.width = "calc(" + pct(seg[1][1]) + " - " + pct(seg[1][0]) + ")";
        b.style.setProperty("--i", ri);
        if (seg[0] === "open") b.appendChild(el("em", null, seg[2]));
        b.title = r[0] + ": " + seg[2];
        lane.appendChild(b);
      });
      /* bots square off on this lane */
      if (r[1] && ri !== 1) D.bots.forEach(function (bot) {
        if ((r[2] === "mcx") !== isMcxInst(bot.inst)) return;
        var p = bot.sq.split(":"), m = +p[0] * 60 + +p[1];
        var tk = el("i", "dk-tl__tick" + (bot.paused ? " is-paused" : "")); tk.style.left = pct(m);
        tk.title = bot.name + " squares off at " + bot.sq; tk.setAttribute("aria-label", tk.title);
        lane.appendChild(tk);
      });
      row.appendChild(lane); grid.appendChild(row);
    });
    var now = el("div", "dk-tl__now"); now.id = "dk-tl-now";
    now.appendChild(el("b", null, hm(nowMin())));
    var wrap = el("div", "dk-tl__wrap"); wrap.appendChild(axis); wrap.appendChild(grid); wrap.appendChild(now);
    box.appendChild(wrap);
    moveNow();
    var n = D.bots.length;
    $("#dk-tl-cap").textContent = (n ? "Ticks mark each bot's square-off. " : "") + (s.fno && s.fno.open[1] === 940 ? "F&O closes 15:45 from 7 Dec 2026." : "IST");
  }
  function moveNow() {
    var n = $("#dk-tl-now"); if (!n) return;
    var m = nowMin(); n.style.left = pct(m); n.hidden = m < T0;
    $("b", n).textContent = hm(m);
  }

  /* ---------- KPIs ---------- */
  function renderKpis(D) {
    var box = $("#dk-kpis"); box.innerHTML = "";
    var active = D.bots.filter(function (b) { return !b.paused; }).length;
    var brokersUsed = {}; D.accts.forEach(function (a) { brokersUsed[a.id] = 1; });
    var nx = nextExpiry("NIFTY"), sx = nextExpiry("SENSEX"), last = D.runs[0];
    var items = [
      ["Bots", D.bots.length, D.bots.length ? active + " active · " + (D.bots.length - active) + " paused" : "None saved yet"],
      ["Broker accounts", D.accts.length, D.accts.length ? Object.keys(brokersUsed).length + (Object.keys(brokersUsed).length === 1 ? " broker" : " brokers") + " · labels only" : "None added yet"],
      ["Backtests run", D.runs.length, last ? "Last on " + label(new Date(last.t + 330 * 60000)) : "None yet"],
      ["Next Nifty expiry", nx ? (nx.days === 0 ? "Today" : label(nx.d)) : "—", nx ? (nx.monthly ? "Monthly" : "Weekly") + (nx.days ? " · in " + nx.days + (nx.days === 1 ? " day" : " days") : "") : ""],
      ["Next Sensex expiry", sx ? (sx.days === 0 ? "Today" : label(sx.d)) : "—", sx ? (sx.monthly ? "Monthly" : "Weekly") + (sx.days ? " · in " + sx.days + (sx.days === 1 ? " day" : " days") : "") : ""],
      ["Paper P&L", "—", "Starts when the paper engine runs"]
    ];
    items.forEach(function (x, i) {
      var c = el("div", "dk-kpi"); c.setAttribute("role", "listitem"); c.style.setProperty("--i", i);
      c.appendChild(el("span", "dk-kpi__k", x[0]));
      var f = el("b", "dk-kpi__v"); c.appendChild(f);
      if (typeof x[1] === "number") countUp(f, x[1], function (v) { return String(Math.round(v)); }); else f.textContent = x[1];
      c.appendChild(el("span", "dk-kpi__s", x[2]));
      box.appendChild(c);
    });
  }

  /* ---------- Bots table ---------- */
  function renderBots(D) {
    var box = $("#dk-bots"); box.innerHTML = "";
    if (!D.bots.length) {
      var e = el("div", "dk-empty");
      e.appendChild(el("p", null, "No bots yet. Start from a template, backtest it on your own candles, then save it as a paper bot."));
      var b = el("button", "dk-btn dk-btn--solid", "Build your first bot"); b.type = "button"; b.setAttribute("data-goto", "bots"); e.appendChild(b);
      box.appendChild(e); return;
    }
    var today = sessions(dayFromKey(keyOf(ist()))), m = nowMin();
    var wrap = el("div", "dk-tblwrap"), t = el("table", "dk-tbl dk-tbl--bots"), th = el("thead"), tr = el("tr");
    ["Bot", "Account", "Square-off", "State", ""].forEach(function (h, i) { var c = el("th", i === 4 ? "dk-r" : null, h); c.scope = "col"; tr.appendChild(c); });
    th.appendChild(tr); t.appendChild(th);
    var tb = el("tbody");
    D.bots.forEach(function (b, i) {
      var r = el("tr"); r.style.setProperty("--i", i);
      var c0 = el("th"); c0.scope = "row"; c0.appendChild(el("b", null, b.name)); var sm = el("small", null, b.inst + " · " + b.tf + " · " + (b.exit === "flip" ? "flip" : (b.exit === "stop" ? "stop " : "target ") + b.exitv + "%"));
      sm.appendChild(el("span", "dk-m-only", " · off " + b.sq)); c0.appendChild(sm); r.appendChild(c0);
      r.appendChild(el("td", "dk-wrap", b.acctLabel));
      var p = b.sq.split(":"), sqm = +p[0] * 60 + +p[1], open = isMcxInst(b.inst) ? today.mcx : today.nse;
      var c3 = el("td", "dk-mono", b.sq);
      if (open && m < sqm && m >= open.open[0] - 60) c3.appendChild(el("small", null, "in " + dur(sqm - m)));
      r.appendChild(c3);
      var c4 = el("td"); c4.appendChild(el("span", "dk-state dk-state--" + (b.paused ? "paused" : "ready"), b.paused ? "Paused" : "Ready"));
      r.appendChild(c4);
      var c5 = el("td", "dk-r"), btn = el("button", "dk-btn dk-btn--sm", b.paused ? "Resume" : "Pause"); btn.type = "button";
      btn.setAttribute("aria-label", (b.paused ? "Resume " : "Pause ") + b.name);
      btn.addEventListener("click", function () { A.setPaused(i, !b.paused); });
      c5.appendChild(btn); r.appendChild(c5);
      tb.appendChild(r);
    });
    t.appendChild(tb); wrap.appendChild(t); box.appendChild(wrap);
  }

  /* ---------- Path to live ---------- */
  function renderReady(D) {
    var steps = [
      ["Add a broker account", D.accts.length > 0, "Label each account you trade", ["data-goto-anchor", "brokers"]],
      ["Build a bot", D.bots.length > 0, "Rules, exits and square-off", ["data-goto", "bots"]],
      ["Backtest on your data", D.runs.length > 0, "Your own candle CSV", ["data-goto", "backtest"]],
      ["Paper trade", null, "Next: the live-price paper engine"],
      ["Go live", null, "In development: broker API and algo tag"]
    ];
    var done = steps.filter(function (s) { return s[1] === true; }).length;
    var box = $("#dk-ready"); box.innerHTML = "";
    var ring = el("div", "dk-ring"); var R = 26, C = 2 * Math.PI * R;
    ring.innerHTML = '<svg viewBox="0 0 64 64" aria-hidden="true"><circle cx="32" cy="32" r="' + R + '" class="t"/><circle cx="32" cy="32" r="' + R + '" class="v" style="stroke-dasharray:' + C.toFixed(2) + ';--off:' + (C * (1 - done / 3)).toFixed(2) + ';--c:' + C.toFixed(2) + '"/></svg>';
    var rl = el("div", "dk-ring__l"); rl.appendChild(el("b", null, done + "/3")); rl.appendChild(el("span", null, "done")); ring.appendChild(rl);
    box.appendChild(ring);
    var ol = el("ol", "dk-steps");
    steps.forEach(function (s, i) {
      var li = el("li", s[1] === true ? "is-done" : s[1] === false ? "is-todo" : "is-later"); li.style.setProperty("--i", i);
      li.appendChild(el("i", null, s[1] === true ? "✓" : String(i + 1)));
      var d = el("div"); d.appendChild(el("b", null, s[0])); d.appendChild(el("span", null, s[2])); li.appendChild(d);
      if (s[1] === false && s[3]) { var a = el("button", "dk-link", "Start"); a.type = "button"; a.setAttribute(s[3][0], s[3][1]); li.appendChild(a); }
      ol.appendChild(li);
    });
    box.appendChild(ol);
    $("#dk-ready-cap").textContent = done === 3 ? "Ready for paper" : 3 - done + " to go";
  }

  /* ---------- Kill switch ---------- */
  function renderKill(D) {
    var btn = $("#dk-kill"), all = D.bots.length && D.bots.every(function (b) { return b.paused; });
    btn.setAttribute("aria-pressed", all ? "true" : "false");
    btn.disabled = !D.bots.length;
    $("#dk-kill-t").textContent = all ? "Resume all bots" : "Pause all bots";
    btn.title = D.bots.length ? (all ? "Lets every bot take new entries again." : "Stops new entries on every bot at once.") : "No bots to pause yet.";
  }

  /* ---------- Positions / orders / engine log ---------- */
  var term = "pos";
  function renderTerm() {
    var box = $("#dk-term"); box.innerHTML = "";
    var cols = { pos: ["Symbol", "Bot", "Qty", "Avg", "LTP", "P&L"], ord: ["Time", "Symbol", "Side", "Qty", "Price", "Status", "Fill delay"], log: ["Time", "Level", "Bot", "Message"] }[term];
    var msg = {
      pos: "No open positions. When the paper engine runs your bots, positions open here with live P&L, grouped by bot.",
      ord: "No orders yet. Every order will show its status and the delay from signal to fill, so you can see slippage against your backtest.",
      log: "The engine log is empty. Signals, fills, rejections and errors will stream here, filterable by bot and severity."
    }[term];
    var wrap = el("div", "dk-tblwrap"), t = el("table", "dk-tbl dk-tbl--term"), tr = el("tr");
    cols.forEach(function (c) { var h = el("th", null, c); h.scope = "col"; tr.appendChild(h); });
    var th = el("thead"); th.appendChild(tr); t.appendChild(th); wrap.appendChild(t); box.appendChild(wrap);
    var e = el("div", "dk-empty dk-empty--term"); e.appendChild(el("p", null, msg)); box.appendChild(e);
  }
  $$("[data-term]").forEach(function (b) {
    b.addEventListener("click", function () {
      term = b.getAttribute("data-term");
      $$("[data-term]").forEach(function (o) { o.setAttribute("aria-selected", o === b ? "true" : "false"); });
      renderTerm();
    });
  });

  /* ---------- Last backtest ---------- */
  function spark(pts, w, h, cls) {
    if (!pts || pts.length < 2) pts = [0, 0];
    var lo = Math.min.apply(null, pts.concat(0)), hi = Math.max.apply(null, pts.concat(0)); if (hi === lo) { hi += 1; lo -= 1; }
    var X = function (i) { return (i / (pts.length - 1) * w).toFixed(1); }, Y = function (v) { return (3 + (hi - v) / (hi - lo) * (h - 6)).toFixed(1); };
    var d = pts.map(function (v, i) { return (i ? "L" : "M") + X(i) + " " + Y(v); }).join(" ");
    return '<svg class="' + cls + '" viewBox="0 0 ' + w + " " + h + '" preserveAspectRatio="none" aria-hidden="true"><line x1="0" x2="' + w + '" y1="' + Y(0) + '" y2="' + Y(0) + '" class="z"/><path class="a" d="' + d + " L" + w + " " + Y(lo) + " L0 " + Y(lo) + ' Z"/><path class="l" pathLength="1" d="' + d + '"/></svg>';
  }
  function renderBt(D) {
    var box = $("#dk-bt"), r = D.runs[0]; box.innerHTML = "";
    if (!r) {
      $("#dk-bt-cap").textContent = "";
      var e = el("div", "dk-empty dk-empty--col");
      e.appendChild(el("p", null, "Run First Candle Cross on your own candle CSV. The latest result, and your earlier runs, show here."));
      var b = el("button", "dk-btn dk-btn--solid", "Open the backtester"); b.type = "button"; b.setAttribute("data-goto", "backtest"); e.appendChild(b);
      box.appendChild(e); return;
    }
    var when = new Date(r.t + 330 * 60000);
    $("#dk-bt-cap").textContent = label(when) + ", " + pad(when.getUTCHours()) + ":" + pad(when.getUTCMinutes());
    var head = el("div", "dk-bt__head");
    head.appendChild(el("b", null, r.strategy));
    head.appendChild(el("span", null, (r.file || "Your file") + (r.from ? " · " + r.from.split("-").reverse().join("/") + " – " + r.to.split("-").reverse().join("/") : "") + (r.tf ? " · " + r.tf : "")));
    box.appendChild(head);
    var sp = el("div", "dk-bt__chart" + (r.net < 0 ? " is-down" : "")); sp.innerHTML = spark(r.eq, 300, 90, "dk-spark"); box.appendChild(sp);
    requestAnimationFrame(function () { sp.classList.add("is-drawn"); });
    var dl = el("dl", "dk-bt__stats");
    [["Net P&L", rs(r.net), r.net > 0 ? "up" : r.net < 0 ? "down" : ""], ["Trades", String(r.trades)], ["Win rate", r.win == null ? "—" : r.win + "%"],
     ["Profit factor", r.pf == null ? "—" : String(r.pf)], ["Max drawdown", r.dd ? "−₹" + fmt0.format(r.dd) : "₹0", r.dd ? "down" : ""]].forEach(function (x) {
      var d = el("div"); d.appendChild(el("dt", null, x[0])); d.appendChild(el("dd", x[2] ? "is-" + x[2] : null, x[1])); dl.appendChild(d);
    });
    box.appendChild(dl);
    if (D.runs.length > 1) {
      var ul = el("ul", "dk-runs");
      D.runs.slice(1, 4).forEach(function (x) {
        var li = el("li"), w = new Date(x.t + 330 * 60000);
        li.innerHTML = spark(x.eq, 60, 18, "dk-spark dk-spark--mini");
        li.appendChild(el("span", null, label(w)));
        li.appendChild(el("span", "dk-mono", x.trades + " trades"));
        li.appendChild(el("b", x.net > 0 ? "is-up" : x.net < 0 ? "is-down" : null, rs(x.net)));
        ul.appendChild(li);
      });
      box.appendChild(ul);
    }
    box.appendChild(el("p", "dk-note", "Your backtest on your data, with your costs. Past results don't predict future returns."));
  }

  /* ---------- Calendar ---------- */
  function renderCal() {
    var box = $("#dk-cal"), today = dayFromKey(keyOf(ist())); box.innerHTML = "";
    var unknown = false;
    for (var i = 0; i < 14; i++) {
      var d = addDays(today, i), s = sessions(d), c = el("div", "dk-day");
      c.style.setProperty("--i", i);
      if (i === 0) c.classList.add("is-today");
      if (!s.nse) c.classList.add(s.weekend ? "is-weekend" : "is-holiday");
      if (!KNOWN_YEARS[d.getUTCFullYear()] && !s.weekend) unknown = true;
      c.appendChild(el("span", "dk-day__w", i === 0 ? "Today" : DOW[d.getUTCDay()]));
      c.appendChild(el("b", "dk-day__n", String(d.getUTCDate())));
      c.appendChild(el("span", "dk-day__m", MON[d.getUTCMonth()]));
      var tags = el("div", "dk-day__tags");
      if (s.holiday) tags.appendChild(el("span", "dk-tag dk-tag--hol", s.holiday));
      else if (s.weekend) tags.appendChild(el("span", "dk-tag dk-tag--muted", "Closed"));
      else {
        expiriesFor(d).forEach(function (e) {
          tags.appendChild(el("span", "dk-tag dk-tag--" + e.ex.toLowerCase(), (e.idx === "NIFTY" ? "Nifty" : "Sensex") + (e.monthly ? " monthly" : " weekly")));
        });
        if (d.getTime() === FNO_NEW) tags.appendChild(el("span", "dk-tag dk-tag--new", "F&O to 15:45"));
      }
      if (s.holiday && s.mcx) tags.appendChild(el("span", "dk-tag dk-tag--muted", "MCX evening"));
      c.appendChild(tags);
      box.appendChild(c);
    }
    $("#dk-cal-foot").textContent = "NSE expiries fall on Tuesday and BSE expiries on Thursday, moved to the previous trading day on a holiday. Holidays from NSE's 2026 circulars." + (unknown ? " NSE hasn't published its 2027 holiday list yet, so 2027 weekdays show as open." : "");
  }

  /* ---------- Activity ---------- */
  function ago(t) {
    var s = (Date.now() - t) / 1000;
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + "m ago"; if (s < 86400) return Math.floor(s / 3600) + "h ago";
    var d = new Date(t + 330 * 60000); return label(d);
  }
  function renderAct(D) {
    var box = $("#dk-act"); box.innerHTML = "";
    if (!D.log.length) { var li = el("li", "dk-act__empty", "Nothing yet. Saving a bot, adding an account or running a backtest shows up here."); box.appendChild(li); return; }
    D.log.slice(0, 9).forEach(function (x, i) {
      var li = el("li"); li.style.setProperty("--i", i);
      li.appendChild(el("time", null, ago(x.t))); li.appendChild(el("span", null, x.m)); box.appendChild(li);
    });
  }

  /* ---------- Accounts ---------- */
  function renderAcc(D) {
    var box = $("#dk-acc"); box.innerHTML = "";
    $("#dk-h-broker").textContent = D.accts.length ? D.accts.length + (D.accts.length === 1 ? " label" : " labels") + " · API not connected" : "Not connected";
    if (!D.accts.length) {
      var e = el("div", "dk-empty"); e.appendChild(el("p", null, "No accounts yet. Add a label for each account you trade, so every bot knows where it belongs."));
      var b = el("button", "dk-btn dk-btn--solid", "Add an account"); b.type = "button"; b.setAttribute("data-goto-anchor", "brokers"); e.appendChild(b);
      box.appendChild(e); return;
    }
    var grid = el("div", "dk-accs");
    D.accts.forEach(function (a, i) {
      var c = el("div", "dk-acc"); c.style.setProperty("--i", i);
      c.appendChild(A.tile(a.id, a.name, true));
      var t = el("div"); t.appendChild(el("b", null, a.label)); t.appendChild(el("span", null, a.name + " · " + a.api)); c.appendChild(t);
      var n = D.bots.filter(function (bt) { return bt.acctLabel === a.name + " · " + a.label; }).length;
      var s = el("div", "dk-acc__s"); s.appendChild(el("span", "dk-state dk-state--off", "API not connected")); s.appendChild(el("small", null, n + (n === 1 ? " bot" : " bots")));
      c.appendChild(s); grid.appendChild(c);
    });
    box.appendChild(grid);
  }

  /* ---------- Risk limits (saved; enforced once the engine runs) ---------- */
  var riskForm = $("#dk-risk");
  function loadRisk(D) {
    var r = D.risk;
    if (document.activeElement && riskForm.contains(document.activeElement)) return;
    $("#dk-risk-loss").value = r.loss != null ? r.loss : "";
    $("#dk-risk-trades").value = r.trades != null ? r.trades : "";
    $("#dk-risk-cut").value = r.cut || "";
  }
  var riskT;
  riskForm.addEventListener("input", function () {
    clearTimeout(riskT);
    riskT = setTimeout(function () {
      var loss = parseFloat($("#dk-risk-loss").value), trades = parseInt($("#dk-risk-trades").value, 10), cut = $("#dk-risk-cut").value;
      var out = {}, msg = $("#dk-risk-msg");
      if (!isNaN(loss)) { if (loss < 0 || loss > 1e8) { msg.textContent = "Loss limit must be 0 or more."; return; } out.loss = Math.round(loss); }
      if (!isNaN(trades)) { if (trades < 1 || trades > 500) { msg.textContent = "Max trades must be 1 to 500."; return; } out.trades = trades; }
      if (/^([01]\d|2[0-3]):[0-5]\d$/.test(cut)) out.cut = cut;
      A.store.set("risk", out);
      msg.textContent = "Saved. The paper engine will enforce these across every bot.";
    }, 400);
  });
  riskForm.addEventListener("submit", function (e) { e.preventDefault(); });

  /* ---------- Render all ---------- */
  function renderAll() {
    if (!A) return;
    var D = data();
    $$(".dk-main > *, .dk-side > *", root).forEach(function (n, i) { n.style.setProperty("--n", i % 4); });
    renderHead(); renderTimeline(D); renderKpis(D); renderBots(D); renderReady(D); renderKill(D);
    renderBt(D); renderCal(); renderAct(D); renderAcc(D); loadRisk(D);
    $("#dk-h-store").textContent = A.store.ok ? "This browser · " + (function (n) { return n + (n === 1 ? " item" : " items"); })(D.bots.length + D.accts.length + D.runs.length) : "This tab only";
    $("#dk-h-store-led").setAttribute("data-s", A.store.ok ? "on" : "warn");
  }
  function visible() { return !root.hidden; }
  function enter() {
    root.classList.remove("dk-in"); void root.offsetWidth;
    renderAll(); renderTerm();
    requestAnimationFrame(function () { root.classList.add("dk-in"); });
  }

  $("#dk-kill").addEventListener("click", function () {
    var D = data(); if (!D.bots.length) return;
    var all = D.bots.every(function (b) { return b.paused; });
    var n = A.pauseAll(!all);
    A.toast(all ? "All bots resumed" : "All bots paused" + (n ? " (" + n + ")" : ""));
  });

  var pend;
  document.addEventListener("algora:change", function () { if (!visible()) return; clearTimeout(pend); pend = setTimeout(renderAll, 30); });
  function boot() {
    A = window.AlgoraApp;
    tickClock();
    setInterval(function () {
      tickClock();
      if (!visible()) return;
      moveNow();
      var d = ist();
      if (d.getUTCSeconds() === 0) { renderHead(); var D = data(); renderBots(D); renderKpis(D); if (d.getUTCMinutes() === 0 && d.getUTCHours() === 0) { renderTimeline(D); renderCal(); } }
    }, 1000);
    new MutationObserver(function () { if (visible()) enter(); }).observe(root, { attributes: true, attributeFilter: ["hidden"] });
    if (visible()) enter();
  }
  if (window.AlgoraApp) boot(); else document.addEventListener("algora:ready", boot);
})();
