"""Live FLUX test with P100 fix — submit, poll, download, verify."""
import sys, os, json, time
sys.path.insert(0, r'C:\Users\TAUSHEF\Downloads\int')
from admin.tools.kaggle_tools import generate_image_kaggle, _run_kaggle_cmd, check_notebook_status, download_notebook_output

# Step 1: Submit
print("Submitting FLUX image generation...")
result = generate_image_kaggle(
    prompt="A cute golden retriever puppy on a sunny beach, high quality",
    width=512, height=512, steps=4,
)
print(f"Status: {result.get('status')}")

if result.get("status") != "submitted":
    print(f"ERROR: {result}")
    exit(1)

slug = result["kernel_slug"]
print(f"URL: https://www.kaggle.com/code/{slug}")

# Step 2: Poll
print("Polling for completion...")
t0 = time.time()
while time.time() - t0 < 600:  # 10 min max
    r = _run_kaggle_cmd(["kernels", "status", slug], timeout=30)
    status_text = r.get("stdout", "").lower()
    elapsed = int(time.time() - t0)
    print(f"  [{elapsed}s] {r.get('stdout','')[:120].strip()}")
    
    if "complete" in status_text:
        print("COMPLETE!")
        break
    if "error" in status_text or "fail" in status_text:
        print("ERROR!")
        break
    time.sleep(15)
else:
    print("TIMEOUT")
    exit(1)

# Step 3: Download
print("Downloading output...")
d = r"C:\Users\TAUSHEF\Downloads\int\outputs\live_test_p100"
os.makedirs(d, exist_ok=True)
r2 = _run_kaggle_cmd(["kernels", "output", slug, "-p", d], timeout=120)
if r2["success"]:
    for f in os.listdir(d):
        fp = os.path.join(d, f)
        print(f"  {f} ({os.path.getsize(fp)} bytes)")
        if f.endswith(".log"):
            with open(fp, encoding="utf-8", errors="replace") as fh:
                log = fh.read()
            for line in log.split("\n"):
                if "stream_name" in line:
                    try:
                        obj = json.loads(line.rstrip(","))
                        if obj.get("stream_name") == "stdout":
                            txt = obj.get("data", "")[:200]
                            if txt.strip():
                                print(f"    OUT: {txt}")
                    except:
                        pass
        elif f.endswith((".png", ".jpg", ".jpeg")):
            print(f"  ✓ IMAGE FOUND: {fp}")
else:
    print(f"Download error: {r2}")

print(f"\nDone in {int(time.time()-t0)}s")
