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
    files: "Unggah bukti dulu. Foto chat atau transfer cukup.",
    text: "Ceritakan singkat, atau lewati.",
    url: "Punya tautan? Tempel di sini, atau lewati.",
    submit: "Kirim. Kami baca buktinya.",
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
           filesInput.files = e.dataTransfer.files;
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
        window._toast("Masukkan berkas, cerita, atau tautan.");
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
    REVIEW_REQUIRED: "Siap ditinjau",
    READY_FOR_ACTION: "Menyusun langkah…",
    WAITING_APPROVAL: "Menunggu persetujuan…",
    GENERATING: "Membuat paket…",
    VERIFYING: "Memeriksa paket…",
    HANDOFF_READY: "Paket siap",
    FAILED_SAFE: "Tidak bisa dilanjutkan otomatis. Isi manual di tinjauan.",
  };
  const caseId = document.body.getAttribute("data-case");
  const page = document.body.getAttribute("data-page");
  const waitKind = document.body.getAttribute("data-wait");
  async function json(url, opts) {
    const headers = {};
    if (opts && opts.method && opts.method !== "GET") {
      headers["Idempotency-Key"] = "ui-" + caseId + "-" + (waitKind || "run");
    }
    const res = await fetch(url, Object.assign({ headers }, opts || {}));
    return res.json();
  }
  if (caseId && page === "processing") {
    async function tick() {
      try {
        const data = await json("/api/v1/cases/" + caseId);
        const state = data.state;
        const box = document.getElementById("state-live");
        if (box) box.textContent = STATES[state] || "Sedang berjalan. Halaman ini lanjut sendiri.";
        if (state === "REVIEW_REQUIRED") location.href = "/cases/" + caseId + "/review";
        else if (state === "WAITING_APPROVAL") location.href = "/cases/" + caseId + "/approval";
        else if (state === "HANDOFF_READY") location.href = "/cases/" + caseId + "/artifacts";
        else if (state === "RECEIPT_RECORDED") location.href = "/cases/" + caseId + "/receipt";
      } catch (_) {}
    }
    json("/api/v1/cases/" + caseId + "/runs", { method: "POST" }).finally(function () {
      tick();
      setInterval(tick, 1500);
    });
  }
  if (caseId && page === "wait") {
    async function tickWait() {
      try {
        const data = await json("/api/v1/cases/" + caseId);
        const state = data.state;
        const box = document.getElementById("state-live");
        if (box) {
          box.innerHTML = '<span class="pulse" aria-hidden="true"></span>' + (STATES[state] || "Sedang berjalan. Halaman ini lanjut sendiri.");
        }
        if (waitKind === "pack" && (state === "HANDOFF_READY" || state === "RECEIPT_RECORDED")) location.reload();
        else if (waitKind === "plan" && state === "REVIEW_REQUIRED") location.href = "/cases/" + caseId + "/review";
        else if (waitKind === "plan" && (state === "WAITING_APPROVAL" || state === "HANDOFF_READY")) location.reload();
        else if (state === "FAILED_SAFE") location.href = "/cases/" + caseId + "/review";
      } catch (_) {}
    }
    json("/api/v1/cases/" + caseId + "/runs", { method: "POST" }).finally(function () {
      tickWait();
      setInterval(tickWait, 1500);
    });
  }

  window._toast = function (msg) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 2400);
  };
})();
