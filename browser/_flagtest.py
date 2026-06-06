"""Test which flag combos prevent first-attempt renderer crash on CMC."""
import sys, time
sys.path.insert(0, "/home/kali/.jarvis")
import os
os.environ.setdefault("MALLOC_ARENA_MAX", "2")

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

CHROME_BIN = "/usr/bin/chromium"
CHROMEDRIVER = "/usr/bin/chromedriver"
URL = "https://coinmarketcap.com/currencies/bitcoin/"

def make(extra_args, prefs):
    opts = Options()
    opts.binary_location = CHROME_BIN
    opts.page_load_strategy = "eager"
    base = [
        "--headless=new", "--no-sandbox", "--disable-setuid-sandbox",
        "--disable-dev-shm-usage", "--disable-gpu",
        "--disable-software-rasterizer",
        "--disable-blink-features=AutomationControlled",
        "--window-size=1280,900",
    ]
    for a in base + extra_args:
        opts.add_argument(a)
    if prefs:
        opts.add_experimental_option("prefs", prefs)
    env = dict(os.environ); env["MALLOC_ARENA_MAX"] = "2"
    svc = Service(CHROMEDRIVER, log_output="/dev/null", env=env)
    d = webdriver.Chrome(service=svc, options=opts)
    d.set_page_load_timeout(25)
    return d

def trial(name, extra_args, prefs, n=2):
    crashes = 0
    for i in range(n):
        d = make(extra_args, prefs)
        try:
            d.get(URL)
            time.sleep(1.5)
            _ = d.page_source
            ok = len(_) > 1000
            print(f"  [{name}] run{i+1}: OK src={len(_)}")
        except Exception as e:
            msg = str(e).split(chr(10))[0]
            if "tab crashed" in msg.lower():
                crashes += 1
            print(f"  [{name}] run{i+1}: FAIL {msg[:60]}")
        finally:
            try: d.quit()
            except Exception: pass
    return crashes

CFG = sys.argv[1] if len(sys.argv) > 1 else "current"

configs = {
 "current": ([
    "--no-zygote","--disable-extensions","--disable-background-networking",
    "--disable-renderer-backgrounding","--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling","--disable-ipc-flooding-protection",
    "--disable-features=Translate,TranslateUI,site-per-process,IsolateOrigins,BackForwardCache,OptimizationHints",
    "--mute-audio","--disable-2d-canvas-clip-aa","--disable-hang-monitor",
    "--disable-client-side-phishing-detection","--disable-component-update",
    "--blink-settings=imagesEnabled=false",
 ], {"profile.managed_default_content_settings.images":2,
     "profile.default_content_setting_values.notifications":2}),

 # No --no-zygote (zygote/no-sandbox combos can destabilize renderer)
 "nozyg_off": ([
    "--disable-extensions","--disable-background-networking",
    "--disable-renderer-backgrounding","--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling","--disable-ipc-flooding-protection",
    "--disable-features=Translate,TranslateUI,site-per-process,IsolateOrigins,BackForwardCache,OptimizationHints",
    "--mute-audio","--disable-hang-monitor",
    "--disable-client-side-phishing-detection","--disable-component-update",
    "--blink-settings=imagesEnabled=false","--single-process",
 ], {"profile.managed_default_content_settings.images":2}),

 # Limit renderer memory + disable GPU compositing fully, no single-process
 "memcap": ([
    "--disable-extensions","--disable-background-networking",
    "--disable-renderer-backgrounding","--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling","--disable-ipc-flooding-protection",
    "--disable-features=Translate,TranslateUI,site-per-process,IsolateOrigins,BackForwardCache,OptimizationHints,WebGL,WebGL2",
    "--mute-audio","--disable-hang-monitor",
    "--disable-client-side-phishing-detection","--disable-component-update",
    "--blink-settings=imagesEnabled=false",
    "--disable-accelerated-2d-canvas","--disable-gl-drawing-for-tests",
    "--js-flags=--max-old-space-size=512",
    "--disable-webgl","--disable-webgl2","--use-gl=swiftshader",
 ], {"profile.managed_default_content_settings.images":2}),
}

extra, prefs = configs[CFG]
c = trial(CFG, extra, prefs, n=3)
print(f"RESULT {CFG}: crashes={c}/3")
