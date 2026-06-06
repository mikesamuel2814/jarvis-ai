import subprocess
out = subprocess.run(["ps","-eo","pid,ppid,pcpu,pmem,etime,args"],
                     capture_output=True, text=True).stdout
lines = [l for l in out.splitlines() if "chrom" in l.lower() and "_psdiag" not in l]
lines.sort(key=lambda l: float(l.split()[2]), reverse=True)
print(f"chromium-related processes: {len(lines)}")
total_cpu = 0.0
for l in lines[:25]:
    f = l.split(None, 5)
    total_cpu += float(f[2])
    # shorten args
    args = f[5]
    tag = "?"
    for t in ("--type=renderer","--type=gpu-process","--type=utility","--type=zygote","chromedriver"):
        if t in args:
            tag = t; break
    if tag == "?" and "chromium" in args:
        tag = "main"
    print(f"pid={f[0]:>7} ppid={f[1]:>7} cpu={f[2]:>5} mem={f[3]:>4} up={f[4]:>9} {tag}")
print(f"sum %cpu (top25): {total_cpu:.1f}")
print("loadavg:", open("/proc/loadavg").read().strip())
