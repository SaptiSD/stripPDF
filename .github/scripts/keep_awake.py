"""Visit the Streamlit app in a headless browser so Streamlit Community Cloud keeps it awake.

Streamlit puts apps to sleep after 12 hours without visitors, and only a real page load
counts as a visit (a plain HTTP request does not). If the app is already asleep, this
presses the "Yes, get this app back up!" button. Exits with an error if the app never
loads, so a failing run doubles as a "site is down" alert.
"""
import os
import re

from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

APP_URL = os.environ.get("APP_URL", "https://strip-pdf.streamlit.app/")

with sync_playwright() as p:
    browser = p.chromium.launch()
    page = browser.new_page()
    page.goto(APP_URL, wait_until="domcontentloaded", timeout=60_000)

    wake_button = page.get_by_role("button", name=re.compile("get this app back up", re.I))
    try:
        wake_button.wait_for(timeout=15_000)
        print("App was asleep - waking it up")
        wake_button.click()
    except PlaywrightTimeout:
        print("App was awake")

    # The app itself runs inside an iframe; wait until its title has rendered.
    app = page.frame_locator('iframe[title="streamlitApp"]')
    app.get_by_role("heading", name="PDF Text Extractor").wait_for(timeout=180_000)
    print(f"App loaded OK: {APP_URL}")
    browser.close()
