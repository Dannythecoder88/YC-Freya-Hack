"""Dev: tools/run_cases.py with Freya retries (staging returns 504 under load). Same arguments."""
import sys, pathlib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "tools"), str(ROOT / "builder")]
import freya
from build import harden_freya
class P:
    def log(self, m): print(m, flush=True)
harden_freya(freya, P())
import score, time as _t
_http = score.http
def _retry_http(path, body=None):
    for i in range(5):
        try: return _http(path, body)
        except Exception as e:
            if i == 4: raise
            print("bank read failed, retrying:", str(e)[:80], flush=True); _t.sleep(5 * (i + 1))
score.http = _retry_http
sys.argv[0] = str(ROOT / "tools" / "run_cases.py")
import runpy
runpy.run_path(str(ROOT / "tools" / "run_cases.py"), run_name="__main__")
