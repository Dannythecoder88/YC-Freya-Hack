"""Run the generated in-workflow code locally, before upload. Catches crashes and impossible results."""
import datetime
import json
import traceback

import runtime
from generate import openapi_tools


def _exec(code, env):
    g = dict(env)
    exec(compile(code, "<snippet>", "exec"), g)
    return g


def synthetic_products(spec, openapi):
    api = openapi_tools(openapi)
    acct = spec["account"]
    item = {}
    try:
        r = api["get_account"]["responses"]["200"]["content"]["application/json"]["schema"]
        item = r["properties"][acct.get("products_key", "products")]["items"].get("properties", {})
    except Exception:
        pass
    types = list((acct.get("types") or {}).keys()) or ["product"]
    out = []
    lates = sorted({1, 3, 7, 8, 12, 20, 30, 45, 60, 90})
    for i, t in enumerate(types * 3):
        p = {}
        for f, fs in item.items():
            ty = fs.get("type")
            p[f] = 150.0 if ty == "number" else (7 if ty == "integer" else "1234")
        p[acct.get("type_field", "type")] = t
        p[acct.get("id_field", "product_id")] = f"p{i % 3 + 1}"
        p[acct.get("past_due_field", "past_due")] = 500.0 + i
        p[acct.get("days_late_field", "days_late")] = lates[i % len(lates)]
        out.append(p)
    return out


def check_code(spec, openapi=None, now_values=("2026-10-14T11:00", "2026-10-16T18:30", "2026-12-23T09:15", "2027-03-01T10:00")):
    """Returns a list of problems (empty = fine)."""
    import generate
    errs = []
    try:
        wf, code = generate.build(spec, openapi)
    except Exception as e:
        return [f"generate failed: {type(e).__name__}: {e}"]
    for nm, src in code.items():
        try:
            compile(src, nm, "exec")
        except SyntaxError as e:
            return [f"{nm} code does not compile: {e}"]
    prods = synthetic_products(spec, openapi)
    for now in now_values:
        try:
            g = _exec(code["setup"], {"vars": {"now": now}})
            v = g["vars"]
            if v.get("x_setup_state") != "ok":
                errs.append(f"setup failed for now={now}: {v.get('x_setup_state')}")
        except Exception as e:
            errs.append(f"setup crashed for now={now}: {e}")
        for k in range(0, len(prods), 3):
            group = prods[k:k + 3]
            for form in ("list", "json"):
                vv = {"now": now, "x_products": group if form == "list" else json.dumps(group)}
                try:
                    g = _exec(code["facts"], {"vars": vv})
                except Exception as e:
                    errs.append(f"facts crashed: {e}\n{traceback.format_exc()[-400:]}")
                    continue
                v = g["vars"]
                if v.get("x_facts_state") != "ok":
                    errs.append(f"facts error for now={now}, products={group}: {v.get('x_facts')}")
                    continue
                rules = json.loads(v["x_rules"])
                today = now[:10]
                for pid, r in rules.items():
                    if r["band"] != "none" and r["deadline"] < today:
                        errs.append(f"deadline {r['deadline']} before today {today} for {r}")
    # only report distinct problems
    seen, uniq = set(), []
    for e in errs:
        key = e[:120]
        if key not in seen:
            seen.add(key); uniq.append(e)
    return uniq[:12]


def hook_smoke(wf):
    """Run every hook with typical results so a NameError shows up now and not on a call."""
    errs = []
    samples = [
        {"status": "success", "status_code": 200, "data": {"ok": True, "verified": True, "wrong_answers_left": 3, "all_verified": False}, "header": {}},
        {"status": "success", "status_code": 200, "data": {"ok": True, "verified": False, "wrong_answers_left": 0}, "header": {}},
        {"status": "error", "status_code": 500, "data": "Internal error", "header": {}},
        {"status": "success", "status_code": 200, "data": {"ok": False, "error": "x"}, "header": {}},
    ]
    for nname, n in wf["nodes"].items():
        for t in (n.get("tools") or []):
            name = t["function"]["name"]
            body = {k: "x" for k in t["function"]["parameters"]["properties"]["body"]["properties"]}
            for hk in ("pre_process_code", "post_process_code"):
                src = t.get(hk)
                if not src:
                    continue
                for s in samples:
                    env = {"vars": {"x_rules": "{}", "x_last_field": "date_of_birth"}, "result": json.loads(json.dumps(s)),
                           "config": {"url": "u", "method": "POST", "headers": {}, "body": dict(body)}}
                    try:
                        _exec(src, env)
                    except Exception as e:
                        errs.append(f"{nname}/{name} {hk} raised {e}")
    return sorted(set(errs))
