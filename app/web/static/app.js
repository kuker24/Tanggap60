(function () {
  const caseId = document.body.getAttribute("data-case");
  const page = document.body.getAttribute("data-page");
  if (!caseId || page !== "processing") return;

  async function json(url, opts) {
    const res = await fetch(url, Object.assign({ headers: { "Idempotency-Key": "ui-" + caseId } }, opts || {}));
    return res.json();
  }

  async function tick() {
    const data = await json("/api/v1/cases/" + caseId);
    const state = data.state;
    const box = document.getElementById("state-live");
    if (box) box.textContent = state;
    if (state === "REVIEW_REQUIRED") location.href = "/cases/" + caseId + "/review";
    else if (state === "WAITING_APPROVAL") location.href = "/cases/" + caseId + "/approval";
    else if (state === "HANDOFF_READY") location.href = "/cases/" + caseId + "/artifacts";
    else if (state === "FAILED_SAFE") box.textContent = "Gagal aman. Coba ulang atau isi manual.";
  }

  json("/api/v1/cases/" + caseId + "/runs", { method: "POST" }).finally(function () {
    tick();
    setInterval(tick, 1500);
  });
})();
