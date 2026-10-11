"""Spec (extracted from the brief) -> Freya workflow JSON.

The workflow shape is fixed and generic for this kind of call:

    setup (code) -> greet -> identity -> load_account (api) -> facts (code) -> negotiate
                      \\-> third_party
    deterministic closes: identity failed, callback booked, transfer done (api nodes that call close_call)
    model closes: close_call tool in every conversation node -> end

All brief-specific content comes from the spec: texts, rules, field names, enums, date and amount
expressions. Tool definitions come from the brief's own OpenAPI file.
"""
import copy
import json

import runtime

TOOL_NAMES = ["verify_identity", "get_account", "create_promise", "add_note", "schedule_callback", "transfer_to_agent", "close_call"]


# ---------------------------------------------------------------- OpenAPI -> tools
def openapi_tools(openapi):
    """name -> {url, props, required, summary}"""
    base = (openapi.get("servers") or [{}])[0].get("url", "").rstrip("/")
    out = {}
    for path, ops in (openapi.get("paths") or {}).items():
        for method, op in ops.items():
            name = path.strip("/").split("/")[-1]
            schema = (((op.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema") or {}
            schema = _resolve(schema, openapi)
            out[name] = {
                "url": base + path, "method": method.upper(), "summary": op.get("summary") or op.get("description") or "",
                "props": {k: _resolve(v, openapi) for k, v in (schema.get("properties") or {}).items()},
                "required": list(schema.get("required") or []),
                "responses": op.get("responses") or {},
            }
    return out


def _resolve(s, root):
    if isinstance(s, dict) and "$ref" in s:
        node = root
        for part in s["$ref"].lstrip("#/").split("/"):
            node = node.get(part, {})
        return _resolve(node, root)
    return s


def response_fields(api, name):
    """Top-level fields of the 200 response, for prompts and checks."""
    r = (api.get(name) or {}).get("responses", {}).get("200", {})
    sch = (((r.get("content") or {}).get("application/json") or {}).get("schema")) or {}
    return list((sch.get("properties") or {}).keys())


def make_tool(api, name, spec, call_vars, description, baked=None, timeout=25000, retries=0, hooks=(None, None), mapping=None):
    t = api[name]
    notes = (spec.get("tool_notes") or {}).get(name) or {}
    props = {}
    baked = dict(baked or {})
    for f, fs in t["props"].items():
        leaf = {"type": fs.get("type", "string")}
        if leaf["type"] not in ("string", "number", "integer", "boolean"):
            leaf["type"] = "string"
        if f not in baked and f not in call_vars and isinstance(fs.get("enum"), list) and len(fs["enum"]) == 1:
            baked[f] = fs["enum"][0]
        if f in call_vars or f in baked:
            leaf["default"] = baked.get(f, "{{" + f + "}}")
        else:
            desc = " ".join(x for x in [fs.get("description", ""), notes.get(f, "")] if x).strip()
            if fs.get("enum"):
                leaf["enum"] = list(fs["enum"])
                desc = (desc + " One of: " + ", ".join(map(str, fs["enum"])) + ".").strip()
            if fs.get("format") == "date" and "YYYY-MM-DD" not in desc:
                desc = (desc + " Format YYYY-MM-DD.").strip()
            leaf["description"] = desc or f
            leaf["default"] = "{{" + f + "}}"
        props[f] = leaf
    required = [f for f in t["required"] if f not in call_vars and f not in baked]
    tool = {
        "type": "function",
        "config": {"timeoutMs": timeout, "maxRetries": retries},
        "function": {
            "name": name,
            "handler": "__function__:api_call",
            "description": description,
            "parameters": {"type": "object", "properties": {
                "url": {"type": "string", "default": t["url"]},
                "method": {"type": "string", "default": t["method"]},
                "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                "body": {"type": "object", "properties": props, "required": required},
            }},
        },
    }
    pre, post = hooks
    if pre:
        runtime.assert_no_braces(pre, name + " pre hook"); tool["pre_process_code"] = pre
    if post:
        runtime.assert_no_braces(post, name + " post hook"); tool["post_process_code"] = post
    if mapping:
        tool["response_mapping"] = mapping
    return tool


# ---------------------------------------------------------------- condition helpers
def cond_any(*pairs):
    return {"logic": "or", "conditions": [{"variable": v, "operator": "equals", "operand": o} for v, o in pairs]}

def cond_true(*variables):
    pairs = []
    for v in variables:
        pairs += [(v, "true"), (v, "True")]
    return cond_any(*pairs)

def cond_gt(var, n):
    return {"logic": "or", "conditions": [{"variable": var, "operator": "greater_than", "operand": n}]}

def tr(target, desc, cond):
    return {"target_node": target, "description": desc, "condition": cond}

ALWAYS = {"operator": "always"}


def code_node(name, code, nxt):
    runtime.assert_no_braces(code, name)
    return {
        "name": name, "node_type": "code", "passthrough": True, "passthrough_default_transition": nxt,
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{"type": "function", "function": {"name": "code_exec", "handler": "__function__:code_exec",
                         "parameters": {"type": "object", "properties": {"code": {"type": "string", "default": code},
                                                                          "timeout_ms": {"type": "number", "default": 15000}}}}}],
        "transitions": [tr(nxt, "always", ALWAYS)],
    }


def bullet(rules):
    if isinstance(rules, str):
        return rules.strip()
    return "\n".join("- " + r.strip() for r in rules if r and r.strip())


# ---------------------------------------------------------------- the workflow
def build(spec, openapi):
    raw = openapi_tools(openapi)
    tmap = spec.get("tool_map") or {}
    api = {}
    for role in TOOL_NAMES:
        actual = tmap.get(role, role)
        if actual and actual in raw:
            api[role] = raw[actual]
    missing = [t for t in ("verify_identity", "get_account", "create_promise", "close_call") if t not in api]
    if missing:
        raise ValueError(f"tool API has no endpoint for {missing} (tool_map={tmap}, endpoints={list(raw)})")
    call_vars = list(spec.get("call_variables") or ["customer_id", "first_name", "last_name", "title", "now"])
    ident = spec["identity"]
    acct = spec["account"]
    dates = spec["dates"]
    prom = spec["promise"]
    closing = spec["closing"]
    lang = spec["meta"].get("language", "English")
    who = spec.get("customer_name") or ("{{title}} {{last_name}}" if "title" in call_vars else "{{first_name}} {{last_name}}")

    # ---------- configs for in-sandbox code
    code_cfg = {
        "holidays": dates.get("holidays") or [], "weekend": dates.get("weekend", [5, 6]),
        "next_weekday": dates.get("next_weekday", "following_calendar_week"),
        "earliest_date": dates.get("earliest_date") or "today",
        "bands": spec["bands"], "attempts": spec["attempts"], "floor_expr": acct["floor_expr"],
        "min_amount": spec.get("min_amount", "floor"),
        "products_key": acct.get("products_key", "products"), "id_field": acct.get("id_field", "product_id"),
        "type_field": acct.get("type_field", "type"), "past_due_field": acct.get("past_due_field", "past_due"),
        "days_late_field": acct.get("days_late_field", "days_late"), "types": acct.get("types") or {},
        "currency_word": spec["meta"].get("currency_word", ""),
        "statement_intro_single": acct.get("statement_intro_single", ""), "statement_intro_multi": acct.get("statement_intro_multi", ""),
        "statement_each": acct.get("statement_each", ""),
        "first_ask_single": acct.get("first_ask_single", ""), "first_ask_multi": acct.get("first_ask_multi", ""),
        "facts_fail_line": acct.get("facts_fail_line", ""),
        "floor_offer_note": (spec.get("floor_offer_note") or "").strip(),
        "callback": (spec.get("third_party") or {}).get("callback") or {},
    }
    setup = runtime.setup_code(code_cfg)
    facts = runtime.facts_code(code_cfg)

    # ---------- tools
    p = spec.get("params") or {}
    fparam, aparam = p.get("identity_field", "field"), p.get("identity_answer", "answer")
    pid_p, amt_p, date_p = p.get("promise_product", "product_id"), p.get("promise_amount", "amount"), p.get("promise_date", "date")
    out_p, rsn_p = p.get("close_outcome", "outcome"), p.get("close_reason", "reason")
    queue_p = p.get("transfer_queue", "queue")

    id_cfg = {
        "questions": ident["questions"], "field_param": fparam, "answer_param": aparam,
        "resp": ident.get("response") or {}, "hints": ident.get("hints") or {},
        "unreadable_text": ident.get("unreadable_text", ""), "all_by_count": not bool((ident.get("response") or {}).get("all_verified")),
        "ok_field": (ident.get("response") or {}).get("ok", "ok"),
    }
    id_pre, id_post = runtime.identity_hooks(id_cfg)
    q_texts = "; ".join(f"{q['field']}: {q.get('answer_format', '')}" for q in ident["questions"])
    t_verify = make_tool(api, "verify_identity", spec, call_vars,
        "Check ONE security answer. CALL it every time the customer gives a complete answer to a security question, with the matching field. "
        f"Answer formats: {q_texts}. Then follow what_to_do_next in the result.",
        hooks=(id_pre, id_post))

    pr_cfg = {"pid_param": pid_p, "amount_param": amt_p, "date_param": date_p, "guard": bool(spec.get("guard", True)),
              "holidays": dates.get("holidays") or [], "weekend": dates.get("weekend", [5, 6]),
              "retries": int(prom.get("retries", 1)), "ok_field": "ok",
              "ok_text": ("Now tell the customer it is recorded and say exactly: \"" + prom["recorded_sentence"] + "\"") if prom.get("recorded_sentence") else "Tell the customer it is recorded.",
              "final_fail_text": prom.get("final_fail_text", "")}
    pr_pre, pr_post = runtime.promise_hooks(pr_cfg)
    t_promise = make_tool(api, "create_promise", spec, call_vars,
        "Record ONE payment promise for ONE product. CALL it only after you read the promise back and the customer clearly said yes. "
        "product_id copied exactly from the account facts. amount as a number. date as YYYY-MM-DD from the date table. "
        "Then follow what_to_do_next in the result.", hooks=(pr_pre, pr_post))

    note_ok = "Note saved. Continue with the next step of the call."
    t_note = None if "add_note" not in api else make_tool(api, "add_note", spec, call_vars,
        "Record why ONE product ends without a promise. CALL it once per product that has no promise, as the rules say, before closing. "
        "product_id exactly as in the account facts.",
        hooks=(None, runtime.generic_post_hook(note_ok, "The note FAILED. Try once more.")))

    tq_pre = runtime._hook({"queue_param": queue_p}, "\ntry:\n    b = config.get('body') if isinstance(config, dict) else None\n    if isinstance(b, dict): vars['x_transfer_queue'] = str(b.get(CFG['queue_param']) or '')\nexcept Exception:\n    pass\n")
    tq_post = runtime._hook({"ok_field": "ok"}, "\ntry:\n    d = _data(result)\n    if d is not None:\n        if _failed(result, d):\n            d['what_to_do_next'] = 'The transfer FAILED. Tell the customer you could not transfer the call.'\n        else:\n            vars['x_transfer_done'] = str(vars.get('x_transfer_queue') or '')\n            d['what_to_do_next'] = 'Transferred.'\nexcept Exception:\n    pass\n")
    t_transfer = None if "transfer_to_agent" not in api else make_tool(api, "transfer_to_agent", spec, call_vars,
        "Hand the call to a person in the right queue. CALL it only when a rule says to transfer. The call ends after it.",
        hooks=(tq_pre, tq_post))

    cb_post = runtime._hook({"ok_field": "ok"}, "\ntry:\n    d = _data(result)\n    if d is not None:\n        if _failed(result, d):\n            d['what_to_do_next'] = 'The callback was NOT booked. Do not say it was. Ask for another time inside the allowed window, or close as the rules say.'\n        else:\n            vars['x_callback_done'] = 'yes'\n            d['what_to_do_next'] = 'Callback booked.'\nexcept Exception:\n    pass\n")
    t_callback = None if "schedule_callback" not in api else make_tool(api, "schedule_callback", spec, call_vars,
        "Book a callback. CALL it only for a day and time inside the allowed callback window. date YYYY-MM-DD, time HH:MM (24 hour).",
        hooks=(None, cb_post))

    close_cfg = {"outcome_param": out_p, "reason_param": rsn_p, "no_reason": closing.get("no_reason_outcomes") or []}
    close_post = runtime._hook({"ok_field": "ok"}, "\ntry:\n    d = _data(result)\n    if d is not None:\n        if _failed(result, d):\n            d['what_to_do_next'] = 'close_call failed. Call close_call again with the same values.'\n        else:\n            vars['x_closed'] = 'yes'\n            d['what_to_do_next'] = 'Call closed.'\nexcept Exception:\n    pass\n")
    table = "; ".join(f"{r['outcome']}" + (f"/{r['reason']}" if r.get("reason") else "") + f" = {r['when']}" for r in closing["table"])
    t_close = make_tool(api, "close_call", spec, call_vars,
        "End the call and record its outcome. It must be the LAST tool call of the call. In the SAME message, first thank the customer and say goodbye, "
        f"then call close_call. Outcome table: {table}.",
        hooks=(runtime.close_pre_hook(close_cfg), close_post))

    t_account = make_tool(api, "get_account", spec, call_vars, "Get the late products.", retries=2,
                          hooks=(None, runtime.generic_post_hook("Account loaded.", "The account could not be loaded.")))

    # ---------- prompts
    sys_rules = bullet(spec.get("system_rules") or [])
    cur = (spec["meta"].get("currency_word") or "").strip()
    money_rule = (f'Say every amount followed by "{cur}" (for example "250 {cur}"); never use another currency.' if cur
                  else "Say amounts as plain numbers (for example \"250\"); never add a currency name.")
    system_prompt = f"""You are {spec['meta']['agent_self_description']}, making an outbound phone call. Speak {lang} only.
Everything you say must be in {lang}, even though these instructions and the account facts are written in English: translate dates, amounts and any wording from the facts into natural {lang}. Keep sentences that the rules quote in {lang} exactly as written.
You are talking with a person on the phone. Write only the words you say: no lists, no markdown, no brackets, no stage directions.
Keep every turn to one to three short sentences. Ask one question at a time. {money_rule} Say dates as weekday, day and month, in {lang}.
The current date and time of this call is {{{{now}}}} ({{{{x_today_text}}}}). Use it for every date; never use your own clock.
The person you must reach is {who}.

How you use tools:
- When a rule says to call a tool, call it. Saying that you did something is not the same as doing it.
- Call one tool per message. After every tool result, read its what_to_do_next field and follow it, then speak. Never end a turn silently.
- Never say that something was recorded, booked or transferred unless the tool result shows it worked.
- Never invent amounts, dates, reasons or facts. If you do not know something, say you do not have that information.

How people talk on the phone:
- People often say one answer across two or more messages: a date as "9 September" and then "1990", digits as "46" and then "51", an amount as "four hundred" and then "and twenty". Keep every piece already said and combine the pieces in the order given. Never throw a piece away.
- If what was said so far is incomplete, ask only for the missing part in a few words in {lang} (like "And the year?" or "And the other two digits?"). Never ask them to repeat the whole answer and never say you need the full answer.
- If a message stops in the middle of a sentence or a number, say only a short "go on" in {lang} and wait for the rest.
- If they correct themselves ("the 21st, sorry, I mean the 20th"), use the corrected value. Numbers and dates said in words count the same as digits.
- If they talk about something unrelated, answer briefly and politely, then return to where you were.

Bank rules that apply in every step of the call:
{sys_rules}"""

    greet_task = f"""CURRENT STEP: find out whether you are speaking with {who}. Your opening line already asked whether you are speaking with {who}.
{bullet(spec['person_check']['rules'])}
- If the person answers with a question, answer it briefly within these rules, then ask again whether you are speaking with {who}.
- Do not call verify_identity or any account tool here. Do not mention any security question yet: the next step asks it.
- Use close_call only when these rules say the call ends here."""

    third_task = f"""CURRENT STEP: the person on the line is NOT {who} (or says this is the wrong number, or that the customer has died).
{bullet(spec['third_party']['rules'])}
Allowed callback window, computed for this call: {{{{x_callback_text}}}}.
- Use schedule_callback only for a day and time inside that window (date YYYY-MM-DD, time HH:MM). After it worked, just thank them and say goodbye.
- Use close_call for every other ending, with the outcome and reason from the rules."""

    first_q = ident["questions"][0]["ask"]
    id_task = f"""CURRENT STEP: security check. The customer confirmed who they are. You have asked (or are asking) the security questions, one at a time, in this order:
{chr(10).join(f"{i + 1}. {q['field']}: ask \"{q['ask']}\" Send the answer as: {q.get('answer_format', 'as said')}" for i, q in enumerate(ident['questions']))}
{bullet(ident['rules'])}
- Call verify_identity once for each COMPLETE answer, with the field of the question you asked. Answers often come in pieces over several messages: as soon as the pieces together form the complete answer, combine them and call verify_identity (for example "9 September" then "1990" -> 1990-09-09; "99" then "04" -> 9904). Until then ask only for the missing piece ("And the year?").
- Never say anything about the account, the debt or the reason for the call in this step, even if asked. Never tell the customer which answer is correct.
- After every verify_identity result, do exactly what its what_to_do_next says."""

    neg_rules = bullet(spec["negotiation_rules"])
    notes_rules = bullet(spec.get("notes_rules") or []) if spec.get("uses_notes") else ""
    neg_task = f"""CURRENT STEP: the customer is verified. You have already stated the debt and asked the first question (see the conversation). Now get a payment promise where the rules allow it, record it, and close the call.

ACCOUNT FACTS (computed by the bank's system from get_account; trust them, do not recompute):
{{{{x_facts}}}}

DATE TABLE (check EVERY date the customer names against it; it already knows weekends, bank holidays and each product's deadline):
{{{{x_calendar}}}}
Weekday words: {{{{x_weekdays}}}}

RULES FOR THIS STEP:
{neg_rules}
{("NOTES:" + chr(10) + notes_rules) if notes_rules else ""}

HOW TO MAKE OFFERS:
- Every offer names one amount AND one date together, in one sentence (for example the attempts listed in the account facts). Do not offer an amount first and its date in a later message.
- An offer the customer refused or did not accept is used up. Never make the same offer again. Never offer the floor more often than the rules allow.
- A refusal does not end a product by itself. Before you give up on a product, make each persuasion attempt listed for it in the account facts, one per message, each after the previous one was refused, unless a rule (for example hardship, a dispute or an earlier payment) says to make none. Only after its last attempt is refused is that product refused. With several products, do this product by product.
- When the customer accepts an offer or names an allowed amount and date, go straight to the read-back.
- If a date or amount the customer said is unclear or cut off (for example "the thirty"), ask which exact day or amount they mean before you check it. Never guess.

HOW TO RECORD:
- Before recording, read back the product, the amount and the date and ask for confirmation. Call create_promise only after a clear yes.
- create_promise: product_id exactly as in the facts, amount as a number, date as YYYY-MM-DD. Then follow what_to_do_next.
- At the end: in the same message say thank you and goodbye, and call close_call with the outcome and reason that the rules give."""

    # ---------- nodes
    nodes = {}
    nodes["setup"] = code_node("setup", setup, "greet")
    closes_tr = [tr("end", "The call was closed", {"logic": "or", "conditions": [
        {"variable": "x_closed", "operator": "equals", "operand": "yes"},
        {"variable": "close_call_success_count", "operator": "greater_than", "operand": 0}]})]

    queues = (spec.get("transfers") or {}) if t_transfer else {}
    def tools(*ts):
        return [copy.deepcopy(t) for t in ts if t]
    transfer_trs = []
    for q in queues:
        transfer_trs.append(tr(f"close_transfer_{q}", f"Transferred to {q}", {"logic": "or", "conditions": [
            {"variable": "x_transfer_done", "operator": "equals", "operand": q},
            {"logic": "and", "conditions": [
                {"variable": "transfer_to_agent_args." + queue_p, "operator": "equals", "operand": q},
                {"logic": "or", "conditions": [
                    {"variable": "transfer_to_agent_response.data.ok", "operator": "equals", "operand": "true"},
                    {"variable": "transfer_to_agent_response.data.ok", "operator": "equals", "operand": "True"}]}]}]}))

    def conv(name, task, tools, transitions, on_enter, extraction=None, max_turns=40):
        n = {"name": name, "node_type": "conversation", "task_message": task, "on_enter": on_enter,
             "on_enter_first_entry_only": True, "tools": tools, "transitions": transitions,
             "max_node_turns": max_turns}
        if extraction:
            n["extraction_schema"] = extraction
        return n

    party = [{"name": "x_party", "type": "string", "enum_values": ["customer", "someone_else", "unclear"],
              "description": f"Who answered the phone, judged from the latest answer to the question whether this is {who}. "
                             "customer: the person clearly confirmed they are that person (" + "; ".join(spec['person_check'].get('confirm_examples') or []) + "). "
                             "someone_else: the person says they are someone else, a relative, that the customer is not there, that it is the wrong number, or that the customer has died. "
                             "unclear: anything else, for example a greeting, 'go ahead', 'who is this?' or a question.",
              "min_confidence": 0.6}]
    nodes["greet"] = conv("greet", greet_task, tools(t_close, t_transfer),
        [tr("identity", "The customer confirmed", cond_any(("x_party", "customer"))),
         tr("third_party", "Someone else answered", cond_any(("x_party", "someone_else")))] + transfer_trs + closes_tr,
        {"type": "tts", "text": spec["opening_line"]}, extraction=party, max_turns=12)

    cc = (spec.get("third_party") or {}).get("callback_close")
    cb_trs = [tr("close_callback", "Callback booked", {"logic": "or", "conditions": [
            {"variable": "x_callback_done", "operator": "equals", "operand": "yes"},
            {"variable": "schedule_callback_response.data.ok", "operator": "equals", "operand": "true"},
            {"variable": "schedule_callback_response.data.ok", "operator": "equals", "operand": "True"}]})] if (t_callback and cc) else []
    nodes["third_party"] = conv("third_party", third_task, tools(t_callback, t_close),
        cb_trs + closes_tr,
        {"type": "llm"}, max_turns=20)

    id_line = (ident.get("intro_line", "").strip() + " " + first_q).strip()
    verified_vars = [("x_id_state", "verified")]
    allf = (ident.get("response") or {}).get("all_verified")
    if allf:
        verified_vars += [(f"verify_identity_response.data.{allf}", "true"), (f"verify_identity_response.data.{allf}", "True")]
    nodes["identity"] = conv("identity", id_task, tools(t_verify, t_close, t_transfer),
        [tr("load_account", "Identity verified", cond_any(*verified_vars)),
         tr("close_id_failed", "Identity check failed", cond_any(("x_id_state", "failed")))] + transfer_trs + closes_tr,
        {"type": "tts", "text": id_line}, max_turns=30)

    nodes["load_account"] = {
        "name": "load_account", "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "facts",
        "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True,
        "pre_actions": [{k: v for k, v in t_account.items() if k not in ("response_mapping",)}],
        "response_mapping": [{"path": "data." + acct.get("products_key", "products"), "variable": "x_products"}],
        "transitions": [tr("facts", "always", ALWAYS)],
    }
    nodes["facts"] = code_node("facts", facts, "negotiate")

    neg_tools = tools(t_promise, t_note if spec.get("uses_notes") else None, t_transfer, t_close)
    nodes["negotiate"] = conv("negotiate", neg_task, neg_tools, transfer_trs + closes_tr,
        {"type": "tts", "text": "{{x_negotiate_opening}}"}, max_turns=60)

    # ---------- deterministic close nodes
    def close_node(name, outcome, reason, farewell):
        body = {f: {"type": "string", "default": "{{" + f + "}}"} for f in api["close_call"]["props"] if f in call_vars}
        body[out_p] = {"type": "string", "default": outcome}
        if reason:
            body[rsn_p] = {"type": "string", "default": reason}
        return {
            "name": name, "node_type": "api_request", "passthrough": True, "passthrough_default_transition": "end",
            "on_enter": {"type": "tts", "text": farewell} if farewell else {"type": "silent"}, "disable_inactivity_monitor": True,
            "pre_actions": [{"type": "function", "config": {"timeoutMs": 25000, "maxRetries": 2},
                             "function": {"name": "close_call", "handler": "__function__:api_call", "description": "Close the call.",
                                          "parameters": {"type": "object", "properties": {
                                              "url": {"type": "string", "default": api["close_call"]["url"]},
                                              "method": {"type": "string", "default": "POST"},
                                              "headers": {"type": "object", "properties": {"Content-Type": {"type": "string", "default": "application/json"}}},
                                              "body": {"type": "object", "properties": body, "required": []}}}}}],
            "transitions": [tr("end", "always", ALWAYS)],
        }

    fc = ident["failed_close"]
    nodes["close_id_failed"] = close_node("close_id_failed", fc["outcome"], fc.get("reason"), fc.get("farewell", ""))
    if cb_trs:
        nodes["close_callback"] = close_node("close_callback", cc["outcome"], cc.get("reason"), cc.get("farewell", ""))
    for q, c in queues.items():
        nodes[f"close_transfer_{q}"] = close_node(f"close_transfer_{q}", c["outcome"], c.get("reason"), c.get("farewell", ""))

    nodes["end"] = {
        "name": "end", "node_type": "end_call", "on_enter": {"type": "silent"}, "disable_inactivity_monitor": True, "transitions": [],
        "terminal": {"type": "function", "function": {"name": "call_closed", "handler": "__function__:end_call",
                                                      "parameters": {"type": "object", "properties": {"message_mode": {"type": "string", "default": "none"}}}}},
    }

    test_vars = {v: "" for v in call_vars}
    test_vars.update({"customer_id": "c_test", "now": "2026-10-14T11:00"})
    wf = {"initial_node": "setup", "system_prompt": system_prompt, "nodes": nodes, "global_nodes": {}, "test_variables": test_vars}
    return wf, {"setup": setup, "facts": facts}


def summary(wf):
    nodes = wf["nodes"]
    tools = sorted({t["function"]["name"] for n in nodes.values() for t in (n.get("tools") or []) + (n.get("pre_actions") or [])})
    return {"steps": len(nodes), "tools": tools}
