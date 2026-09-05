from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest
from fastapi.testclient import TestClient

from tests.conftest import ScriptedOcr
from tests.hero_support import create_case, upload_text_png


def test_review_reload_keeps_checked_facts_editable_and_refreshes_version(client: TestClient, ocr: ScriptedOcr):
    case_id = create_case(client)
    upload_text_png(
        client, ocr, case_id, "transfer.png",
        "Transfer Berhasil Rp500.000 Ke: DEMO-DEST-C 23 September 2026 10:05 WIB",
    )
    assert client.post(f"/api/v1/cases/{case_id}/runs", headers={"Idempotency-Key": "continuity"}).status_code == 202
    facts = client.get(f"/api/v1/cases/{case_id}/facts").json()["facts"]
    candidates = [fact for fact in facts if fact["review_status"] == "CANDIDATE"]
    assert len(candidates) > 1
    version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
    page = client.get(f"/cases/{case_id}/review").text
    assert f"{len(candidates)} data lagi perlu diperiksa." in page
    for index, fact in enumerate(candidates):
        payload = {"action": "confirm", "expected_version": version}
        if index == 0:
            payload.update(action="correct", value=fact["raw_value"])
        response = client.patch(f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}", json=payload)
        assert response.status_code == 200
        stale = client.patch(f"/api/v1/cases/{case_id}/facts/{fact['fact_id']}", json=payload)
        assert stale.status_code == 409
        version = client.get(f"/api/v1/cases/{case_id}").json()["version"]
        page = client.get(f"/cases/{case_id}/review").text
        assert f"const VERSION = {version};" in page
        for checked in candidates[:index + 1]:
            card = re.search(rf'<article class="fact[^"]*" data-fact="{checked["fact_id"]}">(.*?)</article>', page, re.S)
            assert card is not None
            assert ">Ubah</button>" in card[1]
            assert f'id="input-{checked["fact_id"]}"' in card[1]
        remaining = len(candidates) - index - 1
        assert (f"{remaining} data lagi perlu diperiksa." if remaining else "Semua data sudah diperiksa.") in page
    assert 'id="review-summary"' in page


def test_review_empty_copy(client: TestClient):
    case_id = create_case(client)
    page = client.get(f"/cases/{case_id}/review").text
    assert "Belum ada data untuk diperiksa" in page
    assert "bukan hasil pembacaan foto" in page
    assert "OCR" not in page
    assert "Simpan pasangan" not in page


