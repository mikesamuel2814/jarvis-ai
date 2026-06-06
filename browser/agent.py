"""
Jarvis Browser Agent — Selenium + system Chromium (headless)

Single persistent browser session shared across all calls.
Thread-safe via lock. Auto-restarts if Chrome crashes.

Public API:
  google_search(query, num)  → list[{title, url, snippet}]
  open_url(url)              → clean page text
  click(text_or_css)         → click element on current page
  type_text(text)            → type into active element
  scroll(direction)          → scroll up/down
  screenshot()               → PNG bytes (send to Telegram)
  get_links()                → list[{text, url}]
  current_url()              → str
  quit()                     → close browser
"""

from __future__ import annotations

import logging
import re
import threading
import time
from io import BytesIO
from typing import Any

log = logging.getLogger("jarvis.browser")

_CHROME_BIN      = "/usr/bin/chromium"
_CHROMEDRIVER    = "/usr/bin/chromedriver"
_PAGE_TIMEOUT    = 20_000   # ms
_IMPLICIT_WAIT   = 8        # seconds

_CHROME_ARGS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-blink-features=AutomationControlled",
    "--window-size=1280,900",
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
]


# ── Driver factory ────────────────────────────────────────────────────────

def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.binary_location = _CHROME_BIN
    for arg in _CHROME_ARGS:
        opts.add_argument(arg)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)

    svc = Service(_CHROMEDRIVER, log_output="/dev/null")
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.set_page_load_timeout(_PAGE_TIMEOUT / 1000)
    driver.implicitly_wait(_IMPLICIT_WAIT)

    # Mask webdriver fingerprint
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
    )
    return driver


# ── Singleton browser session ─────────────────────────────────────────────

