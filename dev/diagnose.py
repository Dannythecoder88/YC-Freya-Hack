"""Dev only: print compact transcripts of the failed (or all) calls of a run_cases.py --out file.

    python3 dev/diagnose.py results/run.json [--all] [--only PUB-04]
"""
import json
import sys
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import freya  # noqa: E402


def show(call_id):
    call = freya.get_call(call_id)
    print(f"  ended: {call.get('endedReason')}  duration: {call.get('duration')}")
    for m in call.get("messages", []):
        role, msg = m.get("role"), m.get("message") or ""
        t = m.get("secondsFromStart")
        if role in ("assistant", "user"):
            print(f"  {t:>6.1f} {role[:4].upper()} [{m.get('nodeId', '')}] {msg}")
        elif role == "system":
            try:
                ev = json.loads(msg)
            except Exception:
                continue
            e = ev.get("event")
            if e == "function_call_start":
                body = (ev.get("args") or {}).get("body", ev.get("args"))
                print(f"  {t:>6.1f}   -> {ev.get('function')} {json.dumps(body)[:300]}")
            elif e == "function_call_complete":
                print(f"  {t:>6.1f}   <- {ev.get('function')} {json.dumps(ev.get('result'))[:400]}")
            elif e == "node_enter":
                print(f"  {t:>6.1f}   == node {ev.get('from_node')} -> {ev.get('node')} ({ev.get('reason')})")
            elif e == "error":
                print(f"  {t:>6.1f}   !! {ev.get('message')} {str(ev.get('details'))[:300]}")


def main():
    data = json.load(open(sys.argv[1]))
    only = sys.argv[sys.argv.index("--only") + 1].split(",") if "--only" in sys.argv else None
    for r in data["rows"]:
        if only and r["id"] not in only:
            continue
        if r["pass"] and "--all" not in sys.argv and not only:
            continue
        print(f"\n===== {r['id']} {'PASS' if r['pass'] else 'FAIL'} tools={r['tools']}")
        for f in r["failed"]:
            print("  FAIL:", f)
        if r.get("call_id"):
            show(r["call_id"])


if __name__ == "__main__":
    main()
