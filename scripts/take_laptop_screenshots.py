#!/usr/bin/env python3
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT.parent / "Screenshot"
OUT_DIR.mkdir(parents=True, exist_ok=True)
FIXTURES = ROOT / "fixtures" / "demo_tanggap60"

BASE_URL = "http://127.0.0.1:8000"

def run():
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/usr/bin/google-chrome", headless=True)
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            device_scale_factor=1,
        )
        page = context.new_page()

        print("1. Opening Landing Page...")
        page.goto(f"{BASE_URL}/", wait_until="networkidle")
        page.screenshot(path=str(OUT_DIR / "01_landing_hero.png"), full_page=True)
        print("   Saved 01_landing_hero.png")

        # Open details / FAQs
        details = page.query_selector_all("details")
        for d in details:
            try:
                d.evaluate("el => el.open = true")
            except Exception:
                pass
        time.sleep(0.5)
        page.screenshot(path=str(OUT_DIR / "02_landing_faq_detail.png"), full_page=True)
        print("   Saved 02_landing_faq_detail.png")

        # Click Start "Sudah transfer"
        print("2. Starting Case (AFTER_LOSS)...")
        with page.expect_navigation():
            page.click("button[name='declared_condition'][value='AFTER_LOSS']")
        
        case_id = page.url.split("/cases/")[1].split("/")[0]
        print(f"   Created Case: {case_id}")
        page.screenshot(path=str(OUT_DIR / "03_intake_empty.png"), full_page=True)
        print("   Saved 03_intake_empty.png")

        # Fill Intake Form
        print("3. Filling Intake Form with Evidence...")
        chat_img = FIXTURES / "01_chat.png"
        trans_img = FIXTURES / "03_transfer.png"
        
        file_input = page.query_selector("input#files")
        if file_input and chat_img.exists() and trans_img.exists():
            file_input.set_input_files([str(chat_img), str(trans_img)])
            time.sleep(0.5)

        text_toggle = page.query_selector("button[data-input-toggle='text']")
        if text_toggle:
            text_toggle.click()
            time.sleep(0.2)
            page.fill("textarea#text", "Pelaku mengirimkan bukti transaksi dan meminta transfer Rp2.750.000 ke rekening BCA.")
            time.sleep(0.3)

        page.screenshot(path=str(OUT_DIR / "04_intake_filled.png"), full_page=True)
        print("   Saved 04_intake_filled.png")

        # Submit intake
        print("4. Submitting Intake Form...")
        submit_btn = page.query_selector("button#submit-intake")
        if submit_btn:
            submit_btn.click()
        
        # Wait for processing or review page
        time.sleep(1)
        if "/processing" in page.url:
            print("   On processing page...")
            page.screenshot(path=str(OUT_DIR / "05_processing_status.png"), full_page=True)
            print("   Saved 05_processing_status.png")
            page.wait_for_url("**/review", timeout=30000)

        print(f"5. Arrived at Review Page: {page.url}")
        time.sleep(1)
        page.screenshot(path=str(OUT_DIR / "06_review_conflicts.png"), full_page=True)
        print("   Saved 06_review_conflicts.png")

        # Resolve conflict if present
        resolve_btns = page.query_selector_all("button[name='resolution_fact_id']")
        if resolve_btns:
            print(f"   Found {len(resolve_btns)} conflict choices. Picking the first (Rp2.750.000)...")
            resolve_btns[0].click()
            time.sleep(1.5)

        # Confirm any remaining candidate facts
        while True:
            confirm_btn = page.query_selector("button[data-action='confirm']")
            if not confirm_btn or not confirm_btn.is_visible():
                break
            print("   Confirming fact item...")
            confirm_btn.click()
            time.sleep(0.8)

        page.screenshot(path=str(OUT_DIR / "07_review_resolved.png"), full_page=True)
        print("   Saved 07_review_resolved.png")

        # Submit Continue Form to transition case state
        print("6. Submitting Continue form to transition case state...")
        continue_form = page.query_selector("form[action$='/continue']")
        if continue_form:
            with page.expect_navigation():
                continue_form.evaluate("f => f.submit()")
        else:
            page.goto(f"{BASE_URL}/cases/{case_id}/readiness", wait_until="networkidle")

        time.sleep(1)
        print(f"   Current URL: {page.url}")
        page.screenshot(path=str(OUT_DIR / "08_readiness_and_emergency_call.png"), full_page=True)
        print("   Saved 08_readiness_and_emergency_call.png")

        # Test Copy Script button
        copy_script_btn = page.query_selector("#btn-copy-call-script")
        if copy_script_btn:
            copy_script_btn.click()
            time.sleep(0.5)
            page.screenshot(path=str(OUT_DIR / "08b_readiness_script_copied.png"), full_page=True)
            print("   Saved 08b_readiness_script_copied.png")

        # Proceed to Approval Page
        print("7. Navigating to Approval...")
        primary_btn = page.query_selector("a.btn.ember[href*='/approval']")
        if primary_btn:
            with page.expect_navigation():
                primary_btn.click()
        else:
            page.goto(f"{BASE_URL}/cases/{case_id}/approval", wait_until="networkidle")

        time.sleep(1)
        print(f"   Current URL: {page.url}")
        page.screenshot(path=str(OUT_DIR / "09_approval_gate.png"), full_page=True)
        print("   Saved 09_approval_gate.png")

        # Check checkbox and submit approval
        checkbox = page.query_selector("input#notice")
        if checkbox:
            checkbox.check()
            time.sleep(0.3)
            submit_appr = page.query_selector("button#go")
            if submit_appr:
                print("   Submitting approval form to create artifacts...")
                with page.expect_navigation(timeout=30000):
                    submit_appr.click()

        time.sleep(2)
        print(f"8. Arrived at: {page.url}")
        page.screenshot(path=str(OUT_DIR / "10_artifacts_ready.png"), full_page=True)
        print("   Saved 10_artifacts_ready.png")

        # Expand details in artifacts
        details = page.query_selector_all("details")
        for d in details:
            try:
                d.evaluate("el => el.open = true")
            except Exception:
                pass
        time.sleep(0.5)
        page.screenshot(path=str(OUT_DIR / "10b_artifacts_expanded.png"), full_page=True)
        print("   Saved 10b_artifacts_expanded.png")

        # Go to Workspace
        print("9. Navigating to Safe Workspace...")
        page.goto(f"{BASE_URL}/cases/{case_id}/workspace", wait_until="networkidle")
        time.sleep(2)
        page.screenshot(path=str(OUT_DIR / "11_safe_workspace.png"), full_page=True)
        print("   Saved 11_safe_workspace.png")

        # Go to Receipt
        print("10. Navigating to Receipt...")
        page.goto(f"{BASE_URL}/cases/{case_id}/receipt", wait_until="networkidle")
        time.sleep(1)
        page.screenshot(path=str(OUT_DIR / "12_receipt.png"), full_page=True)
        print("   Saved 12_receipt.png")

        browser.close()
        print("\nAll screenshots refreshed successfully in Screenshot/ folder!")

if __name__ == "__main__":
    run()
