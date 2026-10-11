"""The builder: one command, a brief in, a phone agent on Freya out.

    python3 builder/build.py <brief.md> [<more briefs>...] <tool_api.json> [--slot NAME] [--practice A|B] [--no-review]

Steps (all logged to traces/<slot>-<time>.log):
  1. Claude reads the brief(s) and the tool API and fills a spec (extract.py).
  2. The spec is checked locally: enums against the tool API, generated code run on synthetic accounts.
     Problems go back to Claude for a repair (at most two rounds).
  3. Claude reviews the spec against the brief once more, looking for rules that were dropped or misplaced.
  4. The workflow is generated (generate.py), validated by Freya (dry run), saved and published.
  5. Smoke test: a short chat with the published agent.
  6. Optional (--practice A|B): the practice customers from the kit, scored.
  7. Export to submission/<slot>.workflow.json.
"""
import argparse
import json
import os
import pathlib
import sys
import time
import traceback

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

def load_dotenv(path=ROOT / ".env"):
    """Read KEY=VALUE lines from .env into the environment, without overriding variables already set."""
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                v = v.split(" #")[0].strip().strip('"').strip("'")
                os.environ.setdefault(k.strip().removeprefix("export ").strip(), v)


load_dotenv()

import extract  # noqa: E402
import generate  # noqa: E402
import llm  # noqa: E402
import selftest  # noqa: E402


