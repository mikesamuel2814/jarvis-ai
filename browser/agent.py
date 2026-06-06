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
import os
import re
import threading
import time
from io import BytesIO
from typing import Any

# Kali-specific segfault mitigation for anything spawning subprocesses near
# Chrome (chromedriver / renderer / zygote). Set at import time so it is
# inherited by every child process this module launches.
os.environ.setdefault("MALLOC_ARENA_MAX", "2")

log = logging.getLogger("jarvis.browser")

_CHROME_BIN      = "/usr/bin/chromium"
_CHROMEDRIVER    = "/usr/bin/chromedriver"
_PAGE_TIMEOUT    = 20_000   # ms
_IMPLICIT_WAIT   = 8        # seconds
_MAX_ATTEMPTS    = 3        # open_url tries (1 + 2 fresh-driver crash retries)
_IDLE_TIMEOUT    = 30       # seconds of inactivity → auto-close the browser (CPU backstop)

# Substrings that indicate the renderer/tab died and the driver is unusable.
_CRASH_MARKERS = (
    "tab crashed",
    "renderer",
    "session deleted",
    "disconnected",
    "no such window",
    "chrome not reachable",
    "invalid session id",
    "web view not found",
    "target window already closed",
)

_CHROME_ARGS = [
    "--headless=new",
    "--no-sandbox",
    "--disable-setuid-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-software-rasterizer",
    "--disable-blink-features=AutomationControlled",
    "--window-size=1280,900",
    # ── Renderer/tab-crash hardening for heavy SPA pages ──────────────────
    # NOTE: we deliberately KEEP Chrome multi-process (no --single-process /
    # no --no-zygote). Multi-process means a "tab crashed" kills only the
    # renderer, which _ensure_driver(force=True) + open_url retry can recover
    # from. --single-process turns the same failure into an unrecoverable
    # "session deleted as the browser..." whole-browser death — measurably
    # worse in testing. The crash rate is instead driven down by (a) starving
    # the renderer of GPU/WebGL/canvas memory and capping the JS heap, and
    # (b) the about:blank warm-up in _make_driver().
    "--disable-extensions",
    "--disable-background-networking",
    "--disable-renderer-backgrounding",
    "--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling",
    "--disable-ipc-flooding-protection",
    # WebGL/Accelerated* in the feature blocklist + the explicit GPU flags
    # below were the single biggest reduction in CMC renderer crashes.
    "--disable-features=Translate,TranslateUI,site-per-process,"
    "IsolateOrigins,BackForwardCache,OptimizationHints,"
    "WebGL,WebGL2,AcceleratedVideoDecode",
    "--disable-webgl",
    "--disable-webgl2",
    "--use-gl=swiftshader",
    "--disable-gpu-compositing",
    "--disable-accelerated-2d-canvas",
    "--js-flags=--max-old-space-size=512",   # cap renderer V8 heap → fewer OOM-y crashes
    "--mute-audio",
    "--disable-2d-canvas-clip-aa",
    "--disable-hang-monitor",
    "--disable-client-side-phishing-detection",
    "--disable-component-update",
    "--blink-settings=imagesEnabled=false",   # block images → huge renderer mem cut
    "--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
]


