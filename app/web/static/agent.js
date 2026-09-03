/* Tanggap60 Rescue Agent — chat panel, guided pointer, approval, voice bonus.
   Progressive enhancement: core flow works without this file. All text
   inserted via textContent (no raw HTML from server). */
(function () {
  "use strict";
  const root = document.getElementById("agent-root");
  if (!root) return;
  const CASE = root.getAttribute("data-case-id");
  const WORKSPACE_URL = root.getAttribute("data-workspace-url");
  const fab = document.getElementById("agent-fab");
  const panel = document.getElementById("agent-panel");
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
    (actions || []).slice(0, 4).forEach(label => {
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

  async function send(text) {
    if (inFlight) return;
    text = (text || "").trim();
    if (!text && greeted) return;
    if (text) addMsg("user", text, null, false);
    input.value = "";
    setBusy(true);
    setStatus("Membaca kondisi kasus…");
    renderQuick([]);
    try {
      const res = await fetch(`/api/v1/cases/${CASE}/agent/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text,
          ui_state: { current_page: currentPage(), pending_action: pendingAction },
        }),
      });
      if (!res.ok) throw new Error("http-" + res.status);
      const data = await res.json();
      greeted = true;
      pendingAction = data.proposed_action || null;
      setStatus("Siap membantu");
      addMsg("ai", data.message, data.tools_used, true);
      renderQuick(data.quick_actions);
      if (data.proposed_action) renderProposal(data.proposed_action);
      if (data.guidance) guide(data.guidance);
      if (speakOn) speak(data.message);
    } catch (e) {
      setStatus("Siap membantu");
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
      card.remove();
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

  /* --- Guided pointer: scroll + ring + tooltip, reduced-motion aware --- */
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

  /* --- Voice bonus: push-to-talk STT + optional TTS, same agent API --- */
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  if (!SR) { mic.hidden = true; }
  else {
    const rec = new SR();
    rec.lang = "id-ID";
    rec.interimResults = false;
    rec.maxAlternatives = 1;
    rec.onresult = (ev) => {
      const text = ev.results[0][0].transcript || "";
      input.value = text;
      input.focus();
    };
    rec.onend = () => { mic.classList.remove("listening"); mic.setAttribute("aria-label", "Bicara dengan Tanggap60"); };
    rec.onerror = () => { mic.classList.remove("listening"); showToast("Suara tidak dikenali. Tulis saja pesannya."); };
    mic.addEventListener("click", () => {
      try { mic.classList.add("listening"); rec.start(); } catch (e) { mic.classList.remove("listening"); }
    });
  }
  function speak(text) {
    try {
      if (!("speechSynthesis" in window)) return;
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "id-ID";
      window.speechSynthesis.speak(u);
    } catch (e) {}
  }
  speakBtn.addEventListener("click", () => {
    speakOn = !speakOn;
    speakBtn.setAttribute("aria-pressed", speakOn ? "true" : "false");
    speakBtn.textContent = speakOn ? "🔊" : "🔈";
    if (!speakOn && "speechSynthesis" in window) { try { window.speechSynthesis.cancel(); } catch (e) {} }
  });

  /* --- Panel wiring --- */
  function open() {
    panel.hidden = false;
    fab.setAttribute("aria-expanded", "true");
    // Render stored history only once when container is currently empty
    if (msgs.children.length === 0 && hist.length > 0) {
      hist.forEach(h => {
        if (h && h.role === "ai") addMsg("ai", h.text, h.tools, false);
      });
    }
    if (!greeted && msgs.children.length === 0) send("");
    setTimeout(() => input.focus(), 50);
  }
  function close() { panel.hidden = true; fab.setAttribute("aria-expanded", "false"); fab.focus(); }
  fab.addEventListener("click", () => (panel.hidden ? open() : close()));
  document.getElementById("agent-close").addEventListener("click", close);
  form.addEventListener("submit", (ev) => { ev.preventDefault(); send(input.value); });
})();
