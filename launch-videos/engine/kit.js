/* Decibyl video kit: builds a whole video from window.SPEC.
 * Every frame is a pure function of seek(t). Scenes record sound events in
 * window.EVENTS so the soundtrack is generated from the same script. */
(() => {
const SPEC = window.SPEC;
const V = SPEC.aspect === "9:16";
const W = V ? 1080 : 1920, H = V ? 1920 : 1080;
const EVENTS = [];
const UP = [];
const TOUCH = new Set(); // devices whose updaters compose their own transform
let uidN = 0;
const uid = (p = "e") => `${p}${uidN++}`;
const ev = (t, kind, extra = {}) => EVENTS.push({ t: +t.toFixed(3), kind, ...extra });
const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;");

const clamp = (x) => Math.max(0, Math.min(1, x));
const easeOut = (x) => 1 - Math.pow(1 - x, 3);
const easeOutQ = (x) => 1 - Math.pow(1 - x, 5);
const easeIn = (x) => x * x * x;
const easeInOut = (x) => (x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2);
const back = (x) => { const c = 1.6; return 1 + (c + 1) * Math.pow(x - 1, 3) + c * Math.pow(x - 1, 2); };
const lerp = (a, b, x) => a + (b - a) * x;
const $ = (id) => document.getElementById(id);

/* ---------- faces: BlobFace.tsx, exactly ---------- */
const PATHS = [
  "M52 6c18 1 34 12 39 30s-2 39-18 49-39 12-54 2S-1 58 5 40 34 5 52 6z",
  "M60 8c15 5 30 18 31 35 1 18-11 33-26 41s-36 9-48-3S2 47 9 31 45 3 60 8z",
  "M47 5c16-2 33 7 41 22s9 35-1 49-30 21-46 17S9 77 5 60 31 7 47 5z",
];
const PASTEL = { pink: "#F7B5E3", yellow: "#FFD66B", green: "#CDEB7A" };
const SHAPE = { pink: 0, yellow: 1, green: 2 };
function face(color = "pink", size = 26, extra = "") {
  const fill = PASTEL[color] || PASTEL.pink;
  const [rx, ry] = size >= 40 ? [5, 7] : [6, 8];
  return `<svg class="blob ${extra}" width="${size}" height="${size}" viewBox="0 0 100 100"><path d="${PATHS[SHAPE[color] ?? 0]}" fill="${fill}"/>` +
    `<g class="eyes"><ellipse cx="38" cy="46" rx="${rx}" ry="${ry}" fill="#0d0d0d"/><ellipse cx="62" cy="46" rx="${rx}" ry="${ry}" fill="#0d0d0d"/></g></svg>`;
}
const LOGO = `<img src="decibyl-logo.svg" alt="">`;
const I = {
  chat: '<svg class="ico" viewBox="0 0 24 24"><path d="M4 5h16v11H8l-4 4z"/></svg>',
  today: '<svg class="ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><path d="M12 8v4l3 2"/></svg>',
  up: '<svg class="ico" viewBox="0 0 24 24"><path d="M12 19V5m-6 6 6-6 6 6"/></svg>',
  mic: '<svg class="ico" viewBox="0 0 24 24" style="width:14px;height:14px"><path d="M12 3a3 3 0 0 0-3 3v6a3 3 0 0 0 6 0V6a3 3 0 0 0-3-3zM5 11a7 7 0 0 0 14 0M12 18v3"/></svg>',
  check: '<svg class="ico" viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="m8 12 3 3 5-6"/></svg>',
  tick: '<svg class="ico" style="width:13px;height:13px" viewBox="0 0 24 24"><path d="m5 12 4 4 10-10"/></svg>',
  back: '<svg class="ico" viewBox="0 0 24 24"><path d="m15 5-7 7 7 7"/></svg>',
  lock: '<svg class="ico" style="width:13px;height:13px" viewBox="0 0 24 24"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/></svg>',
  phone: '<svg class="ico" viewBox="0 0 24 24"><path d="M5 4h4l2 5-3 2a11 11 0 0 0 5 5l2-3 5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2"/></svg>',
  warn: '<svg class="ico" style="width:26px;height:26px" viewBox="0 0 24 24"><path d="M12 3 2 21h20zM12 10v5M12 18v.5"/></svg>',
  speaker: '<svg class="ico" viewBox="0 0 24 24"><path d="M4 9v6h4l5 4V5L8 9zM16 9a4 4 0 0 1 0 6"/></svg>',
};

/* ---------- small timeline helpers ---------- */
function typer(id, text, ta, tb, caretUntil, ph = "Ask Decibyl") {
  UP.push((t) => {
    const el = $(id); if (!el) return;
    if (t < ta) { el.innerHTML = `<span class="ph">${esc(ph)}</span>`; return; }
    const n = Math.round(clamp((t - ta) / (tb - ta)) * text.length);
    el.innerHTML = esc(text.slice(0, n)) + (t < caretUntil ? '<span class="caret"></span>' : "");
  });
}
function streamer(id, text, ta, cps = 140) {
  const tb = ta + Math.max(0.6, text.length / cps);
  UP.push((t) => { const el = $(id); if (el) el.textContent = text.slice(0, Math.round(clamp((t - ta) / (tb - ta)) * text.length)); });
  return tb;
}
function pointerTap(targetId, tMove, tTap, mobile, sceneOut) {
  const pid = uid("ptr");
  let last = null;
  UP.push((t) => {
    const p = $(pid), target = document.querySelector(`[data-k="${targetId}"]`);
    if (!p) return;
    const r = target ? target.getBoundingClientRect() : { width: 0 };
    if (r.width) last = [r.left + r.width * 0.5, r.top + r.height * 0.55];
    if (!last) { p.style.opacity = 0; return; }
    const [tx, ty] = last;
    if (mobile) {
      const a = clamp((t - (tTap - 0.08)) / 0.45);
      p.style.left = tx + "px"; p.style.top = ty + "px";
      p.style.opacity = t >= tTap - 0.08 && a < 1 ? (1 - a) : 0;
      p.style.transform = `scale(${0.6 + 0.8 * easeOut(a)})`;
    } else {
      const m = easeInOut(clamp((t - tMove) / (tTap - tMove - 0.05)));
      p.style.left = lerp(W * 0.82, tx, m) + "px"; p.style.top = lerp(H * 0.95, ty, m) + "px";
      p.style.opacity = t > tMove - 0.1 && t < sceneOut ? clamp((t - tMove + 0.1) / 0.15) * (1 - clamp((t - tTap - 0.5) / 0.3)) : 0;
      p.style.transform = t > tTap - 0.06 && t < tTap + 0.08 ? "scale(0.88)" : "scale(1)";
    }
    if (target) target.style.transform = t > tTap - 0.06 && t < tTap + 0.08 ? "scale(0.96)" : "";
  });
  ev(tTap, "click");
  return mobile ? `<div class="touch" id="${pid}"></div>`
    : `<svg class="cursor" id="${pid}" viewBox="0 0 24 24"><path d="M4 2l16 9-7 2-3 7z" fill="#0d0d0d" stroke="#fff" stroke-width="1.5" stroke-linejoin="round"/></svg>`;
}
function caption(text, t0, top, pill = false) {
  if (!text) return "";
  const [a, b] = String(text).split("|");
  const size = V ? 72 : 64;
  return `<div class="caption" style="top:${top - (pill ? 12 : 0)}px;font-size:${size}px"><span class="kin ${pill ? "pill" : ""}" data-kin="${t0}">${esc(a)}${b ? ` <span class="muted">${esc(b)}</span>` : ""}</span></div>`;
}

/* ---------- screens (phone) and pages (browser) ---------- */
function statusBar(time = "9:41", dark = false) {
  return `<div class="status" style="${dark ? "color:#fff" : ""}"><span>${time}</span><span class="bat"></span></div>`;
}

// Chat thread with optional typed ask, streamed reply, docked approval and results.
function chatParts(p, t0, mobile) {
  const ids = { typed: uid("ty"), stream: uid("st"), dock: uid("dk") };
  const out = { html: {}, end: t0 };
  let tSend = null, r0;
  if (p.typed) {
    const ta = t0 + 0.35, tb = ta + Math.min(1.8, Math.max(0.9, p.user.length / 55));
    tSend = tb + 0.3;
    typer(ids.typed, p.user, ta, tb, tSend);
    ev(ta, "typing", { until: tb });
    ev(tSend, "send");
    r0 = tSend + 0.5;
  } else r0 = t0 + 0.7;
  const userIn = p.typed ? tSend + 0.05 : t0 + 0.1;
  const rEnd = streamer(ids.stream, p.reply || "", r0);
  let tDock = null, tTap = null, after = rEnd + 0.25;
  if (p.approval) {
    tDock = rEnd + 0.3; ev(tDock, "card");
    tTap = tDock + (p.approval.hold ?? 1.6);
    after = tTap + 0.35;
    ev(tTap + 0.05, "approve");
  }
  const doneHtml = (p.done || []).map((d, i) => {
    const td = after + i * 0.3; ev(td, "done", { i });
    return `<div class="done" data-in="${td.toFixed(2)}" data-dur="0.35" data-dist="10">${I.check}${esc(d)}</div>`;
  }).join("");
  const extraHtml = (p.extra || []).map((x) => `<div class="bot" data-in="${(after + 0.3 * (p.done || []).length).toFixed(2)}">${face(p.color, mobile ? 24 : 30)}<div>${esc(x)}</div></div>`).join("");
  out.end = after + 0.3 * (p.done || []).length + 0.6;
  out.thread = `<div class="thread">
      <div class="user" data-in="${userIn.toFixed(2)}" data-dur="0.35" data-dist="10">${esc(p.user)}</div>
      <div class="bot" data-in="${(r0 - 0.15).toFixed(2)}" data-dur="0.3" data-dist="8">${face(p.color, mobile ? 24 : 30)}<div id="${ids.stream}"></div></div>
      ${doneHtml}${extraHtml}</div>`;
  if (p.approval) {
    const a = p.approval;
    UP.push((t) => {
      const d = $(ids.dock); if (!d) return;
      const pin = easeOut(clamp((t - tDock) / 0.45)), pout = easeIn(clamp((t - tTap - 0.1) / 0.3));
      d.style.opacity = pin * (1 - pout);
      d.style.transform = `translateY(${(1 - pin) * 20 + pout * 8}px)`;
      d.style.display = t > tTap + 0.45 ? "none" : "";
    });
    out.dock = `<div class="dock" id="${ids.dock}" style="opacity:0">
        <div class="wants">Decibyl wants to: <b>${esc(a.wants)}</b></div>
        ${a.detail ? `<div class="detail">${esc(a.detail)}</div>` : ""}
        <div class="acts"><div class="btn primary" data-k="doit">Do it</div><div class="btn">Don't</div></div></div>`;
  } else out.dock = "";
  out.composer = `<div class="composer" data-k="composer"><div id="${ids.typed}" style="flex:1"><span class="ph">Ask Decibyl</span></div>
      <div class="cbtns"><div class="talk">${I.mic}Talk</div><div class="send" data-k="send">${I.up}</div></div></div>`;
  out.tTap = tTap; out.tDock = tDock; out.tSend = tSend;
  return out;
}

const SCREENS = {
  chat(p, t0) {
    const c = chatParts(p, t0, true);
    return { cls: "", html: `${statusBar(p.time)}<div class="mhead">${I.back}${face(p.color, 30)}<div>${esc(p.agent || "Decibyl")}<div class="sm">${esc(p.agentSub || "")}</div></div></div>
      ${c.thread}${c.dock}${c.composer}<div class="tabbar"><div class="on">${I.chat}Chat</div><div>${I.today}Today</div></div>`, end: c.end, tTap: c.tTap, tDock: c.tDock };
  },
  home(p, t0) {
    const id = uid("hm");
    const ta = t0 + (p.typeAt ?? 0.3), tb = ta + Math.min(1.8, Math.max(0.9, (p.ask || "").length / 50));
    const tSend = tb + 0.45;
    if (p.ask) { typer(id, p.ask, ta, tb, tSend); ev(ta, "typing", { until: tb }); ev(tSend, "send"); }
    UP.push((t) => { const s = document.querySelector(`[data-send="${id}"]`); if (s) s.style.transform = t > tSend - 0.06 && t < tSend + 0.08 ? "scale(0.88)" : ""; });
    return { cls: "", html: `${statusBar(p.time)}<div class="home"><div class="greet">${face(p.color || "pink", 56)}What can I do for you${p.name ? ", " + esc(p.name) : ""}?</div>
      <div class="composer" data-k="composer"><div id="${id}" style="flex:1"><span class="ph">Ask Decibyl</span></div><div class="cbtns"><div class="send" data-send="${id}" data-k="send">${I.up}</div></div></div>
      ${p.starters ? `<div class="starters">${p.starters.map((s) => `<div>${esc(s)}</div>`).join("")}</div>` : ""}</div>
      <div class="tabbar"><div class="on">${I.chat}Chat</div><div>${I.today}Today</div></div>`, end: tSend + 0.4 };
  },
  call(p, t0) {
    const wid = uid("wv"), tid = uid("tm"), cid = uid("cp");
    UP.push((t) => {
      const w = $(wid); if (!w) return;
      if (!w.children.length) for (let i = 0; i < 32; i++) w.appendChild(document.createElement("i"));
      [...w.children].forEach((b, i) => {
        const talk = 0.55 + 0.45 * Math.sin(t * 2.1);
        const h = 8 + 76 * talk * Math.abs(Math.sin(i * 0.55 + t * 7.3) * Math.cos(i * 0.21 - t * 3.1));
        b.style.height = h.toFixed(1) + "px"; b.style.opacity = 0.35 + 0.65 * (h / 84);
      });
      const s = Math.max(0, Math.floor((t - t0) * 2.2) + (p.startSec || 4));
      $(tid).textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
    });
    let end = t0 + 1.5;
    if (p.lines) {
      // live captions: each line streams in, replacing the last
      p.lines.forEach((ln, i) => {
        const ta = t0 + (ln.at ?? 0.6 + i * 1.6);
        UP.push((t) => {
          const el = $(cid); if (!el) return;
          const next = p.lines[i + 1], nextAt = next ? t0 + (next.at ?? 0.6 + (i + 1) * 1.6) : Infinity;
          if (t >= ta && t < nextAt) el.innerHTML = `<b style="opacity:.6">${esc(ln.who)}:</b> ` + esc(ln.text.slice(0, Math.round(clamp((t - ta) / Math.max(0.5, ln.text.length / 40)) * ln.text.length)));
          if (t < t0 + (p.lines[0].at ?? 0.6)) el.innerHTML = "";
        });
        end = Math.max(end, ta + ln.text.length / 40 + 0.8);
      });
    }
    return { cls: "call", dark: true, html: `${statusBar(p.time, true)}<div class="who">${face(p.color || "green", 112)}<div class="nm">${esc(p.who)}</div><div class="csub">${esc(p.sub || "")}</div>
      <div class="live"><b></b><span id="${tid}">00:00</span></div></div><div class="wave" id="${wid}"></div><div class="cap" id="${cid}"></div>
      <div class="btns"><div>${I.mic.replace("width:14px;height:14px", "")}</div><div class="end">${I.phone.replace('class="ico"', 'class="ico" style="transform:rotate(135deg)"')}</div><div>${I.speaker}</div></div>`, end };
  },
  messages(p, t0) {
    let end = t0 + 1;
    const msgs = (p.messages || []).map((m, i) => {
      const ta = t0 + (m.at ?? 0.5 + i * 0.6); end = Math.max(end, ta + 0.8);
      ev(ta, "msg", { dir: m.dir });
      return `<div class="bub ${m.dir === "in" ? "in" : "out"}" data-in="${ta.toFixed(2)}" data-dur="0.35" data-dist="12">${esc(m.text)}<small>${esc(m.time || "")}</small></div>`;
    }).join("");
    return { cls: "wa", html: `${statusBar(p.time)}<div class="mhead">${I.back}${face(p.color || "pink", 36)}<div>${esc(p.contact)}<div class="sm">${esc(p.app || "WhatsApp")}</div></div></div>
      <div class="wa-body"><div class="day">${esc(p.day || "Today")}</div>${msgs}</div><div class="wa-in"><div></div><span></span></div>`, end };
  },
  today(p, t0) {
    let end = t0 + 1;
    const rows = (p.rows || []).map((r, i) => {
      const ta = t0 + (r.at ?? 0.4 + i * 0.22); end = Math.max(end, ta + 0.8); ev(ta, "row", { i });
      let barHtml = "";
      if (r.progress != null) {
        const bid = uid("bar"); barHtml = `<div class="bar"><i id="${bid}"></i></div>`;
        UP.push((t) => { const b = $(bid); if (b) b.style.width = (r.progress * easeOut(clamp((t - ta - 0.3) / 1.2))) + "%"; });
      }
      const st = r.status ? `<div class="status-chip ${r.wait ? "wait" : ""}">${r.wait ? "" : I.tick}${esc(r.status)}</div>` : "";
      return `<div class="t-row" data-in="${ta.toFixed(2)}" data-dist="14" ${r.k ? `data-k="${r.k}"` : ""}>${face(r.color || "pink", 42)}<div class="t-main"><div class="t-title">${esc(r.title)}</div><div class="t-sub">${esc(r.sub || "")}</div>${barHtml}${st}</div></div>`;
    }).join("");
    return { cls: "", html: `${statusBar(p.time)}<div class="td-h"><h2>${esc(p.title || "Today")}</h2><div class="date">${esc(p.date || "")}</div></div>
      <div class="td-list">${rows}</div><div class="tabbar"><div>${I.chat}Chat</div><div class="on">${I.today}Today</div></div>`, end };
  },
  lock(p, t0) {
    let end = t0 + 1;
    const ns = (p.notifs || []).map((n, i) => {
      const ta = t0 + (n.at ?? 0.5 + i * 0.5); end = Math.max(end, ta + 1); ev(ta, "msg", { dir: "in" });
      return `<div class="notif" data-in="${ta.toFixed(2)}" data-dur="0.45" data-dist="-20"><div class="app">${n.app === "WhatsApp" ? face("green", 26) : LOGO}</div>
        <div style="flex:1"><div class="nt">${esc(n.title)}<span>${esc(n.when || "now")}</span></div><div class="nx">${esc(n.text)}</div></div></div>`;
    }).join("");
    return { cls: "lock", html: `${statusBar(p.time)}<div class="clock">${esc(p.clock || p.time || "9:41")}</div><div class="ld">${esc(p.date || "")}</div><div style="margin-top:26px">${ns}</div>`, end };
  },
  simple(p, t0) {
    const tv = t0 + (p.verdictAt ?? 1.2);
    if (p.verdict) ev(tv, "card");
    return { cls: "simple", html: `${statusBar(p.time)}<div class="sh">${esc(p.heading)}</div><div class="sbody">
      ${p.card ? `<div class="msgcard" data-in="${(t0 + 0.3).toFixed(2)}"><div class="from">${esc(p.card.from)}</div>${esc(p.card.text)}</div>` : ""}
      ${p.text ? `<div class="bigtext" data-in="${(t0 + 0.3).toFixed(2)}">${esc(p.text)}</div>` : ""}
      ${p.verdict ? `<div class="verdict" data-in="${tv.toFixed(2)}" data-dist="14"><div class="vt">${p.verdict.warn ? I.warn : I.check}${esc(p.verdict.title)}</div><ul>${(p.verdict.reasons || []).map((r) => `<li>${esc(r)}</li>`).join("")}</ul></div>` : ""}
      ${p.button ? `<div class="bigbtn" data-k="bigbtn">${esc(p.button)}</div>` : ""}</div>`, end: tv + 1 };
  },
  settings(p, t0) {
    const rows = (p.rows || []).map((r) => {
      let ctl = r.value ? `<span class="sv">${esc(r.value)}</span>` : "";
      if (r.toggle != null) {
        const tid = uid("tg");
        if (r.flipAt != null) UP.push((t) => { const g = $(tid); if (g) g.classList.toggle("on", t >= t0 + r.flipAt ? !r.toggle : !!r.toggle); });
        ctl = `<div class="tog ${r.toggle ? "on" : ""}" id="${tid}" ${r.k ? `data-k="${r.k}"` : ""}><i></i></div>`;
      }
      return `<div class="set-row" ${r.k && r.toggle == null ? `data-k="${r.k}"` : ""}><span>${esc(r.label)}${r.hint ? `<br><span class="sv">${esc(r.hint)}</span>` : ""}</span>${ctl}</div>`;
    }).join("");
    return { cls: "", html: `${p.mobile !== false ? statusBar(p.time) : ""}<div class="set-h">${esc(p.title)}</div><div class="set-list">${rows}</div>`, end: t0 + 1 };
  },
  onboard(p, t0) {
    const opts = (p.options || []).map((o, i) => {
      const oid = uid("op");
      if (p.selectAt != null && i === p.select) UP.push((t) => { const e = $(oid); if (e) e.classList.toggle("sel", t >= t0 + p.selectAt); });
      return `<div class="opt" id="${oid}" ${i === p.select ? 'data-k="choice"' : ""}>${esc(o.label)}${o.sub ? `<small>${esc(o.sub)}</small>` : ""}</div>`;
    }).join("");
    return { cls: "", html: `${statusBar(p.time)}<div class="set-h" style="padding-top:30px">${esc(p.heading)}</div>${p.sub ? `<div class="sub" style="padding:0 28px 10px;font-size:16px">${esc(p.sub)}</div>` : ""}
      <div class="opts">${opts}</div>${p.field ? `<div class="field">${esc(p.field)}</div>` : ""}
      ${p.button ? `<div style="margin:auto 28px 40px" ><div class="btn primary" data-k="continue" style="text-align:center;padding:14px">${esc(p.button)}</div></div>` : ""}`, end: t0 + 1 };
  },
};

// Desktop pages inside a browser window.
function deskPage(p, t0) {
  const rail = `<aside class="rail">${LOGO}<div class="item ${p.kind === "today" ? "" : "active"}">${I.chat}Chat</div><div class="item ${p.kind === "today" ? "active" : ""}">${I.today}Today</div>
    <div class="label">RECENTS</div>${(p.recents || [["pink", "Today's plan"], ["yellow", "Follow-ups"], ["green", "Calls"]]).map(([c, n]) => `<div class="item">${face(c, 22)}${esc(n)}</div>`).join("")}</aside>`;
  if (p.kind === "home") {
    const s = SCREENS.home(p, t0);
    const body = s.html.replace(/<div class="status".*?<\/div>/, "").replace(/<div class="tabbar">[\s\S]*$/, "");
    return { html: `${rail}<div class="main">${body}</div>`, end: s.end };
  }
  if (p.kind === "onboard") {
    const s = SCREENS.onboard({ ...p, mobile: false }, t0);
    return { html: `<div class="main" style="align-items:center;justify-content:center"><div style="width:620px">${LOGO.replace('alt=""', 'alt="" style="width:130px;margin:0 28px 10px"')}${s.html.replace(/<div class="status".*?<\/div>/, "")}</div></div>`, end: s.end };
  }
  if (p.kind === "settings") {
    const s = SCREENS[p.kind]({ ...p, mobile: false }, t0);
    return { html: `${rail}<div class="main" style="padding:10px 40px">${s.html.replace(/<div class="status".*?<\/div>/, "")}</div>`, end: s.end };
  }
  const c = chatParts(p, t0, false);
  return { html: `${rail}<div class="main"><div class="header">${face(p.color, 24)}${esc(p.agent || "Decibyl")}</div>${c.thread}${c.dock}${c.composer}</div>`, end: c.end, tTap: c.tTap, tDock: c.tDock };
}

/* ---------- devices ---------- */
function browserFrame(inner, { x, y, w, h, zoom = 1.25, url = "app.decibyl.ai/chat", id, tin }) {
  return `<div class="browser" id="${id}" style="left:${x}px;top:${y}px;width:${w}px;height:${h}px" data-in="${tin.toFixed(2)}" data-dur="0.6" data-keep="1">
    <div class="chrome"><span class="dot"></span><span class="dot"></span><span class="dot"></span><div class="url">${I.lock}<b>${esc(url.split("/")[0])}</b>/${esc(url.split("/").slice(1).join("/"))}</div></div>
    <div class="app" style="zoom:${zoom};height:${((h - 46) / zoom).toFixed(1)}px">${inner}</div></div>`;
}
function phoneFrame(scr, { x, y, zoom = 1, id, tin }) {
  return `<div class="abs" id="${id}" style="left:${x}px;top:${y}px" data-in="${tin.toFixed(2)}" data-dur="0.7" data-dist="70" data-keep="1">
    <div class="phone" style="position:relative;zoom:${zoom}"><div class="screen ${scr.cls}"><div class="island"></div>${scr.html}<div class="glare"></div></div></div></div>`;
}
function tilt(id, t0, { ry = 0, rx = 4, rz = 0, settle = 1.0, from = 18 } = {}) {
  TOUCH.add(id);
  UP.push((t) => {
    const el = $(id); if (!el) return;
    const s = easeOut(clamp((t - t0) / settle));
    el.style.transform = `${el.dataset.tf || ""} rotateY(${lerp(ry + Math.sign(ry || 1) * from, ry, s) + 1.6 * Math.sin(t * 0.8)}deg) rotateX(${rx + 1.2 * Math.sin(t * 0.7)}deg) rotateZ(${rz}deg)`;
  });
}

/* ---------- scenes ---------- */
const SCENES = {
  // The ask, typed into the real Chat home.
  ask(p, t0, t1) {
    if (V) {
      const s = SCREENS.home(p, t0), id = uid("ph");
      tilt(id, t0, { ry: 0, rx: 3, from: 0 });
      return phoneFrame(s, { x: (W - 372 * 2.0) / 2, y: 150, zoom: 2.0, id, tin: t0 });
    }
    const pg = deskPage({ ...p, kind: "home" }, t0), id = uid("bw");
    TOUCH.add(id);
    UP.push((t) => {
      const el = $(id); const hs = easeOut(clamp((t - t0) / 0.9)), hz = easeInOut(clamp((t - t0 - 0.2) / 1.4));
      el.style.transformOrigin = "50% 58%";
      el.style.transform = `rotateX(${lerp(14, 0, hs)}deg) scale(${lerp(0.94, 1, hs) * lerp(1, 1.14, hz)})`;
    });
    return browserFrame(pg.html, { x: 180, y: 120, w: 1560, h: 840, zoom: 1.4, url: "app.decibyl.ai/overview", id, tin: t0 });
  },
  // A conversation with docked approval and results.
  chat(p, t0, t1) {
    const cap = caption(p.caption, t0 + 0.5, V ? 110 : 44, !V);
    if (V) {
      const s = SCREENS.chat(p, t0), id = uid("ph");
      tilt(id, t0, { ry: -6, rx: 3, from: 10 });
      const ptr = s.tTap ? pointerTap("doit", s.tTap - 0.6, s.tTap, true, t1) : "";
      return cap + phoneFrame(s, { x: (W - 372 * 1.75) / 2, y: p.caption ? 420 : 260, zoom: 1.75, id, tin: t0 + 0.05 }) + ptr;
    }
    const pg = deskPage(p, t0), id = uid("bw");
    TOUCH.add(id);
    UP.push((t) => {
      const el = $(id);
      const cs = easeOut(clamp((t - t0) / 0.9));
      const zin = pg.tDock ? easeInOut(clamp((t - pg.tDock - 0.1) / 0.8)) : 0;
      el.style.transformOrigin = "60% 92%";
      el.style.transform = `${el.dataset.tf || ""} rotateX(${lerp(14, 0, cs)}deg) rotateY(${lerp(-8, 0, cs)}deg) translateY(${lerp(50, 0, cs)}px) scale(${lerp(0.92, 1, cs) * (1 + 0.12 * zin)})`;
    });
    const ptr = pg.tTap ? pointerTap("doit", pg.tTap - 0.9, pg.tTap, false, t1) : "";
    return cap + browserFrame(pg.html, { x: 210, y: p.caption ? 170 : 110, w: 1500, h: p.caption ? 880 : 900, zoom: 1.25, url: p.url || "app.decibyl.ai/chat", id, tin: t0 + 0.05 }) + ptr;
  },
  // One or two phones showing any screens, with optional floating chips.
  phones(p, t0, t1) {
    const cap = caption(p.caption || (V && p.side) || "", t0 + 0.1, V ? 110 : 44);
    if (V && p.side) p = { ...p, caption: p.side, side: null };
    const n = p.phones.length;
    let html = cap;
    const place = V
      ? (n === 1 ? [[(W - 372 * 1.75) / 2, p.caption ? 420 : 260, 1.75]] : [[90, 560, 1.15], [562, 560, 1.15]])
      : p.side ? [[1180, 150, 1.0]]
      : (n === 1 ? [[(W - 372 * 1.05) / 2, 220, 1.05]] : [[540, 236, 1.0], [1008, 236, 1.0]]);
    p.phones.forEach((ph, i) => {
      const s = SCREENS[ph.screen](ph, t0 + 0.2 + i * 0.15), id = uid("ph");
      const [x, y, z] = place[i];
      const ry = n === 1 ? (p.side ? -16 : -8) : (i === 0 ? 14 : -14);
      tilt(id, t0 + 0.2 + i * 0.15, { ry, rx: 4, rz: n === 1 ? (p.side ? 3 : 0) : (i === 0 ? -2 : 2) });
      html += phoneFrame(s, { x, y, zoom: z, id, tin: t0 + 0.2 + i * 0.15 });
      if (ph.tap) html += pointerTap(ph.tap.k, t0 + ph.tap.at - 0.5, t0 + ph.tap.at, true, t1);
    });
    if (p.side && !V) {
      html += `<div class="abs headline kin" data-kin="${(t0 + 0.15).toFixed(2)}" style="left:190px;top:${p.sub ? 330 : 380}px;width:860px;font-size:96px">${p.side.split("|").map((x, i) => i ? `<span class="muted">${esc(x)}</span>` : esc(x)).join(" ")}</div>`;
      if (p.sub) html += `<div class="abs sub" data-in="${(t0 + 0.7).toFixed(2)}" style="left:194px;top:640px;width:760px;font-size:30px">${esc(p.sub)}</div>`;
    }
    (p.chips || []).forEach((c, i) => {
      const ta = t0 + (c.at ?? 1.2 + i * 0.15); ev(ta, "pop", { i });
      const pos = V ? [[70, 1500], [600, 1560], [120, 1700], [620, 1720], [380, 1620]][i % 5] : [[190, 330], [150, 560], [1470, 300], [1500, 540], [1440, 770]][i % 5];
      html += `<div class="abs pill" style="left:${pos[0]}px;top:${pos[1]}px;font-size:${V ? 40 : 36}px;font-weight:500" data-in="${ta.toFixed(2)}" data-mode="pop">${esc(c.text)}</div>`;
    });
    return html;
  },
  // A kinetic headline with floating pastel faces.
  title(p, t0, t1) {
    const faces = p.faces === false ? "" : (V
      ? [[120, 300, "pink", 140], [800, 260, "yellow", 110], [140, 1420, "green", 120], [780, 1480, "pink", 90], [470, 1640, "yellow", 70]]
      : [[250, 180, "pink", 140], [1540, 150, "yellow", 110], [190, 720, "green", 120], [1570, 700, "pink", 100], [900, 860, "yellow", 70]])
      .map(([x, y, c, s], i) => { ev(t0 + 0.1 + i * 0.1, "pop", { i }); return `<div class="abs" style="left:${x}px;top:${y}px" data-in="${(t0 + 0.1 + i * 0.1).toFixed(2)}" data-mode="pop">${face(c, s, "bob")}</div>`; }).join("");
    const [a, b] = p.text.split("|");
    return `${faces}<div class="center" style="padding:0 ${V ? 80 : 160}px">
      ${p.logo ? `<img class="logo-big" src="decibyl-logo.svg" data-in="${(t0 + 0.1).toFixed(2)}" data-dur="0.8" data-mode="grow" style="margin-bottom:46px">` : ""}
      <div class="headline kin" data-kin="${(t0 + 0.3).toFixed(2)}" style="font-size:${p.size || (V ? 104 : 96)}px">${esc(a)}${b ? ` <span class="muted">${esc(b)}</span>` : ""}</div>
      ${p.sub ? `<div class="sub" data-in="${(t0 + 0.9).toFixed(2)}" style="font-size:${V ? 38 : 34}px;margin-top:34px;max-width:1100px">${esc(p.sub)}</div>` : ""}</div>`;
  },
  // A tutorial step: numbered text beside the real screen, a ring on what to touch.
  step(p, t0, t1) {
    const scr = p.ui || {};
    let dev = "", end;
    const id = uid("dv");
    const mobile = V || scr.device === "phone";
    if (mobile) {
      const s = SCREENS[scr.kind === "home" ? "home" : scr.kind](scr, t0 + 0.3);
      const z = V ? 1.55 : 1.0;
      dev = phoneFrame(s, { x: V ? (W - 372 * z) / 2 : 1150, y: V ? 640 : 150, zoom: z, id, tin: t0 + 0.05 });
      tilt(id, t0, { ry: V ? 0 : -10, rx: 3, from: 6 });
      end = s;
    } else {
      const pg = deskPage(scr, t0 + 0.3);
      dev = browserFrame(pg.html, { x: 690, y: 110, w: 1130, h: 860, zoom: 1.1, url: scr.url || "app.decibyl.ai/chat", id, tin: t0 + 0.05 });
      TOUCH.add(id);
      // camera: lean in on what the step points at
      let origin = null;
      UP.push((t) => {
        const el = $(id);
        const fAt = t0 + (p.focusAt ?? 0.9);
        if (p.focus && !origin && t >= fAt - 0.25) {
          const tg = el.querySelector(`[data-k="${p.focus}"]`), br = el.getBoundingClientRect();
          if (tg) { const r = tg.getBoundingClientRect(); origin = [((r.left + r.width / 2 - br.left) / br.width * 100).toFixed(1), ((r.top + r.height / 2 - br.top) / br.height * 100).toFixed(1)]; }
        }
        if (t < fAt - 0.25) origin = null;
        const z = p.focus ? 1 + (p.zoom ?? 0.28) * easeInOut(clamp((t - fAt + 0.2) / 0.9)) : 1;
        if (origin) el.style.transformOrigin = `${origin[0]}% ${origin[1]}%`;
        el.style.transform = `${el.dataset.tf || ""} rotateY(${lerp(-8, -3, easeOut(clamp((t - t0) / 1)))}deg) scale(${z})`;
      });
      end = pg;
    }
    if (end && end.tTap && p.focus === "doit") {
      if (p.tapAt == null) p.tapAt = end.tTap - t0;
      if (p.focusAt == null) p.focusAt = end.tDock - t0 + 0.35;
    }
    // focus ring + callout
    let ring = "";
    if (p.focus) {
      const rid = uid("rg"), cid = uid("co"), tr = t0 + (p.focusAt ?? 0.9);
      ev(tr, "focus");
      UP.push((t) => {
        const r = $(rid), c = $(cid), target = document.querySelector(`#${id} [data-k="${p.focus}"]`);
        if (!r) return;
        const b = target ? target.getBoundingClientRect() : { width: 0 };
        if (!b.width || (p.tapAt != null && t > t0 + p.tapAt + 0.6)) { r.style.opacity = 0; if (c) c.style.opacity = 0; return; }
        const pad = 10;
        const a = easeOutQ(clamp((t - tr) / 0.5)), fadeOut = t > t1 - 0.4 ? 1 - clamp((t - t1 + 0.4) / 0.3) : 1;
        const pulse = 1 + 0.03 * Math.sin((t - tr) * 6) * (t > tr + 0.5 ? 1 : 0);
        Object.assign(r.style, { left: b.left - pad + "px", top: b.top - pad + "px", width: b.width + pad * 2 + "px", height: b.height + pad * 2 + "px",
          opacity: a * fadeOut, transform: `scale(${(1.25 - 0.25 * a) * pulse})` });
        if (c) {
          const below = b.bottom + 24 + 50 < H;
          Object.assign(c.style, { left: Math.min(W - 40 - c.offsetWidth, Math.max(40, b.left + b.width / 2 - c.offsetWidth / 2)) + "px",
            top: (below ? b.bottom + 22 : b.top - 22 - c.offsetHeight) + "px", opacity: clamp((t - tr - 0.2) / 0.3) * fadeOut });
        }
      });
      ring = `<div class="ring" id="${rid}" style="opacity:0"></div>${p.callout ? `<div class="callout" id="${cid}" style="opacity:0">${esc(p.callout)}</div>` : ""}`;
      if (p.tapAt != null) ring += pointerTap(p.focus, t0 + p.tapAt - 0.8, t0 + p.tapAt, mobile, t1);
    }
    const dots = `<div class="dots">${Array.from({ length: p.of }, (_, i) => `<i class="${i < p.n ? "on" : ""}"></i>`).join("")}</div>`;
    const text = V
      ? `<div class="abs" style="left:80px;right:80px;top:120px"><div data-in="${(t0 + 0.1).toFixed(2)}"><span class="stepchip">Step ${p.n} of ${p.of}</span></div>
          <div class="headline kin" data-kin="${(t0 + 0.2).toFixed(2)}" style="font-size:76px;margin-top:28px">${esc(p.title)}</div>
          ${p.sub ? `<div class="sub" data-in="${(t0 + 0.6).toFixed(2)}" style="font-size:34px;margin-top:20px">${esc(p.sub)}</div>` : ""}</div>`
      : `<div class="abs" style="left:0;top:0;bottom:0;width:820px;z-index:19;background:linear-gradient(90deg,#fff 0%,#fff 72%,rgba(255,255,255,0) 100%)"></div>
        <div class="abs" style="left:130px;top:300px;width:560px;z-index:20"><div data-in="${(t0 + 0.1).toFixed(2)}"><span class="stepchip">Step ${p.n} of ${p.of}</span></div>
          <div class="headline kin" data-kin="${(t0 + 0.2).toFixed(2)}" style="font-size:66px;margin-top:28px">${esc(p.title)}</div>
          ${p.sub ? `<div class="sub" data-in="${(t0 + 0.6).toFixed(2)}" style="font-size:28px;margin-top:22px">${esc(p.sub)}</div>` : ""}
          <div data-in="${(t0 + 0.8).toFixed(2)}">${dots}</div></div>`;
    return text + dev + ring;
  },
  outro(p, t0, t1) {
    const row = ["pink", "yellow", "green"].map((c, i) => { ev(t0 + 0.1 + i * 0.12, "pop", { i }); return `<span data-in="${(t0 + 0.1 + i * 0.12).toFixed(2)}" data-mode="pop">${face(c, V ? 96 : 80)}</span>`; }).join("");
    ev(t0 + 0.7, "cta");
    return `<div class="center"><div style="display:flex;gap:30px;margin-bottom:54px">${row}</div>
      <img class="logo-big" src="decibyl-logo.svg" data-in="${(t0 + 0.25).toFixed(2)}" data-dur="0.8" data-mode="grow" style="${V ? "width:640px" : ""}">
      ${p.line ? `<div class="headline" data-in="${(t0 + 0.5).toFixed(2)}" style="font-size:${V ? 56 : 50}px;margin-top:40px;color:#5d5d5d;font-weight:500;max-width:${V ? 900 : 1300}px">${esc(p.line)}</div>` : ""}
      <div class="cta" data-in="${(t0 + 0.7).toFixed(2)}" data-dur="0.5" data-mode="grow">${esc(p.cta || "Join the free early access list")}</div>
      <div class="url-line" data-in="${(t0 + 0.9).toFixed(2)}">${esc(p.url || "app.decibyl.ai")}</div></div>`;
  },
};

/* ---------- assemble ---------- */
const stage = document.createElement("div");
stage.className = "stage"; stage.id = "stage";
stage.style.width = W + "px"; stage.style.height = H + "px";
document.body.style.width = W + "px"; document.body.style.height = H + "px";
let t = 0;
const bounds = [];
let html = "";
SPEC.scenes.forEach((s, i) => {
  const t0 = t, t1 = t + s.dur;
  const last = i === SPEC.scenes.length - 1;
  html += `<section class="scene" data-in="${t0.toFixed(2)}" data-dur="0.01" ${last ? "" : `data-out="${(t1 - 0.32).toFixed(2)}" data-fade="0.3"`}>${SCENES[s.type](s, t0, t1)}</section>`;
  if (!last) ev(t1 - 0.12, "whoosh");
  bounds.push({ type: s.type, t0, t1 });
  t = t1;
});
stage.innerHTML = html;
document.body.appendChild(stage);
const DURATION = t;

// kinetic words
document.querySelectorAll(".kin").forEach((el) => {
  const t0 = +el.dataset.kin; let k = 0;
  const walk = (node) => [...node.childNodes].forEach((c) => {
    if (c.nodeType === 3) {
      const frag = document.createDocumentFragment();
      c.textContent.split(/(\s+)/).forEach((w) => {
        if (!w) return;
        if (/^\s+$/.test(w)) { frag.appendChild(document.createTextNode(" ")); return; }
        const wd = document.createElement("span"); wd.className = "wd";
        const inner = document.createElement("span"); inner.textContent = w;
        inner.dataset.in = (t0 + k++ * 0.055).toFixed(2); inner.dataset.mode = "rise"; inner.dataset.dur = "0.55";
        wd.appendChild(inner); frag.appendChild(wd);
      });
      c.replaceWith(frag);
    } else walk(c);
  });
  walk(el);
});
const timed = [...document.querySelectorAll("[data-in]")];
const blobs = [...document.querySelectorAll("svg.blob")];
const bobs = [...document.querySelectorAll(".bob")];

function seek(t) {
  for (const el of timed) {
    const tin = +el.dataset.in, dur = +(el.dataset.dur || 0.6), dist = +(el.dataset.dist ?? 24);
    const out = el.dataset.out != null ? +el.dataset.out : Infinity, fade = +(el.dataset.fade || 0.45);
    const p = clamp((t - tin) / dur), q = clamp((t - out) / fade), mode = el.dataset.mode;
    let tf, op;
    if (mode === "pop") { tf = `scale(${0.4 + 0.6 * back(p)})`; op = clamp(p * 3); }
    else if (mode === "grow") { const e = easeOut(p); tf = `scale(${0.92 + 0.08 * e})`; op = e; }
    else if (mode === "rise") { const e = easeOutQ(p); tf = `translateY(${(1 - e) * 110}%)`; op = clamp(p * 2.5); }
    else { tf = `translateY(${(1 - easeOut(p)) * dist}px)`; op = easeOut(p); }
    if (q > 0) tf += ` translateY(${-14 * easeIn(q)}px)`;
    el.style.opacity = op * (1 - easeIn(q));
    if (el.dataset.keep) el.dataset.tf = tf; else el.style.transform = tf;
  }
  bobs.forEach((s, i) => { s.style.transform = `translateY(${10 * Math.sin(t * 1.6 + i)}px) rotate(${4 * Math.sin(t * 0.9 + i * 2)}deg)`; });
  blobs.forEach((s, i) => {
    const period = 3.1 + (i % 5) * 0.37, ph = (t + i * 0.53) % period;
    const b = ph < 0.16 ? 1 - Math.sin((ph / 0.16) * Math.PI) * 0.9 : 1;
    s.querySelector(".eyes").setAttribute("transform", `translate(0 ${46 * (1 - b)}) scale(1 ${b})`);
  });
  for (const f of UP) f(t);
  document.querySelectorAll("[data-keep]").forEach((el) => { if (!TOUCH.has(el.id)) el.style.transform = el.dataset.tf || ""; });
}
window.seek = seek;
window.DURATION = DURATION;
window.EVENTS = EVENTS.sort((a, b) => a.t - b.t);
window.BOUNDS = bounds;
window.SIZE = [W, H];
seek(0);
})();
