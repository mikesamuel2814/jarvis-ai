import sys
sys.path.insert(0, "/home/kali/.jarvis")
from browser.agent import BrowserAgent

URLS = [
    "https://coinmarketcap.com/currencies/bitcoin/",
    "https://finance.yahoo.com/quote/BTC-USD/",
]
N = int(sys.argv[1]) if len(sys.argv) > 1 else 5
a = BrowserAgent.get()
fails = 0
for i in range(N):
    for u in URLS:
        t = a.open_url(u)
        ok = len(t) > 500
        if not ok:
            fails += 1
        print(f"iter{i+1} {u.split('/')[2]:22s} chars={len(t):5d} {'OK' if ok else 'FAIL'}")
a.quit()
print(f"TOTAL FAILS: {fails}/{N*len(URLS)}")
