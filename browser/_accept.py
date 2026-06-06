import sys
sys.path.insert(0, "/home/kali/.jarvis")
from browser.agent import BrowserAgent
a = BrowserAgent.get()
r = a.google_search('bitcoin price today', num=3)
print('results:', len(r))
for x in r: print(' ', x['title'][:50], x['url'][:50])
# Scrape the heaviest one (coinmarketcap if present, else first)
url = next((x['url'] for x in r if 'coinmarketcap' in x['url']), r[0]['url'])
t = a.open_url(url)
print('scraped chars:', len(t))
assert len(t) > 500, 'SCRAPE TOO SHORT — still failing'
a.quit()
print('PASS')
