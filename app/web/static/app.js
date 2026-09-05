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
    Array.from(list).forEach((f, i) => {
      const el = document.createElement("div");
      el.className = "file-chip";
      el.innerHTML = "<span><b>" + escapeHtml(f.name) + "</b></span><span class='meta'>" + (f.size / 1024).toFixed(1) + " KB</span>";
      const rm = document.createElement("button");
      rm.type = "button";
      rm.className = "btn-text";
      rm.textContent = "Hapus ›";
      rm.addEventListener("click", () => removePending(i));
      el.appendChild(rm);
      fileList.appendChild(el);
    });
  }
  function removePending(index) {
    if (!filesInput || !filesInput.files) return;
    const dt = new DataTransfer();
    Array.from(filesInput.files).forEach((f, i) => { if (i !== index) dt.items.add(f); });
    filesInput.files = dt.files;
    renderFiles(filesInput.files);
  }
  function showTab(name) {
    const steps = document.querySelectorAll(".coach-step");
    if (!steps.length) return;
    document.body.classList.add("coach-enabled");
    steps.forEach((el) => {
      const on = el.getAttribute("data-step") === name;
      el.classList.toggle("is-on", on);
      if (el.hasAttribute("role")) el.hidden = !on;
    });
    document.querySelectorAll(".intake-tabs [role='tab']").forEach((btn) => {
      const on = btn.getAttribute("data-tab") === name;
      btn.classList.toggle("is-on", on);
      btn.setAttribute("aria-selected", on ? "true" : "false");
      btn.tabIndex = on ? 0 : -1;
    });
  }
  if (document.getElementById("intake-form")) {
    const formEl = document.getElementById("intake-form");
    showTab((formEl && formEl.getAttribute("data-default-tab")) || "files");
    const tabs = Array.from(document.querySelectorAll(".intake-tabs [role='tab']"));
    tabs.forEach((btn) => {
      btn.addEventListener("click", () => showTab(btn.getAttribute("data-tab")));
      btn.addEventListener("keydown", (ev) => {
        const i = tabs.indexOf(btn);
        let next = -1;
        if (ev.key === "ArrowRight" || ev.key === "ArrowDown") next = (i + 1) % tabs.length;
        else if (ev.key === "ArrowLeft" || ev.key === "ArrowUp") next = (i - 1 + tabs.length) % tabs.length;
        else if (ev.key === "Home") next = 0;
        else if (ev.key === "End") next = tabs.length - 1;
        else return;
        ev.preventDefault();
        tabs[next].focus();
        showTab(tabs[next].getAttribute("data-tab"));
      });
    });
    const pickFiles = document.getElementById("pick-files");
    if (pickFiles && filesInput) pickFiles.addEventListener("click", () => filesInput.click());
    const addMore = document.getElementById("add-more");
    if (addMore) addMore.addEventListener("click", () => {
      showTab("files");
      if (filesInput) filesInput.click();
    });
  }
  if (filesInput) filesInput.addEventListener("change", () => {
    renderFiles(filesInput.files);
    if (filesInput.files && filesInput.files.length) showTab("files");
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
          if (filesInput.files && filesInput.files.length) showTab("files");
        }
        drop.classList.remove("drag");
      })
    );
  }
  if (form && submitBtn) {
    form.addEventListener("submit", (e) => {
      const text = (document.getElementById("text") || {}).value || "";
      const url = (document.getElementById("url") || {}).value || "";
      const hasFile = filesInput && filesInput.files && filesInput.files.length;
      if (!hasFile && !String(text).trim() && !String(url).trim()) {
        e.preventDefault();
        window._toast("Kirim foto, teks chat, atau link.", true);
        return;
      }
      submitBtn.disabled = true;
      submitBtn.textContent = "Mengirim bukti…";
    });
  }

  const STATES = {
    NEW: "Menyiapkan…",
    INGESTING: "Menerima bukti…",
    EXTRACTING: "Membaca bukti…",
    REVIEW_REQUIRED: "Siap dicek",
    READY_FOR_ACTION: "Menyusun langkah…",
    WAITING_APPROVAL: "Menunggu persetujuan Anda…",
    GENERATING: "Membuat dokumen…",
    VERIFYING: "Memeriksa dokumen…",
    HANDOFF_READY: "Dokumen siap",
    FAILED_SAFE: "Bukti belum terbaca. Tulis datanya sendiri.",
  };
  const FALLBACK = "Masih berjalan. Tunggu di halaman ini. Halaman akan pindah sendiri.";
  const caseId = document.body.getAttribute("data-case-id");
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
    const began = Date.now();
    const start = () => { if (!timer) timer = setInterval(() => {
      if (Date.now() - began > 180000) {
        stop();
        const host = waitAlertHost();
        host.innerHTML = "";
        const el = document.createElement("div");
        el.className = "alert warning";
        el.setAttribute("role", "alert");
        el.innerHTML = "<b>Pemeriksaan terlalu lama</b><p>Status tidak berubah. Muat ulang, atau tulis datanya sendiri.</p>";
        const row = document.createElement("div");
        row.className = "actions";
        const reload = document.createElement("button");
        reload.className = "btn ghost";
        reload.type = "button";
        reload.textContent = "Muat ulang";
        reload.addEventListener("click", () => location.reload());
        row.appendChild(reload);
        if (caseId) {
          const manual = document.createElement("a");
          manual.className = "btn ember";
          manual.href = "/cases/" + caseId + "/review";
          manual.textContent = "Tulis data";
          row.appendChild(manual);
        }
        el.appendChild(row);
        host.appendChild(el);
        return;
      }
      if (!document.hidden) fn();
    }, ms); };
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
    p.textContent = "Halaman tidak bisa mengecek status. Bukti Anda tetap tersimpan. Tekan Coba lagi.";
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

  // Harden every plain POST form against double submit. Disable is deferred
  // one tick so the submitter's own name/value still ships with the payload.
  document.addEventListener("submit", (e) => {
    const f = e.target;
    if (e.defaultPrevented) return;
    if (!(f instanceof HTMLFormElement) || f.dataset.noHarden !== undefined) return;
    if (f.method.toLowerCase() !== "post" || f.enctype === "multipart/form-data") return;
    const btn = f.querySelector('[type="submit"]:not([disabled])');
    if (!btn) return;
    const label = btn.textContent.trim();
    setTimeout(() => {
      // Inline confirm()/validation may still cancel after this listener runs.
      if (e.defaultPrevented || !btn.isConnected || btn.disabled) return;
      btn.disabled = true;
      if (label) btn.textContent = "Memproses…";
    }, 0);
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
  window.t60Purge = async function (ev) {
    if (ev && ev.preventDefault) ev.preventDefault();
    const id = document.body.getAttribute("data-case-id");
    if (!id) return false;
    if (!confirm("Hapus semua data kasus ini? Foto, data, dan dokumen ikut hilang dan tidak bisa dikembalikan.")) return false;
    let res;
    try {
      res = await fetch("/api/v1/cases/" + id, {
        method: "DELETE",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmation: "PURGE" }),
      });
    } catch (_) {
      const t = document.getElementById("toast");
      if (t) {
        t.textContent = "Koneksi terputus. Coba lagi.";
        t.classList.add("show");
        setTimeout(() => t.classList.remove("show"), 4000);
      }
      return false;
    }
    if (res.ok) {
      try {
        sessionStorage.removeItem("t60agent:" + id);
        sessionStorage.removeItem("t60plan:" + id);
        sessionStorage.removeItem("t60wait:" + id);
      } catch (e) {}
      location.href = "/";
      return false;
    }
    const t = document.getElementById("toast");
    if (t) {
      t.textContent = "Belum terhapus. Coba lagi.";
      t.classList.add("show");
      setTimeout(() => t.classList.remove("show"), 4000);
    }
    return false;
  };

  document.querySelectorAll("form[data-confirm-message]").forEach((resetForm) => {
    resetForm.addEventListener("submit", (event) => {
      if (!confirm(resetForm.getAttribute("data-confirm-message"))) event.preventDefault();
    });
  });

  const purgeForm = document.getElementById("purge-browser-form");
  if (purgeForm) purgeForm.addEventListener("submit", window.t60Purge);

  const handoffLink = document.querySelector("[data-handoff-opened]");
  if (handoffLink && caseId) {
    handoffLink.addEventListener("click", () => {
      fetch("/api/v1/cases/" + caseId + "/handoff/opened", { method: "POST", keepalive: true }).catch(() => {});
    });
  }

  const decision = document.getElementById("decision");
  if (decision && caseId) {
    const nextMessage = {
      VERIFY_VIA_OFFICIAL_CHANNEL: "Cari nomor atau situs resmi sendiri. Jangan pakai link dari chat yang mencurigakan.",
      CANCELLED_ACTION: "Pilihan dibatalkan. Anda bisa mulai lagi nanti.",
      PROCEED_BY_USER: "Jangan transfer sebelum Anda yakin. Tanggap60 tidak menyatakan link ini aman.",
    };
    decision.addEventListener("click", async (event) => {
      const target = event.target;
      const button = target instanceof Element ? target.closest("[data-d]") : null;
      if (!(button instanceof HTMLButtonElement) || button.disabled) return;
      event.preventDefault();
      const choice = button.getAttribute("data-d");
      const buttons = document.querySelectorAll("#decision [data-d]");
      const box = document.getElementById("decision-next");
      if (!box || !choice) return;
      buttons.forEach((item) => { item.disabled = true; });
      box.hidden = false;
      box.textContent = "Mencatat pilihan...";
      try {
        const response = await fetch("/api/v1/cases/" + caseId + "/decision", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ decision: choice }),
        });
        box.textContent = response.ok
          ? "Pilihan tercatat. " + nextMessage[choice]
          : "Pilihan belum tercatat. Coba tekan pilihan Anda lagi.";
      } catch (_) {
        box.textContent = "Koneksi terputus. Penyimpanan pilihan belum dapat dipastikan. Periksa koneksi, lalu tekan pilihan Anda lagi.";
      } finally {
        buttons.forEach((item) => { item.disabled = false; });
      }
    });
  }
})();