@pytest.mark.parametrize("scenario", ["success", "reject", "correct", "conflict", "complete", "stale", "offline", "storage", "invalid", "initial"])
def test_review_continuity_javascript(client: TestClient, scenario: str):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to execute the inline review JavaScript without a browser")
    version = subprocess.run([node, "--version"], text=True, capture_output=True, timeout=15)
    try:
        major = int(version.stdout.strip().lstrip("v").split(".", 1)[0])
    except (ValueError, IndexError):
        major = 0
    if major < 8:
        pytest.skip("Node.js 8+ is required for the review JavaScript harness")
    case_id = create_case(client)
    page = client.get(f"/cases/{case_id}/review").text
    script = next(script for script in re.findall(r"<script>(.*?)</script>", page, re.S) if "const VERSION" in script)
    # Execute the actual rendered script with a small DOM/storage double, not a user's browser.
    harness = r"""
const assert = require('assert');
const vm = require('vm');
const {script, scenario} = JSON.parse(require('fs').readFileSync(0, 'utf8'));
const storage = new Map();
let reloads = 0, focused = null, centered = null, scroll = null, requests = [];
let pendingIds = ['a', 'b', 'c'];
let cards = ['a', 'b', 'c'];
let onload;
const stale = {hidden: true};
const input = {value: '500000'};
const toast = {textContent: '', setAttribute(){}, classList: {add(){}, remove(){}}};
const button = id => ({
  id, disabled: false, value: id,
  getAttribute: () => scenario === 'reject' ? 'reject' : 'confirm',
  closest: () => ({dataset: {fact: id}}),
  focus(options){ assert.strictEqual(options.preventScroll, true); focused = id; },
  getBoundingClientRect: () => ({top: scenario === 'reject' ? 1000 : 200, bottom: scenario === 'reject' ? 1044 : 244}),
  scrollIntoView(options){ assert.strictEqual(options.behavior, 'instant'); centered = id; }
});
const context = vm.createContext({
  console, Date, JSON, Array, Number,
  sessionStorage: {
    getItem: key => storage.get(key) || null,
    removeItem: key => storage.delete(key),
    setItem(key, value){ if(scenario === 'storage') throw Error('blocked'); storage.set(key, value); }
  },
  location: {reload(){ reloads++; }},
  window: {
    scrollY: 420, innerHeight: 800,
    addEventListener(event, callback){ assert.strictEqual(event, 'load'); onload = callback; },
    scrollTo(options){ scroll = options.top; assert.strictEqual(options.behavior, 'instant'); }
  },
  document: {
    querySelectorAll(selector){
      return selector === '[data-fact]' ? cards.map(id => ({dataset: {fact: id}})) : pendingIds.map(button);
    },
    querySelector: () => null,
    getElementById(id){
      if(id === 'toast') return toast;
      if(id === 'review-stale') return stale;
      if(id.startsWith('input-')) return input;
      if(id === 'review-summary') return pendingIds.length ? null : button('summary');
    }
  },
  requestAnimationFrame: callback => callback(),
  setTimeout: () => {},
  async fetch(url, options){
    requests.push({url, body: JSON.parse(options.body)});
    if(scenario === 'offline') throw Error('offline');
    return {ok: scenario !== 'stale', status: scenario === 'stale' ? 409 : 200, json: async () => ({message: 'stale'})};
  }
});
vm.runInContext(script, context);
(async () => {
  if(scenario === 'initial'){ onload(); assert.strictEqual(focused, null); assert.strictEqual(scroll, null); return; }
  const event = {preventDefault(){}, currentTarget: button('b'), submitter: button('b')};
  context.event = event;
  const action = scenario === 'correct' ? "submitCorrect('b')" : scenario === 'conflict' ? "resolveConflict(event, 'conflict')" : "patchFact(event, 'b')";
  const saving = vm.runInContext(action, context);
  await vm.runInContext("patchFact(event, 'a')", context);
  assert.strictEqual(requests.length, 1, 'no overlapping writes with the same version');
  await saving;
  assert.strictEqual(typeof requests[0].body.expected_version, 'number');
  if(scenario === 'stale' || scenario === 'offline'){
    assert.strictEqual(reloads, 0);
    assert.strictEqual(storage.size, 0);
    assert.strictEqual(input.value, '500000');
    assert.strictEqual(event.currentTarget.disabled, false);
    assert.strictEqual(stale.hidden, scenario !== 'stale');
    await vm.runInContext(action, context);
    assert.strictEqual(requests.length, 2, 'retry is available after a failure');
    return;
  }
  assert.strictEqual(reloads, 1);
  if(scenario === 'storage'){ assert.strictEqual(storage.size, 0); return; }
  const key = [...storage.keys()][0];
   assert.ok(key.startsWith('review-continuity:'));
  const saved = JSON.parse(storage.get(key));
  assert.deepStrictEqual(saved.next, ['c', 'a', 'b']);
  assert.strictEqual(saved.scrollY, 420);
  assert.strictEqual(storage.get(key).includes('500000'), false, 'no fact values in storage');
  if(scenario === 'invalid'){
    storage.set(key, '{invalid'); onload(); assert.strictEqual(focused, null); return;
  }
  // Simulate authoritative HTML after the save, including another reviewer's update.
  pendingIds = scenario === 'complete' ? [] : scenario === 'correct' ? ['a'] : ['a', 'c'];
  if(scenario === 'reject') cards = ['a', 'c'];
  onload();
  assert.strictEqual(focused, scenario === 'complete' ? 'summary' : scenario === 'correct' ? 'a' : 'c');
  assert.strictEqual(scroll, 420);
  assert.strictEqual(centered, scenario === 'reject' ? 'c' : null);
  assert.strictEqual(storage.size, 0, 'continuity is consumed once');
  assert.strictEqual(toast.textContent, 'Tersimpan');
  focused = null; onload(); assert.strictEqual(focused, null);
})().catch(error => { console.error(error); process.exitCode = 1; });
"""
    result = subprocess.run(
        [node, "-e", harness], input=json.dumps({"script": script, "scenario": scenario}),
        text=True, capture_output=True, timeout=15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
