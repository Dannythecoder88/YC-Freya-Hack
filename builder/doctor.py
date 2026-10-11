"""Check that everything the builder needs is reachable. Run: source env.sh && python3 builder/doctor.py"""
import os, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "builder"))
from build import load_dotenv  # noqa: E402
load_dotenv()
ok = True
def check(name, fn):
    global ok
    try:
        print(f"OK    {name}: {fn()}")
    except Exception as e:
        ok = False
        print(f"FAIL  {name}: {str(e)[:300]}")
for k in ("ANTHROPIC_API_KEY", "FREYA_API_KEY", "FREYA_API_URL", "FREYA_BANK_TEAM_KEY"):
    check(f"env {k}", lambda k=k: "set" if os.environ.get(k) else (_ for _ in ()).throw(RuntimeError("not set")))
def anthropic_ping():
    import anthropic
    r = anthropic.Anthropic().messages.create(model="claude-haiku-4-5", max_tokens=5, messages=[{"role": "user", "content": "ping"}])
    return "reachable"
check("Anthropic API", anthropic_ping)
def freya_ws():
    import freya
    return "workspace " + freya.workspace_id()
check("Freya API key", freya_ws)
def bank():
    from score import http
    r = http("/v1/practice/sessions", {"case": "PUB-01"})
    if not r.get("ok"): raise RuntimeError(r)
    return "practice session opened"
check("Practice bank (team key)", bank)
print("\nall good" if ok else "\nsomething is missing: fix the FAIL lines above")
sys.exit(0 if ok else 1)
