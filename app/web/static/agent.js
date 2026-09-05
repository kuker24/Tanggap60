/* Tanggap60 Rescue Agent — chat panel, guided pointer, approval, voice bonus.
   Progressive enhancement: core flow works without this file. All text
   inserted via textContent (no raw HTML from server). */
(function () {
  "use strict";
  const fab = document.getElementById("agent-fab");
  const panel = document.getElementById("agent-panel");
  if (!fab || !panel) return;
  const CASE = document.body.getAttribute("data-case-id");
  const WORKSPACE_URL = CASE ? ("/cases/" + CASE + "/workspace") : "";
  const msgs = document.getElementById("agent-messages");
  const quick = document.getElementById("agent-quick");
  const form = document.getElementById("agent-form");
  const input = document.getElementById("agent-input");
  const status = document.getElementById("agent-status");
  const mic = document.getElementById("agent-mic");
  const speakBtn = document.getElementById("agent-speak");
  const toast = document.getElementById("agent-toast");
  const sendBtn = form.querySelector('button[type="submit"]');
  const HIST_KEY = "t60agent:" + CASE;
  const HIST_TTL = 60 * 60 * 1000;
  let pendingAction = null;
  let speakOn = false;
  let greeted = false;
  let inFlight = false;
  const GUIDANCE_OFF = true;
  let fetchCtl = null;
  let gen = 0;
  let savedScroll = 0;

  function showToast(text) {
    toast.textContent = text;
    toast.classList.add("show");
    setTimeout(() => toast.classList.remove("show"), 2600);
  }
  function setStatus(text) { status.textContent = text; }

  /* Privacy-first: Raw user text (including possible passwords/OTP) is NEVER
     persisted in sessionStorage. Only AI responses and technical metadata are preserved. */
  function loadHist() {
    try {
      const raw = sessionStorage.getItem(HIST_KEY);
      if (!raw) return [];
      const data = JSON.parse(raw);
      if (!data.ts || Date.now() - data.ts > HIST_TTL) { sessionStorage.removeItem(HIST_KEY); return []; }
      return (data.items || []).filter(i => i && i.role === "ai");
    } catch (e) { return []; }
  }
  function saveHist(items) {
    try {
      const aiOnly = (items || []).filter(i => i && i.role === "ai").slice(-20);
      sessionStorage.setItem(HIST_KEY, JSON.stringify({ ts: Date.now(), items: aiOnly }));
    } catch (e) {}
  }
  let hist = loadHist();

  function addMsg(role, text, tools, persist = true) {
    const div = document.createElement("div");
    div.className = "agent-msg " + role;
    const p = document.createElement("p");
    p.style.margin = "0";
    p.textContent = text;
    div.appendChild(p);
    if (role === "ai" && tools && tools.length) {
      const det = document.createElement("details");
      det.className = "tech-toggle";
      const sum = document.createElement("summary");
      sum.textContent = "Detail teknis: tool yang dipakai AI";
      det.appendChild(sum);
      const ul = document.createElement("ul");
      ul.className = "tech-body";
      tools.forEach(t => {
        const li = document.createElement("li");
        li.textContent = t.tool + " · " + t.planner + " · " + t.duration_ms + " ms";
        ul.appendChild(li);
      });
      det.appendChild(ul);
      div.appendChild(det);
    }
    msgs.appendChild(div);
    msgs.scrollTop = msgs.scrollHeight;
    if (role === "ai" && persist) {
      hist.push({ role: "ai", text, tools });
      saveHist(hist);
    }
    return div;
  }

  function renderQuick(actions) {
    quick.innerHTML = "";
    (actions || []).slice(0, 3).forEach(label => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = label;
      b.addEventListener("click", () => send(label));
      quick.appendChild(b);
    });
  }

  function currentPage() {
    const parts = location.pathname.split("/").filter(Boolean);
    return parts[parts.length - 1] || "home";
  }

  function setBusy(busy) {
    inFlight = busy;
    if (sendBtn) sendBtn.disabled = busy;
    input.disabled = busy;
    if (mic) mic.disabled = busy;
  }

  async function send(text, opts) {
    if (inFlight) return;
    const viaVoice = !!(opts && opts.voice);
    text = (text || "").trim();
    if (!text && greeted) return;
    if (text) addMsg("user", text, null, false);
    input.value = "";
    setBusy(true);
    setStatus("Membaca kondisi kasus…");
    setHud("Membaca kondisi kasus…", "working");
    renderQuick([]);
    gen += 1;
    const myGen = gen;
    if (fetchCtl) fetchCtl.abort();
    fetchCtl = new AbortController();
    try {
      const res = await fetch(`/api/v1/cases/${CASE}/agent/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: fetchCtl.signal,
        body: JSON.stringify({
          text,
          ui_state: { current_page: currentPage(), pending_action: pendingAction, voice: viaVoice },
        }),
      });
      if (myGen !== gen) return;
      if (!res.ok) throw new Error("http-" + res.status);
      const data = await res.json();
      if (myGen !== gen) return;
      greeted = true;
      pendingAction = data.proposed_action || null;
      setStatus("Siap membantu");
      addMsg("ai", data.message, data.tools_used, true);
      renderQuick(data.quick_actions);
      if (data.proposed_action) {
        renderProposal(data.proposed_action);
        emit("t60:proposal-ready", { action_type: data.proposed_action.action_type });
      }
      if (data.rollback_drafts) rollbackDrafts();
      if (data.draft_committed) { rollbackDrafts(); showToast("Tersimpan."); }
      if (data.stop_agent) { stopAll(); return; }
      if (data.pause_agent) {
        RT.pause();
        setHud("⏸ Panduan dijeda", "waiting");
        announce("Panduan dijeda. Ucapkan Lanjut untuk melanjutkan.");
        renderQuick(["Lanjut"]);
        return;
      }
      if (data.open_url) {
        setHud(null);
        window.open(data.open_url, "_blank", "noopener");
        return;
      }
      if (!GUIDANCE_OFF && data.guidance_plan && data.guidance_plan.length) {
        setHud("Menyiapkan panduan…", "working");
        if (!RT.run(data.guidance_plan)) setHud(null);
      } else if (!GUIDANCE_OFF && data.guidance) {
        setHud(null);
        guide(data.guidance);
      } else {
        setHud(null);
      }
      if (speakOn) speak(data.voice_note || data.message);
    } catch (e) {
      setStatus("Siap membantu");
      setHud(null);
      addMsg("ai", "Pendamping AI sedang tidak tersedia. Anda tetap bisa melanjutkan secara manual.", null, false);
    } finally {
      setBusy(false);
      setTimeout(() => input.focus(), 50);
    }
  }

  function renderProposal(prop) {
    const card = document.createElement("div");
    card.className = "agent-proposal";
    const title = document.createElement("strong");
    title.textContent = "Tanggap60 AI ingin menyimpan:";
    card.appendChild(title);
    const dl = document.createElement("dl");
    Object.entries(prop.summary || {}).forEach(([k, v]) => {
      const dt = document.createElement("dt");
      dt.textContent = k;
      const dd = document.createElement("dd");
      dd.textContent = String(v);
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
    card.appendChild(dl);
    const row = document.createElement("div");
    row.className = "row";
    const yes = document.createElement("button");
    yes.type = "button"; yes.className = "btn ember"; yes.textContent = "Simpan";
    const no = document.createElement("button");
    no.type = "button"; no.className = "btn-text"; no.textContent = "Batal";
    yes.addEventListener("click", () => decide(prop, true, card, yes));
    no.addEventListener("click", () => decide(prop, false, card, no));
    row.appendChild(yes); row.appendChild(no);
    card.appendChild(row);
    msgs.appendChild(card);
    msgs.scrollTop = msgs.scrollHeight;
  }

  async function decide(prop, ok, card, btn) {
    btn.disabled = true;
    const verb = ok ? "approve" : "deny";
    try {
      const res = await fetch(`/api/v1/cases/${CASE}/agent/actions/${prop.action_id}/${verb}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action_type: prop.action_type,
          payload: prop.payload,
          expected_version: prop.expected_version,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 409) {
        addMsg("ai", data.message || "Data kasus sudah berubah. Saya perbarui dulu sebelum melanjutkan.");
        pendingAction = null;
        card.remove();
        return;
      }
      if (!res.ok) throw new Error("http-" + res.status);
      pendingAction = null;
      rollbackDrafts();
      card.remove();
      emit(ok ? "t60:proposal-approved" : "t60:proposal-denied", { action_type: prop.action_type });
      if (data.url) {
        addMsg("ai", data.message || "Silakan buka sendiri portal resminya.");
        window.open(data.url, "_blank", "noopener");
        return;
      }
      addMsg("ai", data.message || "Tersimpan.");
      setTimeout(() => location.reload(), 700);
    } catch (e) {
      btn.disabled = false;
      showToast("Gagal menyimpan. Coba lagi.");
    }
  }

  /* --- Live Rescue Mode: GuidanceRuntime (visual execution layer) ---
     Server mengirim guidance_plan (langkah tervalidasi). Runtime mengeksekusi
     satu demi satu: STATUS → NAVIGATE → SCROLL → SPOTLIGHT → POINTER →
     CALLOUT → WAIT. Tanpa JS/selector/URL arbitrer dari server. */
  const PLAN_KEY = "t60plan:" + CASE;
  const WAIT_KEY = "t60wait:" + CASE;
  const AUTO_KEY = "t60autopilot";
  const PLAN_TTL = 10 * 60 * 1000;
  const ROUTES = { intake: 1, processing: 1, review: 1, readiness: 1, result: 1, approval: 1, artifacts: 1, receipt: 1, workspace: 1 };
  const TARGET_STEP = { SCROLL_TO: 1, SPOTLIGHT: 1, MOVE_POINTER: 1, CALLOUT: 1, OPEN_DISCLOSURE: 1, FOCUS: 1 };
  const reduceMotion = () => window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  let hudEl = null, hudText = null, hudTimer = 0;
  function ensureHud() {
    if (hudEl) return;
    hudEl = document.createElement("div");
    hudEl.id = "agent-hud";
    hudEl.hidden = true;
    const spark = document.createElement("span");
    spark.className = "hud-spark";
    spark.setAttribute("aria-hidden", "true");
    spark.textContent = "✦";
    hudText = document.createElement("span");
    hudText.className = "hud-text";
    hudEl.appendChild(spark);
    hudEl.appendChild(hudText);
    const stop = document.createElement("button");
    stop.id = "hud-stop";
    stop.type = "button";
    stop.textContent = "Hentikan AI";
    stop.setAttribute("aria-label", "Hentikan panduan AI dan batalkan drafnya");
    stop.addEventListener("click", stopAll);
    hudEl.appendChild(stop);
    document.body.appendChild(hudEl);
  }
  function setHud(text, mode) {
    if (GUIDANCE_OFF) return;
    ensureHud();
    if (hudTimer) { clearTimeout(hudTimer); hudTimer = 0; }
    if (!text) { hudEl.hidden = true; return; }
    hudText.textContent = text;
    hudEl.dataset.mode = mode || "working";
    hudEl.hidden = false;
  }
  function hudDone(text) {
    setHud(text, "done");
    hudTimer = setTimeout(() => { if (hudEl) hudEl.hidden = true; }, 4000);
  }
  let liveEl = null;
  function announce(text) {
    if (!liveEl) {
      liveEl = document.createElement("div");
      liveEl.id = "agent-guide-live";
      liveEl.className = "sr";
      liveEl.setAttribute("role", "status");
      liveEl.setAttribute("aria-live", "polite");
      document.body.appendChild(liveEl);
    }
    liveEl.textContent = "";
    setTimeout(() => { liveEl.textContent = text; }, 30);
  }

  let layerEl = null;
  function ensureLayer() {
    if (layerEl) return layerEl;
    layerEl = document.createElement("div");
    layerEl.id = "agent-guide-layer";
    layerEl.setAttribute("aria-hidden", "true");
    document.body.appendChild(layerEl);
    return layerEl;
  }
  let cursorEl = null, cursorX = 0, cursorY = 0, cursorInit = false;
  function ensureCursor() {
    ensureLayer();
    if (cursorEl) return cursorEl;
    cursorEl = document.createElement("div");
    cursorEl.id = "agent-cursor";
    cursorEl.hidden = true;
    const dot = document.createElement("span");
    dot.className = "cur-dot";
    dot.textContent = "✦";
    const tag = document.createElement("span");
    tag.className = "cur-tag";
    tag.textContent = "Tanggap60";
    cursorEl.appendChild(dot);
    cursorEl.appendChild(tag);
    layerEl.appendChild(cursorEl);
    return cursorEl;
  }
  function afterScrollSettled(fn) {
    if (reduceMotion()) { setTimeout(fn, 60); return; }
    let lastY = window.scrollY, steady = 0, waited = 0;
    const timer = setInterval(() => {
      waited += 120;
      if (window.scrollY === lastY) steady++;
      else { steady = 0; lastY = window.scrollY; }
      if (steady >= 2 || waited >= 2500) { clearInterval(timer); fn(); }
    }, 120);
  }
  function movePointerTo(el, done) {
    const cur = ensureCursor();
    const r = el.getBoundingClientRect();
    const tx = Math.max(8, Math.min(window.innerWidth - 8, r.left + 20));
    const ty = Math.max(8, Math.min(window.innerHeight - 8, r.top + Math.min(r.height, 44) / 2));
    if (!cursorInit) { cursorX = tx; cursorY = Math.max(0, ty - 160); cursorInit = true; }
    cur.hidden = false;
    const dx = tx - cursorX, dy = ty - cursorY;
    const dist = Math.hypot(dx, dy);
    const dur = reduceMotion() ? 0 : Math.max(300, Math.min(700, 300 + dist * 0.4));
    cursorX = tx; cursorY = ty;
    announce("Tanggap60 menunjuk bagian yang perlu diperhatikan.");
    if (!dur) {
      cur.style.transform = "translate(" + tx + "px," + ty + "px)";
      setTimeout(done, 60);
      return;
    }
    const anim = cur.animate(
      [{ transform: cur.style.transform || "translate(" + (tx - dx) + "px," + (ty - dy) + "px)" },
       { transform: "translate(" + tx + "px," + ty + "px)" }],
      { duration: dur, easing: "cubic-bezier(.3,.7,.3,1)" }
    );
    let finished = false;
    const finish = () => {
      if (finished) return;
      finished = true;
      cur.style.transform = "translate(" + tx + "px," + ty + "px)";
      done();
    };
    anim.onfinish = finish;
    setTimeout(finish, dur + 120);
  }
  function hideCursor() { if (cursorEl) cursorEl.hidden = true; }

  let spotRects = [], spotTarget = null, spotRaf = 0;
  function paintSpotlight() {
    spotRaf = 0;
    if (!spotTarget || !spotRects.length) return;
    const r = spotTarget.getBoundingClientRect();
    const pad = 8, vw = window.innerWidth, vh = window.innerHeight;
    const x0 = Math.max(0, r.left - pad), y0 = Math.max(0, r.top - pad);
    const x1 = Math.min(vw, r.right + pad), y1 = Math.min(vh, r.bottom + pad);
    const pos = [
      [0, 0, vw, y0], [0, y1, vw, vh - y1], [0, y0, x0, y1 - y0], [x1, y0, vw - x1, y1 - y0],
    ];
    spotRects.forEach((d, i) => {
      d.style.left = pos[i][0] + "px"; d.style.top = pos[i][1] + "px";
      d.style.width = Math.max(0, pos[i][2]) + "px"; d.style.height = Math.max(0, pos[i][3]) + "px";
    });
  }
  function dockForGuide() {
    document.body.classList.add("agent-docked");
    if (panel) panel.classList.add("is-mini");
  }
  function undockGuide() {
    document.body.classList.remove("agent-docked");
    if (panel) panel.classList.remove("is-mini");
  }
  function showSpotlight(el) {
    ensureLayer();
    dockForGuide();
    hideSpotlight();
    spotTarget = el;
    for (let i = 0; i < 4; i++) {
      const d = document.createElement("div");
      d.className = "spot-rect";
      layerEl.appendChild(d);
      spotRects.push(d);
    }
    paintSpotlight();
  }
  function hideSpotlight() {
    spotRects.forEach(d => d.remove());
    spotRects = []; spotTarget = null;
  }
  function scheduleRepaint() {
    if (spotRaf || (!spotTarget && !calloutEl)) return;
    spotRaf = requestAnimationFrame(() => { paintSpotlight(); placeCalloutAgain(); });
  }
  window.addEventListener("scroll", scheduleRepaint, { passive: true, capture: true });
  window.addEventListener("resize", scheduleRepaint);

  let calloutEl = null, calloutTarget = null;
  function placeCalloutAgain() {
    if (!calloutEl || !calloutTarget) return;
    positionCallout(calloutEl, calloutTarget);
  }
  function positionCallout(card, el) {
    const r = el.getBoundingClientRect();
    const cw = Math.min(260, window.innerWidth - 16), ch = card.offsetHeight || 140;
    let left = Math.max(8, Math.min(window.innerWidth - cw - 8, r.left + window.scrollX - window.scrollX));
    left = Math.max(8, Math.min(window.innerWidth - cw - 8, r.left));
    let top = r.bottom + 10;
    if (top + ch > window.innerHeight - 8) top = r.top - ch - 10;
    if (top < 8) top = Math.min(window.innerHeight - ch - 8, r.bottom + 10);
    if (top < 8) top = 8;
    card.style.left = left + "px";
    card.style.top = top + "px";
  }
  function showCallout(el, title, message) {
    ensureLayer();
    dockForGuide();
    hideCallout();
    calloutTarget = el;
    calloutEl = document.createElement("div");
    calloutEl.id = "agent-callout";
    calloutEl.setAttribute("role", "dialog");
    calloutEl.setAttribute("aria-label", title || "Panduan Tanggap60");
    const head = document.createElement("div");
    head.className = "co-head";
    head.textContent = "✦ Tanggap60";
    const h = document.createElement("strong");
    h.className = "co-title";
    h.textContent = title || "Perhatikan bagian ini";
    const p = document.createElement("p");
    p.className = "co-msg";
    p.textContent = message;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "co-ok";
    btn.textContent = "Mengerti";
    btn.addEventListener("click", () => { hideCallout(); hideSpotlight(); hideCursor(); undockGuide(); });
    calloutEl.appendChild(head);
    calloutEl.appendChild(h);
    calloutEl.appendChild(p);
    calloutEl.appendChild(btn);
    layerEl.appendChild(calloutEl);
    positionCallout(calloutEl, el);
    announce((title ? title + ". " : "") + message);
  }
  function hideCallout() {
    if (calloutEl) { calloutEl.remove(); calloutEl = null; calloutTarget = null; }
  }
  function clearVisuals() {
    hideCallout(); hideSpotlight(); hideCursor();
    undockGuide();
    if (spotRaf) { cancelAnimationFrame(spotRaf); spotRaf = 0; }
  }

  /* --- Native Action Mode: semantic action bus (§16-§18) ---
     Handler berasal dari kode kita sendiri. Tidak ada eval/Function/
     selector arbitrer: server hanya mengirim action+target+value,
     frontend me-resolve lewat registry ini (data-guide-id yang ada). */
  function emit(name, detail) {
    try { document.dispatchEvent(new CustomEvent(name, { detail: detail || {} })); } catch (e) {}
  }
  const UID_RE = /^ru_[0-9a-f]{12}$/;
  const FID_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;
  function txCard(uid) {
    if (!UID_RE.test(uid || "")) return null;
    return document.querySelector('[data-guide-alt="transaction-' + uid + '"]')
      || document.querySelector('[data-guide-id="transaction-' + uid + '"]');
  }
  const aiDrafts = [];
  const Bus = {
    handlers: {},
    register(name, fn) { this.handlers[name] = fn; },
    execute(step) {
      const fn = this.handlers[step.type];
      emit("t60:action-started", { action: step.type });
      if (!fn) {
        emit("t60:action-failed", { action: step.type, reason: "UNKNOWN_ACTION" });
        return { action: step.type, status: "DENIED", changed: false, requires_human: false, message: "", reason: "UNKNOWN_ACTION" };
      }
      try {
        const out = fn(step) || {};
        const res = {
          action: step.type,
          status: out.status || "COMPLETED",
          changed: !!out.changed,
          requires_human: !!out.requires_human,
          message: out.message || "",
        };
        if (out.reason) res.reason = out.reason;
        emit(res.status === "COMPLETED" || res.status === "WAITING_APPROVAL" ? "t60:action-completed" : "t60:action-failed", res);
        return res;
      } catch (e) {
        emit("t60:action-failed", { action: step.type, reason: "EXEC_ERROR" });
        return { action: step.type, status: "DENIED", changed: false, requires_human: false, message: "", reason: "EXEC_ERROR" };
      }
    },
  };
  Bus.register("OPEN_TRANSACTION", (s) => {
    const m = /^transaction-(ru_[0-9a-f]{12})$/.exec(s.target || "");
    const el = m && txCard(m[1]);
    if (!el) return { status: "DENIED", reason: "NOT_FOUND" };
    el.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
    announce("Tanggap60 membuka transaksi.");
    return { status: "COMPLETED", message: "Transaksi dibuka." };
  });
  Bus.register("OPEN_EVIDENCE", (s) => {
    const el = findGuideTarget(s.target);
    if (!el) return { status: "DENIED", reason: "NOT_FOUND" };
    el.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
    announce("Tanggap60 membuka bukti.");
    return { status: "COMPLETED", message: "Bukti dibuka." };
  });
  Bus.register("OPEN_WORKSPACE_VIEW", (s) => {
    const el = findGuideTarget(s.target || "workspace-open");
    if (!el) return { status: "DENIED", reason: "NOT_FOUND" };
    el.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
    announce("Tanggap60 membuka ruang persiapan.");
    return { status: "COMPLETED", message: "Ruang persiapan dibuka." };
  });
  Bus.register("FOCUS_FIELD", (s) => {
    const el = findGuideTarget(s.target);
    if (!el) return { status: "DENIED", reason: "NOT_FOUND" };
    const focusable = (el.matches && el.matches("input,select,textarea,button")) ? el
      : el.querySelector("input,select,textarea,button");
    const t = focusable || el;
    try {
      if (!t.hasAttribute("tabindex") && !/^(INPUT|SELECT|TEXTAREA|BUTTON)$/.test(t.tagName)) t.setAttribute("tabindex", "-1");
      t.focus({ preventScroll: true });
    } catch (e) {}
    t.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
    announce("Tanggap60 memfokuskan bagian yang perlu diisi.");
    return { status: "COMPLETED", message: "Fokus dipindahkan." };
  });
  Bus.register("SET_DRAFT", (s) => {
    // PREPARE (§7, §22): ubah draf UI lokal + tandai jelas; tanpa commit server.
    if (!UID_RE.test(s.unit || "") || !FID_RE.test(s.fact_id || "") || !s.label) {
      return { status: "DENIED", reason: "BAD_TARGET" };
    }
    const scope = txCard(s.unit);
    if (!scope) return { status: "DENIED", reason: "NOT_FOUND" };
    const radio = scope.querySelector('input[type="radio"][value="' + s.fact_id + '"]');
    const select = !radio && scope.querySelector("select");
    let anchor = null;
    if (radio) {
      const group = Array.prototype.slice.call(
        scope.querySelectorAll('input[type="radio"][name="' + radio.name + '"]'));
      const prev = group.filter(r => r.checked).map(r => r.value);
      group.forEach(r => { r.checked = (r === radio); });
      radio.dispatchEvent(new Event("change", { bubbles: true }));
      anchor = radio.closest("fieldset") || radio.closest(".pair-card") || scope;
      aiDrafts.push({ kind: "radio", group, prev });
    } else if (select && select.querySelector('option[value="' + s.fact_id + '"]')) {
      const prev = select.value;
      select.value = s.fact_id;
      select.dispatchEvent(new Event("change", { bubbles: true }));
      anchor = select.closest(".pair-card") || select.closest("fieldset") || scope;
      aiDrafts.push({ kind: "select", el: select, prev });
    } else {
      return { status: "DENIED", reason: "FIELD_NOT_FOUND" };
    }
    if (anchor && !anchor.querySelector(":scope > .ai-draft")) {
      const badge = document.createElement("p");
      badge.className = "ai-draft";
      badge.setAttribute("role", "status");
      badge.textContent = "✦ Disiapkan AI — " + s.label + ". Belum disimpan.";
      anchor.appendChild(badge);
      if (aiDrafts.length) aiDrafts[aiDrafts.length - 1].badge = badge;
    }
    emit("t60:draft-updated", { unit: s.unit, field: s.field, label: s.label });
    announce("Tanggap60 menyiapkan " + s.label + ". Belum disimpan.");
    return { status: "WAITING_APPROVAL", changed: true, requires_human: true, message: "Draf disiapkan." };
  });
  function rollbackDrafts() {
    aiDrafts.splice(0).forEach(d => {
      try {
        if (d.kind === "radio") {
          d.group.forEach(r => { r.checked = d.prev.indexOf(r.value) !== -1; });
        } else if (d.kind === "select") {
          d.el.value = d.prev;
        }
        if (d.badge) d.badge.remove();
      } catch (e) {}
    });
    try {
      Array.prototype.forEach.call(document.querySelectorAll(".ai-draft"), b => b.remove());
    } catch (e) {}
    emit("t60:draft-updated", { rolled_back: true });
  }
  function stopAll() {
    gen += 1;
    if (fetchCtl) fetchCtl.abort();
    RT.cancel(false);
    rollbackDrafts();
    try {
      localStorage.setItem(AUTO_KEY, "0");
      const box = document.getElementById("agent-autopilot");
      if (box) box.checked = false;
    } catch (e) {}
    showToast("Panduan AI dihentikan. Draf AI dibatalkan.");
  }

  function minimizePanel() {
    if (!panel.hidden) {
      panel.hidden = true;
      fab.setAttribute("aria-expanded", "false");
      document.body.classList.remove("agent-open");
      showToast("Lihat yang ditunjukkan — ketuk Bantu saya untuk kembali.");
    }
  }
  function saveContinuation(steps) {
    try {
      // Hanya metadata aman: nama target/route + pesan server. Tanpa teks user/PII.
      sessionStorage.setItem(PLAN_KEY, JSON.stringify({ ts: Date.now(), steps }));
    } catch (e) {}
  }
  function loadContinuation() {
    try {
      const raw = sessionStorage.getItem(PLAN_KEY);
      if (!raw) return null;
      const data = JSON.parse(raw);
      if (!data || !Array.isArray(data.steps) || !data.steps.length) return null;
      if (Date.now() - data.ts > PLAN_TTL) { sessionStorage.removeItem(PLAN_KEY); return null; }
      return data.steps;
    } catch (e) { return null; }
  }
  function clearContinuation() { try { sessionStorage.removeItem(PLAN_KEY); } catch (e) {} }
  function markWaiting() { try { sessionStorage.setItem(WAIT_KEY, JSON.stringify({ ts: Date.now() })); } catch (e) {} }
  function takeWaiting() {
    try {
      const raw = sessionStorage.getItem(WAIT_KEY);
      sessionStorage.removeItem(WAIT_KEY);
      if (!raw) return false;
      return Date.now() - JSON.parse(raw).ts < PLAN_TTL;
    } catch (e) { return false; }
  }
  function clearWaiting() { try { sessionStorage.removeItem(WAIT_KEY); } catch (e) {} }

  const RT = {
    plan: [], idx: 0, busy: false, paused: false, stepTimer: 0,
    run(plan, fromIdx) {
      this.cancel(true);
      const steps = (plan || []).filter(validStep);
      if (!steps.length) return false;
      this.plan = steps;
      this.idx = fromIdx || 0;
      this.busy = true;
      this.paused = false;
      minimizePanel();
      this.next();
      return true;
    },
    next() {
      if (!this.busy || this.paused) return;
      if (this.idx >= this.plan.length) { this.finish(); return; }
      const s = this.plan[this.idx++];
      try { this.exec(s); } catch (e) { this.next(); }
    },
    later(fn, ms) {
      if (this.stepTimer) clearTimeout(this.stepTimer);
      this.stepTimer = setTimeout(() => { this.stepTimer = 0; this.next(); }, ms);
    },
    exec(s) {
      switch (s.type) {
        case "STATUS":
          setHud(s.message, "working");
          announce(s.message);
          this.later(null, reduceMotion() ? 150 : 900);
          break;
        case "NAVIGATE_INTERNAL": {
          saveContinuation(this.plan.slice(this.idx));
          setHud("Membuka halaman " + s.route + "…", "working");
          location.href = "/cases/" + CASE + "/" + s.route;
          break;
        }
        case "SCROLL_TO": {
          const el = findGuideTarget(s.target);
          if (!el) { this.next(); break; }
          el.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
          this.later(null, reduceMotion() ? 150 : 650);
          break;
        }
        case "SPOTLIGHT": {
          const el = findGuideTarget(s.target);
          if (!el) { this.next(); break; }
          el.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
          setTimeout(() => { if (this.busy) showSpotlight(el); }, reduceMotion() ? 50 : 500);
          this.later(null, reduceMotion() ? 200 : 700);
          break;
        }
        case "MOVE_POINTER": {
          const el = findGuideTarget(s.target);
          if (!el) { this.next(); break; }
          dockForGuide();
          el.scrollIntoView({ behavior: reduceMotion() ? "auto" : "smooth", block: "center" });
          // Ukur posisi kursor SETELAH scroll berhenti — kalau tidak,
          // kursor mendarat di koordinat basi (race scroll-vs-pointer).
          afterScrollSettled(() => {
            if (!this.busy) return;
            movePointerTo(el, () => this.next());
          });
          break;
        }
        case "CALLOUT": {
          const el = findGuideTarget(s.target);
          if (!el) { this.next(); break; }
          showCallout(el, s.title, s.message);
          this.later(null, reduceMotion() ? 200 : 600);
          break;
        }
        case "OPEN_DISCLOSURE": {
          const el = findGuideTarget(s.target);
          if (el) {
            const det = el.closest ? (el.closest("details") || el.querySelector("details")) : null;
            if (det) det.open = true;
          }
          this.next();
          break;
        }
        case "FOCUS": {
          const el = findGuideTarget(s.target);
          if (el && el.focus) {
            try {
              if (!el.hasAttribute("tabindex")) el.setAttribute("tabindex", "-1");
              el.focus({ preventScroll: true });
            } catch (e) {}
          }
          this.next();
          break;
        }
        case "OPEN_TRANSACTION":
        case "OPEN_EVIDENCE":
        case "OPEN_WORKSPACE_VIEW": {
          setHud(s.type === "OPEN_TRANSACTION" ? "Membuka transaksi…" : "Membuka…", "working");
          const res = Bus.execute(s);
          if (res.status === "DENIED") { this.next(); break; }
          this.later(null, reduceMotion() ? 200 : 800);
          break;
        }
        case "FOCUS_FIELD": {
          setHud("Memfokuskan…", "working");
          Bus.execute(s);
          this.later(null, reduceMotion() ? 200 : 600);
          break;
        }
        case "SET_DRAFT": {
          setHud("Menyiapkan pilihan…", "working");
          const res = Bus.execute(s);
          if (res.status === "WAITING_APPROVAL") {
            setHud("Menunggu persetujuan Anda", "waiting");
          }
          this.later(null, reduceMotion() ? 200 : 800);
          break;
        }
        case "WAIT_FOR_USER":
          this.paused = true;
          setHud("Menunggu konfirmasi Anda", "waiting");
          announce("Menunggu konfirmasi Anda. " + (this.plan.length ? "" : ""));
          markWaiting();
          break;
        case "CLEAR_GUIDANCE":
        default:
          this.next();
          break;
      }
    },
    pause() { this.paused = true; },
    resume() { if (this.busy && this.paused) { this.paused = false; this.next(); } },
    cancel(silent) {
      if (this.stepTimer) { clearTimeout(this.stepTimer); this.stepTimer = 0; }
      this.busy = false; this.paused = false; this.plan = []; this.idx = 0;
      clearVisuals();
      clearContinuation(); clearWaiting();
      if (!silent) { setHud(null); announce("Panduan ditutup."); }
    },
    finish() {
      this.busy = false; this.paused = false;
      clearContinuation(); clearWaiting();
      clearVisuals();
      hudDone("✓ Panduan selesai");
      announce("Panduan selesai.");
    },
  };
  // Validasi bentuk saja (bukan resolvabilitas DOM): langkah lintas-halaman
  // harus lolos filter di halaman asal dan di-resolve saat eksekusi.
  // Server sudah memvalidasi target/route; exec melewati langkah yang
  // targetnya tidak ada (fail-open aman karena visual hanya kosmetik).
  const TARGET_RE = /^[A-Za-z0-9][A-Za-z0-9_-]{0,79}$/;
  function validStep(s) {
    if (!s || typeof s !== "object") return false;
    const t = s.type;
    if (t === "NAVIGATE_INTERNAL") return !!ROUTES[String(s.route || "")];
    if (t === "STATUS") return typeof s.message === "string" && !!s.message;
    if (t === "WAIT_FOR_USER" || t === "CLEAR_GUIDANCE") return true;
    if (t === "CALLOUT") {
      return TARGET_RE.test(String(s.target || "")) && typeof s.message === "string" && !!s.message;
    }
    if (t === "OPEN_TRANSACTION" || t === "FOCUS_FIELD" || t === "OPEN_EVIDENCE" || t === "OPEN_WORKSPACE_VIEW") {
      return TARGET_RE.test(String(s.target || ""));
    }
    if (t === "SET_DRAFT") {
      return TARGET_RE.test(String(s.unit || "")) && typeof s.fact_id === "string" && !!s.fact_id
        && typeof s.label === "string" && !!s.label;
    }
    if (TARGET_STEP[t]) return TARGET_RE.test(String(s.target || ""));
    return false;
  }
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape" && RT.busy) RT.cancel(false);
  });

  async function silentFollowUp(text) {
    if (GUIDANCE_OFF) return;
    if (inFlight) return;
    inFlight = true;
    setHud("Membaca kondisi kasus…", "working");
    gen += 1;
    const myGen = gen;
    if (fetchCtl) fetchCtl.abort();
    fetchCtl = new AbortController();
    try {
      const res = await fetch(`/api/v1/cases/${CASE}/agent/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        signal: fetchCtl.signal,
        body: JSON.stringify({ text, ui_state: { current_page: currentPage(), pending_action: pendingAction } }),
      });
      if (myGen !== gen) return;
      if (!res.ok) throw new Error("http-" + res.status);
      const data = await res.json();
      if (myGen !== gen) return;
      greeted = true;
      pendingAction = data.proposed_action || null;
      setStatus("Siap membantu");
      addMsg("ai", data.message, data.tools_used, true);
      renderQuick(data.quick_actions);
      if (data.proposed_action) renderProposal(data.proposed_action);
      if (!GUIDANCE_OFF && data.guidance_plan && data.guidance_plan.length) {
        if (!RT.run(data.guidance_plan)) setHud(null);
      } else if (!GUIDANCE_OFF && data.guidance) {
        guide(data.guidance);
        setHud(null);
      } else {
        setHud(null);
      }
    } catch (e) {
      setHud(null);
    } finally {
      inFlight = false;
    }
  }

  /* Lanjutan otomatis: sambung plan lintas halaman / lanjut setelah aksi user. */
  (function autoResume() {
    if (GUIDANCE_OFF) return;
    const cont = loadContinuation();
    if (cont) { clearContinuation(); setTimeout(() => RT.run(cont, 0), 600); return; }
    if (takeWaiting()) { setTimeout(() => silentFollowUp("Lanjut."), 600); return; }
    try {
      if (localStorage.getItem(AUTO_KEY) === "1") setTimeout(() => silentFollowUp("Bantu saya sampai selesai."), 900);
    } catch (e) {}
  })();

  /* --- Guided pointer legacy (one-shot): dipakai bila tanpa guidance_plan --- */
  let tipEl = null, ringEl = null, ringTimer = 0;
  function clearGuide() {
    if (tipEl) { tipEl.remove(); tipEl = null; }
    if (ringEl) { ringEl.classList.remove("guide-ring"); ringEl = null; }
    if (ringTimer) { clearTimeout(ringTimer); ringTimer = 0; }
  }
  function findGuideTarget(target) {
    let el = document.querySelector('[data-guide-id="' + target + '"]');
    if (el) return el;
    el = document.querySelector('[data-guide-alt="' + target + '"]');
    if (el) return el;
    // fallback: transaction-<uid>-<field> -> transaction-<uid>
    const m = /^transaction-(ru_[0-9a-f]{12})-(amount|destination|datetime)$/.exec(target);
    if (m) {
      el = document.querySelector('[data-guide-id="transaction-' + m[1] + '"]')
        || document.querySelector('[data-guide-alt="transaction-' + m[1] + '"]');
    }
    return el;
  }
  function guide(g) {
    clearGuide();
    const el = findGuideTarget(g.target);
    if (!el) {
      if (g.target === "workspace-open" && WORKSPACE_URL) { location.href = WORKSPACE_URL; return; }
      showToast("Buka halaman yang relevan dulu, lalu tanya lagi.");
      return;
    }
    const reduce = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    dockForGuide();
    el.scrollIntoView({ behavior: reduce ? "auto" : "smooth", block: "center" });
    el.classList.add("guide-ring");
    ringEl = el;
    const placeTip = () => {
      if (!ringEl) return;
      if (tipEl) tipEl.remove();
      tipEl = document.createElement("div");
      tipEl.className = "guide-tip";
      tipEl.textContent = g.label;
      document.body.appendChild(tipEl);
      const r = ringEl.getBoundingClientRect();
      tipEl.style.left = Math.max(8, Math.min(window.innerWidth - 250, r.left + window.scrollX)) + "px";
      tipEl.style.top = (r.bottom + window.scrollY + 8) + "px";
    };
    placeTip();
    if (!reduce) setTimeout(placeTip, 600);
    const label = document.createElement("span");
    label.className = "sr";
    label.setAttribute("role", "status");
    label.textContent = g.label;
    el.appendChild(label);
    setTimeout(() => label.remove(), 6000);
    ringTimer = setTimeout(clearGuide, 9000);
  }

  if (mic) mic.hidden = true;
  function speak(text) {
    try {
      if (!("speechSynthesis" in window)) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "id-ID";
      window.speechSynthesis.speak(u);
    } catch (e) {}
  }
  if (speakBtn) speakBtn.addEventListener("click", () => {
    speakOn = !speakOn;
    speakBtn.setAttribute("aria-pressed", speakOn ? "true" : "false");
    speakBtn.textContent = speakOn ? "🔊" : "🔈";
    if (!speakOn && "speechSynthesis" in window) { try { window.speechSynthesis.cancel(); } catch (e) {} }
  });

  /* --- Panduan langsung toggle (default OFF) --- */
  (function autopilot() {
    const box = document.getElementById("agent-autopilot");
    if (!box) return;
    try { box.checked = localStorage.getItem(AUTO_KEY) === "1"; } catch (e) {}
    box.addEventListener("change", () => {
      try { localStorage.setItem(AUTO_KEY, box.checked ? "1" : "0"); } catch (e) {}
      showToast(box.checked ? "Panduan langsung aktif. Saya tunjukkan langkahnya." : "Panduan langsung mati.");
      if (box.checked) silentFollowUp("Bantu saya sampai selesai.");
      else RT.cancel(false);
    });
  })();

  /* --- Panel wiring --- */
  function open() {
    savedScroll = window.scrollY;
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    document.body.classList.add("agent-open");
    if (msgs.children.length === 0 && hist.length > 0) {
      hist.forEach(h => {
        if (h && h.role === "ai") addMsg("ai", h.text, h.tools, false);
      });
    }
    if (!greeted && msgs.children.length === 0) send("");
  }
  function close() {
    panel.hidden = true;
    fab.setAttribute("aria-expanded", "false");
    document.body.classList.remove("agent-open");
    window.scrollTo(0, savedScroll);
    fab.focus();
  }
  fab.addEventListener("click", () => (panel.hidden ? open() : close()));
  document.getElementById("agent-close").addEventListener("click", close);
  form.addEventListener("submit", (ev) => { ev.preventDefault(); send(input.value); });
})();
