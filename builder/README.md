# The builder

One command, the bank's documents in, a phone agent on Freya out.

```bash
pip install -r requirements.txt                     # anthropic, pypdf, python-docx, openpyxl, pyyaml
cp env.template .env                                # ANTHROPIC_API_KEY, FREYA_API_KEY, FREYA_API_URL, FREYA_BANK_TEAM_KEY
python3 builder/doctor.py                           # checks keys and connectivity

# final format: a case folder (Markdown, Word, PDF, Excel, images) and the URL of the bank mock
python3 builder/build.py <case_folder> <mock_url> 2>&1 | tee traces/final.log

# practice format: brief file(s) and the tool API
python3 builder/build.py briefs/brief-a.md briefs/brief-b.md tools/openapi.json
```

Output: the workflow on Freya (created once per slot, then updated in place), `submission/<slot>.workflow.json`
(the export; slot `final` for a case folder), `out/<slot>.spec.json`, `out/<slot>.openapi.json`, and the trace
`traces/<slot>-<time>.log` (start/end time, every model call with model and purpose, checks, what was created, tests).
`sh final.sh <case_folder> <mock_url>` runs the build, commits the export and trace, and writes `submission.json`.

If the case folder has no machine-readable service contract, the builder asks Claude to write one (OpenAPI) from the
documents. Contract endpoints may have any names: the spec maps each to a role (verify identity, get account, record
promise, note, callback, transfer, close). All tool URLs point at `<mock_url>`, the only service the agent calls.

## The bank mock (`mock/`)

Not part of the builder. The rules ask each team to write a mock of the bank's services on Cloudflare Workers from
the case's service contract and test customers; `mock/` is that mock (one Durable Object per customer).
Deploy: `cd mock && npx wrangler deploy`. Health check: `GET <mock_url>/health`. Ledger: `GET <mock_url>/ledger/<customer_id>`.

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