def _is_crash(exc: Exception) -> bool:
    """True if the exception indicates a dead renderer/tab/session."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _CRASH_MARKERS)


def _hard_kill_driver(driver) -> None:
    """Tear down a (possibly crashed) driver WITHOUT leaking processes.

    A plain driver.quit() is unreliable once the renderer has "tab crashed":
    chromedriver can fail to reap its child chromium (gpu-process / utility /
    renderer) siblings, leaving them spinning CPU forever. We observed 50
    orphaned chromium procs accumulate this way. So: try a graceful quit, then
    SIGKILL the whole chromedriver process group as a hard backstop.
    """
    if driver is None:
        return
    # Grab the chromedriver pid BEFORE quit() (quit clears service.process).
    drv_pid = None
    try:
        proc = getattr(getattr(driver, "service", None), "process", None)
        drv_pid = getattr(proc, "pid", None)
    except Exception:
        drv_pid = None

    try:
        driver.quit()
    except Exception:
        pass

    # Backstop: kill the chromedriver's process group. chromedriver launches
    # chromium in its own group, so this reaps every child renderer/gpu proc.
    if drv_pid:
        import signal
        for killer in (
            lambda: os.killpg(os.getpgid(drv_pid), signal.SIGKILL),
            lambda: os.kill(drv_pid, signal.SIGKILL),
        ):
            try:
                killer()
                break
            except (ProcessLookupError, PermissionError):
                break
            except Exception:
                continue


# ── Driver factory ────────────────────────────────────────────────────────

def _make_driver():
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service

    opts = Options()
    opts.binary_location = _CHROME_BIN
    # Don't block on full SPA load — return as soon as DOM is interactive.
    opts.page_load_strategy = "eager"
    for arg in _CHROME_ARGS:
        opts.add_argument(arg)
    opts.add_experimental_option("excludeSwitches", ["enable-automation"])
    opts.add_experimental_option("useAutomationExtension", False)
    # Belt-and-suspenders image blocking via content settings prefs.
    opts.add_experimental_option(
        "prefs",
        {
            "profile.managed_default_content_settings.images": 2,
            "profile.default_content_setting_values.notifications": 2,
        },
    )

    svc_env = dict(os.environ)
    svc_env["MALLOC_ARENA_MAX"] = "2"
    svc = Service(_CHROMEDRIVER, log_output="/dev/null", env=svc_env)
    driver = webdriver.Chrome(service=svc, options=opts)
    driver.set_page_load_timeout(_PAGE_TIMEOUT / 1000)
    driver.implicitly_wait(_IMPLICIT_WAIT)

    # Mask webdriver fingerprint
    try:
        driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
    except Exception:
        pass

    # Renderer warm-up. Empirically, the bulk of "tab crashed" failures on
    # heavy SPAs (coinmarketcap etc.) happen on the very FIRST navigation of a
    # cold renderer. Loading about:blank first warms the renderer and dropped
    # the measured crash rate from ~3/6 to ~1/6 (then the retry loop mops up
    # the rest). Cheap insurance, so always do it.
    try:
        driver.get("about:blank")
    except Exception:
        pass
    return driver


# ── Singleton browser session ─────────────────────────────────────────────

class BrowserAgent:
    _instance: BrowserAgent | None = None
    _lock = threading.Lock()

    def __init__(self):
        self._driver_lock = threading.Lock()
        self._driver = None
        self._last_used = 0.0
        self._watchdog: threading.Thread | None = None

    @classmethod
    def get(cls) -> BrowserAgent:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    def _driver_ok(self) -> bool:
        if self._driver is None:
            return False
        try:
            _ = self._driver.current_url
            return True
        except Exception:
            return False

    def _ensure_driver(self, force: bool = False):
        """Ensure a live driver exists. force=True tears down any existing
        (possibly crashed) driver and spawns a fresh one."""
        if force or self._driver is None or not self._driver_ok():
            log.info("(Re)starting headless Chromium...")
            _hard_kill_driver(self._driver)   # reap old/crashed proc tree, no leak
            self._driver = None
            self._driver = _make_driver()
            log.info("Chromium ready.")
        self._touch()

    def _touch(self) -> None:
        """Mark activity and make sure the idle watchdog is running."""
        self._last_used = time.time()
        if self._watchdog is None or not self._watchdog.is_alive():
            self._watchdog = threading.Thread(
                target=self._idle_reaper, name="browser-idle-reaper", daemon=True
            )
            self._watchdog.start()

    def _idle_reaper(self) -> None:
        """Auto-close the resident browser after _IDLE_TIMEOUT of inactivity, so
        a warmed-up session left on a heavy SPA page never spins CPU forever."""
        while True:
            time.sleep(10)
            with self._driver_lock:
                if self._driver is None:
                    return  # nothing to watch; exit thread
                idle = time.time() - self._last_used
                if idle >= _IDLE_TIMEOUT:
                    log.info("Browser idle %.0fs ≥ %ds — closing to free CPU.", idle, _IDLE_TIMEOUT)
                    _hard_kill_driver(self._driver)
                    self._driver = None
                    return

    def _park(self) -> None:
        """Navigate the current driver to about:blank to release the previous
        (possibly heavy/JS-busy) page's renderer work between calls. Best-effort."""
        try:
            if self._driver is not None:
                self._driver.get("about:blank")
        except Exception:
            pass

    # ── Navigation ────────────────────────────────────────────────────

    def open_url(self, url: str, wait_for: str | None = None) -> str:
        """Navigate to URL. Returns clean extracted text.

        Heavy SPA pages can crash the Chrome renderer ("tab crashed"). On such
        a crash we tear down the (now-dead) driver, spin up a fresh warmed-up
        one, and retry. We allow up to _MAX_ATTEMPTS tries: with a per-attempt
        crash rate of ~1/6 that drives the effective failure rate to well under
        1%.
        """
        with self._driver_lock:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                # force a fresh driver on every retry (attempt > 1)
                self._ensure_driver(force=(attempt > 1))
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
                    text = self._extract_text()
                    self._park()  # release the heavy page's renderer → no idle CPU
                    return text
                except Exception as exc:
                    if attempt < _MAX_ATTEMPTS and _is_crash(exc):
                        log.warning(
                            "open_url(%s) renderer crash (attempt %d/%d: %s) — "
                            "retrying with fresh driver",
                            url, attempt, _MAX_ATTEMPTS, str(exc).splitlines()[0],
                        )
                        continue
                    log.warning("open_url(%s) failed: %s", url, exc)
                    # Terminal failure: reap the crashed/dead proc tree NOW so a
                    # spinning renderer doesn't linger until idle-reaper/quit.
                    _hard_kill_driver(self._driver)
                    self._driver = None
                    return ""
            return ""

    def fetch(self, url: str, want_screenshot: bool = False) -> dict:
        """Navigate and atomically return {text, title, png} while the page is
        still loaded, THEN park to about:blank. Use this instead of
        open_url()+get_title()+screenshot(), which would see the parked blank
        page. Same crash-retry semantics as open_url()."""
        with self._driver_lock:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                self._ensure_driver(force=(attempt > 1))
                try:
                    self._driver.get(url)
                    time.sleep(1.5)
                    title = ""
                    try:
                        title = self._driver.title
                    except Exception:
                        pass
                    text = self._extract_text()
                    png = b""
                    if want_screenshot:
                        try:
                            png = self._driver.get_screenshot_as_png()
                        except Exception:
                            png = b""
                    self._park()
                    return {"text": text, "title": title, "png": png}
                except Exception as exc:
                    if attempt < _MAX_ATTEMPTS and _is_crash(exc):
                        log.warning("fetch(%s) crash (attempt %d/%d) — retrying",
                                    url, attempt, _MAX_ATTEMPTS)
                        continue
                    log.warning("fetch(%s) failed: %s", url, exc)
                    _hard_kill_driver(self._driver)
                    self._driver = None
                    return {"text": "", "title": "", "png": b""}
            return {"text": "", "title": "", "png": b""}

    def _extract_text(self) -> str:
        """Extract clean text from current page HTML via trafilatura.

        Re-raises renderer-crash exceptions so callers (open_url) can retry on
        a fresh driver instead of silently returning empty text.
        """
        html = ""
        try:
            html = self._driver.page_source
        except Exception as exc:
            if _is_crash(exc):
                raise
        try:
            import trafilatura
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
            if text:
                return text[:4000]
        except Exception:
            pass
        # Fallback: strip tags from body text
        try:
            body = self._driver.find_element("tag name", "body").text
            return body[:4000]
        except Exception as exc:
            if _is_crash(exc):
                raise
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

        Like open_url(), a renderer "tab crashed" on the results page is
        recovered by recreating a fresh driver and retrying once.
        """
        with self._driver_lock:
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                self._ensure_driver(force=(attempt > 1))
                try:
                    results = self._google_search_once(query, num)
                    self._park()  # release the results page → no idle CPU
                    return results
                except Exception as exc:
                    if attempt < _MAX_ATTEMPTS and _is_crash(exc):
                        log.warning(
                            "google_search('%s') renderer crash (attempt %d/%d: %s) "
                            "— retrying with fresh driver",
                            query, attempt, _MAX_ATTEMPTS, str(exc).splitlines()[0],
                        )
                        continue
                    log.error("google_search failed: %s", exc)
                    _hard_kill_driver(self._driver)   # reap crashed tree immediately
                    self._driver = None
                    return []
            return []

    def _google_search_once(self, query: str, num: int) -> list[dict]:
        from selenium.webdriver.common.by import By

        search_url = f"https://www.google.com/search?q={_encode(query)}&hl=en&num={num}"
        self._driver.get(search_url)
        time.sleep(2)

        results: list[dict] = []

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
                _hard_kill_driver(self._driver)   # reap full proc tree, no leak
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


# Last-resort cleanup: if the host process exits without anyone calling quit()
# (an unhandled exception in a caller, a killed worker, etc.), reap the
# chromium process tree on the way out so we never leak browsers that spin CPU.
import atexit


@atexit.register
def _reap_on_exit() -> None:
    inst = BrowserAgent._instance
    if inst is not None and inst._driver is not None:
        _hard_kill_driver(inst._driver)
        inst._driver = None
