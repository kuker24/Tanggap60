(function () {
  const drop = document.getElementById("drop");
  const filesInput = document.getElementById("files");
  const fileList = document.getElementById("file-list");
  const form = document.getElementById("intake-form");
  const submitBtn = document.getElementById("submit-intake");

  function escapeHtml(s) {
    return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }
  function renderFiles(list) {
    if (!fileList) return;
    fileList.innerHTML = "";
    if (!list || !list.length) return;
    Array.from(list).forEach((f) => {
      const el = document.createElement("div");
      el.className = "file-chip";
      el.innerHTML = "<span><b>" + escapeHtml(f.name) + "</b></span><span class='meta'>" + (f.size / 1024).toFixed(1) + " KB</span>";
      fileList.appendChild(el);
    });
  }
  const lede = document.getElementById("coach-lede");
  const LEDES = {
    files: "Foto, cerita, atau link — satu saja cukup untuk mulai.",
    text: "Ceritakan singkat, atau lewati.",
    url: "Punya link? Tempel di sini, atau lewati.",
    submit: "Kirim. Kami baca dulu isinya.",
  };
  function showCoach(names) {
    const steps = document.querySelectorAll(".coach-step");
    if (!steps.length) return;
    document.body.classList.add("coach-enabled");
    steps.forEach((el) => {
      const on = names.indexOf(el.getAttribute("data-step")) !== -1;
      el.classList.toggle("is-on", on);
    });
    const focus = names[names.length - 1];
    const dropEl = document.getElementById("drop");
    if (dropEl) dropEl.classList.toggle("is-focus", focus === "files");
    const filesStep = document.querySelector('[data-step="files"]');
    if (filesStep) filesStep.classList.toggle("submit-ready", names.indexOf("submit") !== -1);
    const notice = document.querySelector(".notice");
    if (notice) notice.hidden = focus !== "files";
    if (lede && LEDES[focus]) lede.textContent = LEDES[focus];
  }
  if (document.getElementById("intake-form")) {
    const saved = document.querySelector(".file-list.pack .file-chip");
    if (saved) showCoach(["files", "submit"]);
    else showCoach(["files"]);
    const goText = () => showCoach(["text"]);
    const goUrl = () => showCoach(["url"]);
    const goSubmit = () => showCoach(["files", "submit"]);
    const skipFiles = document.getElementById("skip-files");
    const nextText = document.getElementById("next-text");
    const skipText = document.getElementById("skip-text");
    const nextUrl = document.getElementById("next-url");
    const skipUrl = document.getElementById("skip-url");
    const pickFiles = document.getElementById("pick-files");
    if (pickFiles && filesInput) pickFiles.addEventListener("click", () => filesInput.click());
    const addMore = document.getElementById("add-more");
    if (addMore) addMore.addEventListener("click", () => {
      const filesStep = document.querySelector('[data-step="files"]');
      if (filesStep) filesStep.classList.remove("submit-ready");
      showCoach(["files"]);
      if (filesInput) filesInput.click();
    });
    if (skipFiles) skipFiles.addEventListener("click", goText);
    if (nextText) nextText.addEventListener("click", goUrl);
    if (skipText) skipText.addEventListener("click", goUrl);
    if (nextUrl) nextUrl.addEventListener("click", goSubmit);
    if (skipUrl) skipUrl.addEventListener("click", goSubmit);
  }
  if (document.querySelector("#fact-grid .fact.is-on")) {
    document.body.classList.add("coach-enabled");
  }
  if (filesInput) filesInput.addEventListener("change", () => {
    renderFiles(filesInput.files);
    if (filesInput.files && filesInput.files.length) showCoach(["text"]);
  });
  if (drop && filesInput) {
    drop.addEventListener("click", (e) => {
      if (e.target === filesInput) return;
      filesInput.click();
    });
    ["dragenter", "dragover"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.add("drag");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        if (ev === "drop" && e.dataTransfer) {
          try {
            filesInput.files = e.dataTransfer.files;
          } catch (_) {
            window._toast("File tidak terbaca. Pakai tombol Pilih foto atau file.", true);
            drop.classList.remove("drag");
            return;
          }
          renderFiles(filesInput.files);
          if (filesInput.files && filesInput.files.length) showCoach(["text"]);
        }
        drop.classList.remove("drag");
      })
    );
    drop.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        filesInput.click();
      }
    });
  }
  if (form && submitBtn) {
    form.addEventListener("submit", (e) => {
      const text = (document.getElementById("text") || {}).value || "";
      const url = (document.getElementById("url") || {}).value || "";
      const hasFile = filesInput && filesInput.files && filesInput.files.length;
      if (!hasFile && !String(text).trim() && !String(url).trim()) {
        e.preventDefault();
        window._toast("Isi dulu salah satu: foto, cerita, atau link.", true);
        return;
      }
      submitBtn.disabled = true;
      submitBtn.textContent = "Mengirim…";
    });
  }

  const STATES = {
    NEW: "Menyiapkan…",
    INGESTING: "Menerima bukti…",
    EXTRACTING: "Membaca bukti…",
    REVIEW_REQUIRED: "Siap dicek",
    READY_FOR_ACTION: "Menyusun langkah…",
    WAITING_APPROVAL: "Menunggu persetujuan Anda…",
    GENERATING: "Membuat paket…",
    VERIFYING: "Memeriksa paket…",
    HANDOFF_READY: "Paket siap",
    FAILED_SAFE: "Perlu isi manual. Buka halaman koreksi.",
  };
  const FALLBACK = "Masih berjalan. Tunggu di halaman ini — pindah sendiri.";
  const caseId = document.body.getAttribute("data-case");
  const page = document.body.getAttribute("data-page");
  const waitKind = document.body.getAttribute("data-wait");
  let lastState = "";
  async function json(url, opts) {
    const headers = {};
    if (opts && opts.method && opts.method !== "GET") {
      headers["Idempotency-Key"] = "ui-" + caseId + "-" + (waitKind || "run");
    }
    const res = await fetch(url, Object.assign({ headers }, opts || {}));
    if (!res.ok) throw new Error("http-" + res.status);
    return res.json();
  }
  function setLive(text) {
    const box = document.getElementById("state-live");
    if (!box || box.getAttribute("data-fixed") === "1") return;
    if (box.textContent === text) return;
    box.textContent = text;
  }
  function poll(fn, ms) {
    let timer = 0;
    const start = () => { if (!timer) timer = setInterval(() => { if (!document.hidden) fn(); }, ms); };
    const stop = () => { if (timer) { clearInterval(timer); timer = 0; } };
    document.addEventListener("visibilitychange", () => { if (document.hidden) stop(); else { fn(); start(); } });
    fn();
    start();
  }
  function waitAlertHost() {
    let host = document.getElementById("wait-alert");
    if (!host) {
      host = document.createElement("div");
      host.id = "wait-alert";
      const main = document.querySelector("main");
      if (main) main.prepend(host);
    }
    return host;
  }
  let waitFails = 0;
  function waitNoteFail(retry) {
    waitFails += 1;
    if (waitFails < 3) return;
    const host = waitAlertHost();
    host.innerHTML = "";
    const el = document.createElement("div");
    el.className = "alert warning";
    el.setAttribute("role", "alert");
    const b = document.createElement("b");
    b.textContent = "Koneksi terputus-putus";
    const p = document.createElement("p");
    p.textContent = "Halaman tidak bisa mengecek status. Bukti Anda tetap tersimpan — tekan Coba lagi.";
    const row = document.createElement("div");
    row.className = "actions";
    const btn = document.createElement("button");
    btn.className = "btn ghost";
    btn.type = "button";
    btn.textContent = "Coba lagi";
    btn.addEventListener("click", () => { waitFails = 0; host.innerHTML = ""; retry(); });
    row.appendChild(btn);
    el.appendChild(b);
    el.appendChild(p);
    el.appendChild(row);
    host.appendChild(el);
  }
  if (caseId && page === "processing") {
    async function tick() {
      try {
        const data = await json("/api/v1/cases/" + caseId);
        const state = data.state;
        if (state !== lastState) {
          lastState = state;
          setLive(STATES[state] || FALLBACK);
        }
        if (state === "REVIEW_REQUIRED" || state === "FAILED_SAFE") location.href = "/cases/" + caseId + "/review";
        else if (state === "WAITING_APPROVAL") location.href = "/cases/" + caseId + "/approval";
        else if (state === "HANDOFF_READY") location.href = "/cases/" + caseId + "/artifacts";
        else if (state === "RECEIPT_RECORDED") location.href = "/cases/" + caseId + "/receipt";
      } catch (_) { waitNoteFail(tick); }
    }
    json("/api/v1/cases/" + caseId + "/runs", { method: "POST" }).catch(() => {}).finally(function () {
      poll(tick, 1500);
    });
  }
  if (caseId && page === "wait") {
    async function tickWait() {
      try {
        const data = await json("/api/v1/cases/" + caseId);
        const state = data.state;
        if (state !== lastState) {
          lastState = state;
          const box = document.getElementById("state-live");
          if (box && box.getAttribute("data-fixed") !== "1") {
            box.innerHTML = "";
            const dot = document.createElement("span");
            dot.className = "pulse";
            dot.setAttribute("aria-hidden", "true");
            box.appendChild(dot);
            box.appendChild(document.createTextNode(STATES[state] || FALLBACK));
          }
        }
        if (waitKind === "pack" && (state === "HANDOFF_READY" || state === "RECEIPT_RECORDED")) location.reload();
        else if (waitKind === "plan" && state === "REVIEW_REQUIRED") location.href = "/cases/" + caseId + "/review";
        else if (waitKind === "plan" && (state === "WAITING_APPROVAL" || state === "HANDOFF_READY")) location.reload();
        else if (state === "FAILED_SAFE") location.href = "/cases/" + caseId + "/review";
      } catch (_) { waitNoteFail(tickWait); }
    }
    json("/api/v1/cases/" + caseId + "/runs", { method: "POST" }).catch(() => {}).finally(function () {
      poll(tickWait, 1500);
    });
  }

  window._toast = function (msg, isErr) {    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.setAttribute("role", isErr ? "alert" : "status");
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), isErr ? 4000 : 2400);
  };

  window._alert = function (boxId, type, title, msg) {
    const box = document.getElementById(boxId);
    if (!box) { window._toast(title + ". " + msg, type === "error"); return; }
    box.innerHTML = "";
    const el = document.createElement("div");
    el.className = "alert " + (type || "info");
    el.setAttribute("role", type === "error" ? "alert" : "status");
    el.setAttribute("tabindex", "-1");
    const b = document.createElement("b");
    b.textContent = title;
    const p = document.createElement("p");
    p.textContent = msg;
    el.appendChild(b);
    el.appendChild(p);
    box.appendChild(el);
    el.focus({ preventScroll: false });
  };

  async function apiFetch(url, opts) {
    let res;
    try {
      res = await fetch(url, opts);
    } catch (_) {
      throw new Error("Koneksi terputus. Periksa internet Anda lalu coba lagi.");
    }
    if (res.ok) return res;
    const j = await res.json().catch(() => ({}));
    throw new Error(j.message || ("Gagal (kode " + res.status + "). Coba lagi."));
  }

  // Harden every plain POST form against double submit.
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (!(f instanceof HTMLFormElement) || f.dataset.noHarden !== undefined) return;
    if (f.method.toLowerCase() !== "post" || f.enctype === "multipart/form-data") return;
    const btn = f.querySelector('[type="submit"]');
    if (btn && !btn.disabled) {
      btn.disabled = true;
      if (btn.textContent.trim()) btn.dataset.label = btn.textContent;
      btn.textContent = "Memproses…";
      setTimeout(() => {
        btn.disabled = false;
        if (btn.dataset.label) btn.textContent = btn.dataset.label;
      }, 9000);
    }
  });
  // Technical trace: lazy-load only when the user opens the disclosure.
  const trace = document.getElementById("trace");
  if (trace && !trace.dataset.loaded) {
    trace.addEventListener("toggle", async () => {
      if (!trace.open || trace.dataset.loaded) return;
      trace.dataset.loaded = "1";
      const body = document.getElementById("trace-body");
      const cid = (body && body.getAttribute("data-case")) || caseId;
      if (!body || !cid) return;
      try {
        const data = await apiFetch("/api/v1/cases/" + cid + "/events");
        const rows = (data.events || []).filter((e) => e.tool_name).map((e) =>
          "<tr><td>" + escapeHtml(e.tool_name || e.event_type || "") + "</td><td>" +
          escapeHtml(String(e.duration_ms == null ? "—" : e.duration_ms) + " ms") + "</td><td>" +
          escapeHtml(e.result_code || "") + "</td><td>" + escapeHtml(e.state_after || "") + "</td></tr>"
        );
        body.innerHTML = rows.length
          ? "<table><tr><th>Alat</th><th>Waktu</th><th>Hasil</th><th>Status</th></tr>" + rows.join("") + "</table>"
          : "Belum ada jejak teknis.";
      } catch (err) {
        body.textContent = err.message || "Gagal memuat jejak teknis.";
      }
    });
  }
})();
