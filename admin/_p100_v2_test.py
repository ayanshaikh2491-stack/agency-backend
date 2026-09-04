"""Live test with P100 CPU fallback."""
import sys, os, json, time
sys.path.insert(0, r'C:\Users\TAUSHEF\Downloads\int')
from admin.tools.kaggle_tools import generate_image_kaggle, _run_kaggle_cmd

result = generate_image_kaggle(
    prompt="A cute golden retriever puppy on a sunny beach, high quality",
    width=256, height=256, steps=2,
)
print(f"Status: {result.get('status')}")

if result.get("status") != "submitted":
    print(f"ERROR: {result}")
    exit(1)

slug = result["kernel_slug"]
print(f"Slug: {slug}")

# Poll
t0 = time.time()
while time.time() - t0 < 600:
    r = _run_kaggle_cmd(["kernels", "status", slug], timeout=30)
    status = r.get("stdout", "").lower()
    elapsed = int(time.time() - t0)
    print(f"  [{elapsed}s] {r.get('stdout','')[:100].strip()}")
    if "complete" in status:
        print("COMPLETE!")
        break
    if "error" in status or "fail" in status:
        print("ERROR!")
        break
    time.sleep(15)
else:
    print("TIMEOUT")
    exit(1)

# Download
d = r'C:\Users\TAUSHEF\Downloads\int\outputs\p100_cpu_test'
os.makedirs(d, exist_ok=True)
r2 = _run_kaggle_cmd(["kernels", "output", slug, "-p", d], timeout=120)
print(f"Download success: {r2['success']}")
for f in os.listdir(d):
    fp = os.path.join(d, f)
    print(f"  {f} ({os.path.getsize(fp)} bytes)")
    if f.endswith(".log"):
        with open(fp, 'r', encoding='utf-8', errors='replace') as fh:
            log = fh.read()
        for line in log.split("\n"):
            if "stream_name" in line:
                try:
                    obj = json.loads(line.rstrip(","))
                    dt = obj.get("data", "")[:200]
                    if dt.strip():
                        print(f"    [{obj['stream_name']}] {dt}")
                except:
                    pass

print(f"\nDone in {int(time.time()-t0)}s")
