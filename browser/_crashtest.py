import sys
sys.path.insert(0, "/home/kali/.jarvis")
from browser.agent import BrowserAgent

a = BrowserAgent.get()
url = sys.argv[1] if len(sys.argv) > 1 else "https://coinmarketcap.com/currencies/bitcoin/"
t = a.open_url(url)
print("scraped chars:", len(t))
print(repr(t[:200]))
a.quit()
print("DONE")
