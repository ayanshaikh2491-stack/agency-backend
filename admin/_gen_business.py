"""Generate a business image - moderate size for CPU testing."""
import time, os, json, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from admin.tools.kaggle_tools import generate_image_kaggle, _run_kaggle_cmd

result = generate_image_kaggle(
    prompt="Professional modern Instagram business post with gradient background, abstract geometric shapes, space for text, corporate style, clean aesthetic, marketing",
    width=512, height=512, steps=8,
)
print(f"Status: {result.get('status')}")
if result.get("status") != "submitted":
    print(f"ERROR: {result}")
    exit(1)
slug = result["kernel_slug"]
print(f"URL: https://www.kaggle.com/code/{slug}")

t0 = time.time()
while time.time() - t0 < 600:
    r = _run_kaggle_cmd(["kernels", "status", slug], timeout=30)
    st = r.get("stdout", "").lower()
    el = int(time.time() - t0)
    print(f"  [{el}s] {r.get('stdout','')[:80].strip()}")
    if "complete" in st:
        print("COMPLETE!")
        break
    if "error" in st or "fail" in st:
        print("ERROR!")
        break
    time.sleep(15)
else:
    print("TIMEOUT")
    exit(1)

os.makedirs(r'C:\Users\TAUSHEF\Downloads\int\outputs\business_post', exist_ok=True)
r2 = _run_kaggle_cmd(["kernels", "output", slug, "-p", r'C:\Users\TAUSHEF\Downloads\int\outputs\business_post'], timeout=60)
files = os.listdir(r'C:\Users\TAUSHEF\Downloads\int\outputs\business_post')
for f in files:
    fp = os.path.join(r'C:\Users\TAUSHEF\Downloads\int\outputs\business_post', f)
    print(f"  {f} ({os.path.getsize(fp)} bytes)")
    if f.endswith(('.png', '.jpg')):
        print(f"  >>> IMAGE READY: {fp}")
