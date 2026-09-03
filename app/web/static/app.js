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
  if (filesInput) filesInput.addEventListener("change", () => renderFiles(filesInput.files));
  if (drop) {
    ["dragenter", "dragover"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        drop.classList.add("drag");
      })
    );
    ["dragleave", "drop"].forEach((ev) =>
      drop.addEventListener(ev, (e) => {
        e.preventDefault();
        if (ev === "drop" && e.dataTransfer && filesInput) {
          filesInput.files = e.dataTransfer.files;
          renderFiles(filesInput.files);
        }
        drop.classList.remove("drag");
      })
    );
    drop.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        filesInput && filesInput.click();
      }
    });
  }
  if (form && submitBtn) {
    form.addEventListener("submit", () => {
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
  if (caseId && page === "processing") {
    async function json(url, opts) {
      const res = await fetch(url, Object.assign({ headers: { "Idempotency-Key": "ui-" + caseId } }, opts || {}));
      return res.json();
    }
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

  window._toast = function (msg) {
    const t = document.getElementById("toast");
    if (!t) return;
    t.textContent = msg;
    t.classList.add("show");
    setTimeout(() => t.classList.remove("show"), 2400);
  };
})();
