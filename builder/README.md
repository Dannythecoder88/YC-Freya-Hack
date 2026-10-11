# The builder

One command, a brief in, a phone agent on Freya out:

```bash
pip install -r requirements.txt
source env.sh        # FREYA_API_KEY, FREYA_API_URL, FREYA_BANK_TEAM_KEY, ANTHROPIC_API_KEY
python3 builder/build.py <brief.md> <tool_api.json>            # e.g. briefs/hidden-1.md briefs/hidden-1.openapi.json
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json --practice B   # several briefs, plus practice test
```

Output: the workflow on Freya (created once per slot, then updated in place), `submission/<slot>.workflow.json`
(the export), `out/<slot>.spec.json` and `out/<slot>.workflow.json` (what was generated), and the trace
`traces/<slot>-<time>.log` (start/end time, every model call with model and purpose, checks, what was created, tests).

Before a real run, check keys and connectivity: `python3 builder/doctor.py` (prints OK/FAIL per service).
A fresh machine needs a `.env` made from `env.template`. Freya calls retry on 429/5xx with backoff.

## How it works

1. **Read** (`extract.py`): Claude Opus 5.5 reads the brief(s) and the tool API and fills a JSON spec: texts and rules
   per call step, tool field names, enums, the outcome table, and date/amount rules written as small Python expressions
   (`add_bd(today, 7)`, `last_bd_this_or_next_week(today)`, ...). Raw material (mails, memos that change a policy,
   code lists) is resolved into one consistent spec.
2. **Check** (`selftest.py`, `extract.validate`): enums and field names are checked against the tool API; the generated
   in-call code runs locally on synthetic accounts and several call dates. Problems go back to Claude (≤ 2 rounds).
3. **Review**: a second Claude pass compares the spec with the brief rule by rule and corrects it.
4. **Generate** (`generate.py`, `runtime.py`): a fixed, tested workflow shape:
   `setup (code) → greet → identity → load_account (API) → facts (code) → negotiate`, plus `third_party` and
   deterministic close nodes (identity failed, callback booked, transfer done). Deadlines, floors, the persuasion
   ladder and a date table (weekends, holidays, per-product deadlines, "this/next Friday") are computed in code during
   the call, so the phone agent's model never does date arithmetic. Tool hooks track identity answers, refuse
   promises outside the brief's limits, count retries, and tell the model what to do next after every tool result.
5. **Publish** with `freya.save_workflow`, smoke-test with one chat turn, export.

Nothing in the builder is specific to one brief: every value comes from the brief and its tool API at build time.