def harden_freya(freya, trace):
    """Freya staging returns 503/504 under load. Retry transient failures with backoff instead of failing the build."""
    import urllib.error
    orig = freya.api
    transient = ("-> 429", "-> 500", "-> 502", "-> 503", "-> 504", "temporarily", "timed out", "Connection reset")

    def api(method, path, body=None, **params):
        for i in range(6):
            try:
                return orig(method, path, body, **params)
            except (RuntimeError, urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if i == 5 or not (isinstance(e, (urllib.error.URLError, TimeoutError, ConnectionError)) or any(t in str(e) for t in transient)):
                    raise
                wait = 5 * (i + 1)
                trace.log(f"Freya {method} {path} failed ({str(e)[:120]}); retry {i + 1}/5 in {wait} s")
                time.sleep(wait)
    freya.api = api
    return freya


class Trace:
    def __init__(self, slot):
        (ROOT / "traces").mkdir(exist_ok=True)
        self.path = ROOT / "traces" / f"{slot}-{time.strftime('%Y%m%d-%H%M%S')}.log"
        self.f = self.path.open("w")
        self.t0 = time.time()

    def log(self, msg):
        line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')} +{time.time() - self.t0:6.0f}s] {msg}"
        print(line, flush=True)
        self.f.write(line + "\n"); self.f.flush()


REVIEW_SYSTEM = extract.SPEC_GUIDE + r'''

You are now the REVIEWER. You get the brief(s), the tool API and a spec another reader filled. Check it rule by rule
against the brief: every numbered or implied rule must be represented in the right place, with the right values,
and nothing may contradict the brief. Pay special attention to: memos that override earlier rules, the opening
line, identity rules and counts, callback window, holidays and deadline expressions, floors, persuasion ladder,
exact required sentences, closing outcome/reason for every situation, multi-product and note rules, transfers.
Then return the CORRECTED full spec as one ```json block (the complete object, not a diff). Before the block,
list the corrections you made in at most 15 short lines (or "no corrections").'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+", help="brief file(s) then the tool API json")
    ap.add_argument("--slot")
    ap.add_argument("--practice", choices=["A", "B"])
    ap.add_argument("--reps", type=int, default=1)
    ap.add_argument("--no-review", action="store_true")
    ap.add_argument("--spec", help="reuse a spec json instead of extracting (development)")
    ap.add_argument("--offline", action="store_true", help="do not touch Freya (development)")
    a = ap.parse_args()

    briefs = [f for f in a.files if not f.endswith(".json")]
    apis = [f for f in a.files if f.endswith(".json")]
    if not briefs or len(apis) != 1:
        sys.exit("give one or more brief files and exactly one tool API .json file")
    slot = a.slot or pathlib.Path(briefs[-1]).stem
    tr = Trace(slot)
    tr.log(f"BUILD START slot={slot} briefs={briefs} tool_api={apis[0]} builder_model={llm.MODEL}")
    status = "failed"
    try:
        openapi_text = pathlib.Path(apis[0]).read_text()
        openapi = json.loads(openapi_text)
        brief_texts = [(pathlib.Path(b).name, pathlib.Path(b).read_text()) for b in briefs]
        tr.log(f"read {len(brief_texts)} brief(s), {sum(len(t) for _, t in brief_texts)} chars; tool API with {len(openapi.get('paths', {}))} paths")

        check = lambda s: selftest.check_code(s, openapi)
        if a.spec:
            spec = json.loads(pathlib.Path(a.spec).read_text())
            tr.log(f"spec loaded from {a.spec}")
        else:
            user = extract.extract_prompt(brief_texts, openapi_text)
            try:
                text = llm.ask(tr, "read the brief and tool API, fill the agent spec", extract.SPEC_GUIDE, user, limit_s=480)
            except llm.CallTimeout:
                text = llm.ask(tr, "read the brief and tool API, fill the agent spec (retry, faster)", extract.SPEC_GUIDE, user,
                               effort="medium", limit_s=480)
            spec = extract.parse_json(text)
            spec = repair_loop(tr, spec, openapi, check, user, rounds=2)
            (ROOT / "out").mkdir(exist_ok=True)
            (ROOT / "out" / f"{slot}.spec.first.json").write_text(json.dumps(spec, indent=1))
            budget_left = 17 * 60 - (time.time() - tr.t0)
            if a.no_review or budget_left < 240:
                tr.log(f"review skipped ({'--no-review' if a.no_review else f'only {budget_left:.0f} s of the model budget left'})")
            else:
                try:
                    rv = llm.ask(tr, "review the spec against the brief rule by rule and correct it", REVIEW_SYSTEM,
                                 user + "\n\n=== SPEC TO REVIEW ===\n```json\n" + json.dumps(spec, indent=1) + "\n```",
                                 limit_s=int(min(420, budget_left)))
                except llm.CallTimeout:
                    rv = None
                    tr.log("review took too long; keeping the checked spec")
            if not a.no_review and budget_left >= 240 and rv:
                notes = rv.split("```json")[0].strip()
                tr.log("review corrections:\n    " + "\n    ".join(notes.splitlines()[:20]))
                try:
                    spec2 = extract.parse_json(rv)
                    spec2 = repair_loop(tr, spec2, openapi, check, user, rounds=2)
                    if not extract.validate(spec2, openapi, check):
                        spec = spec2
                    else:
                        tr.log("reviewed spec still has problems; keeping the first valid spec")
                except Exception as e:
                    tr.log(f"review output unusable ({e}); keeping the first spec")
        (ROOT / "out").mkdir(exist_ok=True)
        (ROOT / "out" / f"{slot}.spec.json").write_text(json.dumps(spec, indent=1))
        errs = extract.validate(spec, openapi, check)
        if errs:
            tr.log("WARNING: spec problems remain: " + " | ".join(errs[:6]))
        tr.log(f"spec source notes: {spec['meta'].get('source_notes', '')}")

        wf, code = generate.build(spec, openapi)
        for nm, src in code.items():
            compile(src, nm, "exec")  # never upload in-call code that cannot run
        hook_errs = selftest.hook_smoke(wf)
        tr.log("local hook test: " + ("ok" if not hook_errs else " | ".join(hook_errs)))
        (ROOT / "out" / f"{slot}.workflow.json").write_text(json.dumps(wf, indent=1))
        info = generate.summary(wf)
        tr.log(f"workflow generated: {info['steps']} steps, tools {info['tools']}")

        if a.offline:
            tr.log("offline: not uploading"); status = "ok (offline)"; return
        missing = [k for k in ("FREYA_API_KEY", "FREYA_API_URL") if not os.environ.get(k)]
        if missing:
            raise RuntimeError(f"environment variables missing: {missing} (source env.sh first)")
        import freya
        harden_freya(freya, tr)
        try:
            freya.validate_workflow(f"{slot} check", wf)
            tr.log("Freya dry-run validation: ok")
        except Exception as e:
            tr.log(f"Freya dry-run validation FAILED: {e}")
            raise
        saved = freya.save_workflow(slot, wf, state_file=str(ROOT / ".freya-agents.json"))
        if "version" not in saved:
            # a fresh create gets the platform's default agent_config; one update sets the event model and publishes
            saved["version"] = freya.update_workflow(saved["workflow_id"], wf)
        stored = freya.api("GET", f"/api/v2/workflow/{saved['workflow_id']}", workspaceId=freya.workspace_id())["workflow"]["workflow"]
        llm_cfg = (stored.get("agent_config") or {}).get("llmConfig") or {}
        tr.log(f"agent model on Freya: {llm_cfg.get('provider')}/{llm_cfg.get('model')} (event model {freya.AGENT_LLM['provider']}/{freya.AGENT_LLM['model']})")
        lost = [k for k in wf["nodes"] if k not in (stored.get("nodes") or {})]
        if lost:
            tr.log(f"WARNING: nodes missing after save: {lost}")
        tr.log(f"CREATED/UPDATED on Freya: workflow_id={saved['workflow_id']} agent_id={saved['agent_id']} version={saved.get('version', 1)} steps={info['steps']} tools={len(info['tools'])}")

        smoke(tr, freya, saved["workflow_id"], spec)

        if a.practice:
            run_practice(tr, saved["agent_id"], a.practice, a.reps, slot)

        (ROOT / "submission").mkdir(exist_ok=True)
        out = freya.export_workflow(saved["workflow_id"], str(ROOT / "submission" / f"{slot}.workflow.json"))
        tr.log(f"exported to {out}")
        status = "ok"
    except Exception as e:
        tr.log(f"ERROR: {type(e).__name__}: {e}\n{traceback.format_exc()}")
        gen = ROOT / "out" / f"{slot}.workflow.json"
        sub = ROOT / "submission" / f"{slot}.workflow.json"
        if gen.exists() and not sub.exists():
            sub.parent.mkdir(exist_ok=True)
            sub.write_text(json.dumps({"workflow_id": None, "agent_id": None, "updated_at": None,
                                       "note": "Freya was not reachable during this build; this is the generated workflow, not an export.",
                                       "workflow": json.loads(gen.read_text())}, indent=1))
            tr.log(f"fallback: wrote the generated workflow to {sub} (NOT an export from Freya; rerun when Freya works)")
        raise
    finally:
        tr.log(f"BUILD END status={status} total={time.time() - tr.t0:.0f} s trace={tr.path}")


def repair_loop(tr, spec, openapi, check, user, rounds):
    for i in range(rounds + 1):
        errs = extract.validate(spec, openapi, check)
        tr.log(f"spec check {i + 1}: " + ("ok" if not errs else f"{len(errs)} problem(s): " + " | ".join(e[:160] for e in errs[:6])))
        if not errs or i == rounds:
            return spec
        if time.time() - tr.t0 > 20 * 60:
            tr.log("no time left for a repair round"); return spec
        try:
            text = _repair_call(tr, errs, spec, user)
        except llm.CallTimeout:
            tr.log("repair took too long; keeping the spec as it is"); return spec
        spec = extract.parse_json(text)
    return spec


def _repair_call(tr, errs, spec, user):
    return llm.ask(tr, f"repair {len(errs)} spec problem(s) found by local checks", extract.SPEC_GUIDE,
                       user + "\n\n=== YOUR PREVIOUS SPEC ===\n```json\n" + json.dumps(spec, indent=1) +
                       "\n```\n\n=== PROBLEMS FOUND BY THE CHECKER ===\n- " + "\n- ".join(errs) +
                       "\n\nFix every problem and return the complete corrected spec in one ```json block.", effort="medium", limit_s=360)


def smoke(tr, freya, workflow_id, spec):
    """One short chat. A workflow that cannot start a call fails here, in seconds."""
    vars_ = {v: "" for v in spec.get("call_variables") or []}
    vars_.update({k: v for k, v in {"customer_id": "c_smoke", "first_name": "Alex", "last_name": "Morgan", "title": "Mr", "now": "2026-10-14T11:00"}.items() if k in vars_ or k in ("customer_id", "now")})
    try:
        r = freya.api("POST", "/api/v2/chat/message", {"workflowId": workflow_id, "message": "Hello?", "customerVariables": vars_})
        tr.log(f"smoke test chat turn 1: agent said: {str(r.get('response'))[:300]!r}")
        cid = r.get("callId")
        if cid:
            r2 = freya.api("POST", "/api/v2/chat/message", {"workflowId": workflow_id, "callId": cid, "message": "Who is this?", "customerVariables": vars_})
            tr.log(f"smoke test chat turn 2 ('Who is this?'): agent said: {str(r2.get('response'))[:300]!r}")
            try:
                freya.api("POST", "/api/v2/chat/end", {"callId": cid})
            except Exception:
                pass
        tr.log("smoke test: the agent starts and answers")
    except Exception as e:
        tr.log(f"smoke test could not run: {str(e)[:300]}")


def run_practice(tr, agent_id, brief, reps, slot):
    import subprocess
    cmd = [sys.executable, str(ROOT / "tools" / "run_cases.py"), agent_id, "--brief", brief, "--reps", str(reps),
           "--out", str(ROOT / "results" / f"{slot}-{time.strftime('%H%M%S')}.json")]
    tr.log("TEST: practice customers: " + " ".join(cmd[1:]))
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    for line in (p.stdout + p.stderr).splitlines():
        tr.log("  " + line)


if __name__ == "__main__":
    main()