class BrowserAgent:
    _instance: BrowserAgent | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._driver_lock = threading.Lock()
        self._driver = None

    @classmethod
    def get(cls) -> BrowserAgent:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _driver_ok(self) -> bool:
        try:
            _ = self._driver.current_url
            return True
        except Exception:
            return False

    def _ensure_driver(self):
        if self._driver is None or not self._driver_ok():
            log.info("(Re)starting headless Chromium...")
            try:
                if self._driver:
                    self._driver.quit()
            except Exception:
                pass
            self._driver = _make_driver()
            log.info("Chromium ready.")

    # ── Navigation ────────────────────────────────────────────────────

    def open_url(self, url: str, wait_for: str | None = None) -> str:
        """Navigate to URL. Returns clean extracted text."""
        with self._driver_lock:
            self._ensure_driver()
            try:
                self._driver.get(url)
                if wait_for:
                    from selenium.webdriver.common.by import By
                    from selenium.webdriver.support import expected_conditions as EC
                    from selenium.webdriver.support.ui import WebDriverWait
                    WebDriverWait(self._driver, 10).until(
                        EC.presence_of_element_located((By.CSS_SELECTOR, wait_for))
                    )
                time.sleep(1.5)  # let JS settle
                return self._extract_text()
            except Exception as exc:
                log.warning("open_url(%s) failed: %s", url, exc)
                return ""

    def _extract_text(self) -> str:
        """Extract clean text from current page HTML via trafilatura."""
        try:
            import trafilatura
            html = self._driver.page_source
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
            return (text or "")[:4000]
        except Exception:
            # Fallback: strip tags from body text
            try:
                body = self._driver.find_element("tag name", "body").text
                return body[:4000]
            except Exception:
                return ""

    def current_url(self) -> str:
        with self._driver_lock:
            self._ensure_driver()
            return self._driver.current_url

    def get_title(self) -> str:
        with self._driver_lock:
            self._ensure_driver()
            return self._driver.title

    # ── Google search ─────────────────────────────────────────────────

    def google_search(self, query: str, num: int = 5) -> list[dict]:
        """
        Open Google, search, parse organic results.
        Returns list of {title, url, snippet}.
        """
        with self._driver_lock:
            self._ensure_driver()
            try:
                from selenium.webdriver.common.by import By
                from selenium.webdriver.common.keys import Keys
                from selenium.webdriver.support import expected_conditions as EC
                from selenium.webdriver.support.ui import WebDriverWait

                search_url = f"https://www.google.com/search?q={_encode(query)}&hl=en&num={num}"
                self._driver.get(search_url)
                time.sleep(2)

                results = []

                # Accept cookie banner if present (EU)
                try:
                    btn = self._driver.find_element(By.XPATH, '//button[contains(.,"Accept all")]')
                    btn.click()
                    time.sleep(1)
                except Exception:
                    pass

                # Organic result cards: div[data-hveid] or h3 parent anchors
                cards = self._driver.find_elements(By.CSS_SELECTOR, "div.g, div[data-hveid]")
                for card in cards[:num * 2]:
                    try:
                        a = card.find_element(By.CSS_SELECTOR, "a[href]")
                        href = a.get_attribute("href") or ""
                        if not href.startswith("http"):
                            continue
                        if "google.com" in href:
                            continue
                        title = ""
                        try:
                            title = card.find_element(By.CSS_SELECTOR, "h3").text
                        except Exception:
                            title = a.text[:80]
                        snippet = ""
                        try:
                            snippet = card.find_element(
                                By.CSS_SELECTOR, "div[data-sncf], div.VwiC3b, span.aCOpRe, div.IsZvec"
                            ).text[:300]
                        except Exception:
                            pass
                        if href and title:
                            results.append({"title": title, "url": href, "snippet": snippet})
                            if len(results) >= num:
                                break
                    except Exception:
                        continue

                if not results:
                    # Fallback: grab all h3 + parent href
                    for h3 in self._driver.find_elements(By.TAG_NAME, "h3")[:num]:
                        try:
                            a = h3.find_element(By.XPATH, "..")
                            href = a.get_attribute("href") or ""
                            if href.startswith("http") and "google.com" not in href:
                                results.append({"title": h3.text, "url": href, "snippet": ""})
                        except Exception:
                            continue

                log.info("google_search('%s'): %d results", query, len(results))
                return results

            except Exception as exc:
                log.error("google_search failed: %s", exc)
                return []

    # ── Page actions ──────────────────────────────────────────────────

    def click(self, selector: str) -> bool:
        """Click by CSS selector, XPath, or visible text."""
        with self._driver_lock:
            self._ensure_driver()
            from selenium.webdriver.common.by import By
            for strategy, by in [
                (selector, By.CSS_SELECTOR),
                (f'//*[contains(text(),"{selector}")]', By.XPATH),
                (selector, By.LINK_TEXT),
            ]:
                try:
                    el = self._driver.find_element(by, strategy)
                    el.click()
                    time.sleep(1)
                    return True
                except Exception:
                    continue
            return False

    def type_text(self, text: str, selector: str | None = None) -> bool:
        """Type text into selector or active element."""
        with self._driver_lock:
            self._ensure_driver()
            from selenium.webdriver.common.by import By
            try:
                if selector:
                    el = self._driver.find_element(By.CSS_SELECTOR, selector)
                else:
                    el = self._driver.switch_to.active_element
                el.clear()
                el.send_keys(text)
                return True
            except Exception as exc:
                log.warning("type_text failed: %s", exc)
                return False

    def scroll(self, direction: str = "down", amount: int = 500) -> None:
        with self._driver_lock:
            self._ensure_driver()
            y = amount if direction == "down" else -amount
            self._driver.execute_script(f"window.scrollBy(0, {y})")
            time.sleep(0.5)

    def get_links(self) -> list[dict]:
        """Return all visible links on current page."""
        with self._driver_lock:
            self._ensure_driver()
            from selenium.webdriver.common.by import By
            links = []
            for a in self._driver.find_elements(By.TAG_NAME, "a")[:50]:
                try:
                    href = a.get_attribute("href") or ""
                    text = a.text.strip()
                    if href.startswith("http") and text:
                        links.append({"text": text, "url": href})
                except Exception:
                    continue
            return links

    def screenshot(self) -> bytes:
        """Return PNG screenshot bytes."""
        with self._driver_lock:
            self._ensure_driver()
            return self._driver.get_screenshot_as_png()

    def get_page_text(self) -> str:
        """Extract text from current page."""
        with self._driver_lock:
            self._ensure_driver()
            return self._extract_text()

    # ── Cleanup ───────────────────────────────────────────────────────

    def quit(self):
        with self._driver_lock:
            if self._driver:
                try:
                    self._driver.quit()
                except Exception:
                    pass
                self._driver = None
        BrowserAgent._instance = None


# ── Helpers ───────────────────────────────────────────────────────────────

def _encode(query: str) -> str:
    from urllib.parse import quote_plus
    return quote_plus(query)


# ── Module-level convenience functions ────────────────────────────────────

def google_search(query: str, num: int = 5) -> list[dict]:
    return BrowserAgent.get().google_search(query, num)


def open_url(url: str) -> str:
    return BrowserAgent.get().open_url(url)


def screenshot() -> bytes:
    return BrowserAgent.get().screenshot()


def quit_browser():
    BrowserAgent.get().quit()
