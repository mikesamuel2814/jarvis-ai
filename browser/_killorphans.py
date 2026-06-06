"""Kill leaked chromium/chromedriver from test runs. Does NOT touch any
non-chromium process. Lists what it kills."""
import subprocess, os, signal

out = subprocess.run(["ps","-eo","pid,args"], capture_output=True, text=True).stdout
mypid = os.getpid()
targets = []
for l in out.splitlines()[1:]:
    pid_s, _, args = l.strip().partition(" ")
    if not pid_s.isdigit():
        continue
    pid = int(pid_s)
    if pid == mypid:
        continue
    low = args.lower()
    if ("chromium" in low or "chromedriver" in low) and "_killorphans" not in low:
        targets.append((pid, args[:70]))

print(f"found {len(targets)} chromium/chromedriver procs")
# Kill chromedriver + main browser processes; children die with them.
killed = 0
for pid, args in targets:
    try:
        os.kill(pid, signal.SIGKILL)
        killed += 1
    except ProcessLookupError:
        pass
    except Exception as e:
        print(f"  could not kill {pid}: {e}")
print(f"killed {killed}")

# verify
out2 = subprocess.run(["ps","-eo","pid,args"], capture_output=True, text=True).stdout
remain = [l for l in out2.splitlines() if ("chromium" in l.lower() or "chromedriver" in l.lower()) and "_killorphans" not in l.lower()]
print(f"remaining: {len(remain)}")
