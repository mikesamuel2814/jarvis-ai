"""Measure crash rate of multi-process configs over many fresh launches + warmup."""
import sys, time, os
sys.path.insert(0, "/home/kali/.jarvis")
os.environ.setdefault("MALLOC_ARENA_MAX", "2")
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

CHROME_BIN = "/usr/bin/chromium"
CHROMEDRIVER = "/usr/bin/chromedriver"
URL = "https://coinmarketcap.com/currencies/bitcoin/"

def make(extra):
    opts = Options()
    opts.binary_location = CHROME_BIN
    opts.page_load_strategy = "eager"
    base = ["--headless=new","--no-sandbox","--disable-setuid-sandbox",
            "--disable-dev-shm-usage","--disable-gpu","--disable-software-rasterizer",
            "--disable-blink-features=AutomationControlled","--window-size=1280,900"]
    for a in base + extra:
        opts.add_argument(a)
    opts.add_experimental_option("prefs",{"profile.managed_default_content_settings.images":2,
        "profile.default_content_setting_values.notifications":2})
    env = dict(os.environ); env["MALLOC_ARENA_MAX"]="2"
    svc = Service(CHROMEDRIVER, log_output="/dev/null", env=env)
    d = webdriver.Chrome(service=svc, options=opts)
    d.set_page_load_timeout(25)
    return d

# Multi-process, memory-bounded, warmup with about:blank first
EXTRA = [
    "--disable-extensions","--disable-background-networking",
    "--disable-renderer-backgrounding","--disable-backgrounding-occluded-windows",
    "--disable-background-timer-throttling","--disable-ipc-flooding-protection",
    "--disable-features=Translate,TranslateUI,site-per-process,IsolateOrigins,BackForwardCache,OptimizationHints,WebGL,WebGL2,AcceleratedVideoDecode",
    "--mute-audio","--disable-hang-monitor","--disable-client-side-phishing-detection",
    "--disable-component-update","--blink-settings=imagesEnabled=false",
    "--disable-accelerated-2d-canvas","--disable-webgl","--disable-webgl2",
    "--use-gl=swiftshader","--disable-gpu-compositing",
    "--js-flags=--max-old-space-size=512","--disable-dev-tools",
]
WARMUP = "--warmup" in sys.argv

N = 6
crashes = 0
for i in range(N):
    d = make(EXTRA)
    try:
        if WARMUP:
            d.get("about:blank"); time.sleep(0.3)
        d.get(URL); time.sleep(1.5)
        src = d.page_source
        print(f"run{i+1}: OK src={len(src)}")
    except Exception as e:
        m = str(e).split(chr(10))[0]
        if "crash" in m.lower() or "session" in m.lower(): crashes += 1
        print(f"run{i+1}: FAIL {m[:70]}")
    finally:
        try: d.quit()
        except Exception: pass
print(f"RESULT warmup={WARMUP}: crashes={crashes}/{N}")
