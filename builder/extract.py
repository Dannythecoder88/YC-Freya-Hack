"""Brief (+ tool API) -> spec JSON, using Claude. Validates the spec locally and asks Claude to repair it."""
import json
import re

SPEC_GUIDE = r'''
You are the reading half of a builder. A bank wrote a brief (a specification) for an automated outbound
collections phone call. Your job: read the brief and its tool API file and fill a JSON spec. A program then
turns the spec into a phone agent with a FIXED structure:

  greet (opening line, confirm the right person) -> identity (security questions) -> account loaded by code
  -> negotiate (state debt, get a promise, record it, close)       greet -> third_party (someone else answered)
  Code computes dates, deadlines, floors, attempt plans and a date table from YOUR expressions.
  Code closes the call by itself after: identity failure, a booked callback, a transfer. In every other case the
  agent model calls close_call itself.

The phone agent runs on a small model. It only knows what you put in the spec. Every rule of the brief that
matters for the call must land in exactly the right place of the spec. Missing one rule fails real customers.

SOURCES. The brief can be a clean specification or raw bank material (mails, policy extracts, memos, code lists).
A later memo or mail can CHANGE an earlier rule: always apply the newest valid instruction and drop superseded
ones. Code lists map to the tool API enums: use the exact enum strings of the tool API file. When several briefs
are given, the later one extends or replaces the earlier one as it says.

Return ONE JSON object in a ```json fenced block, with exactly these keys:

{
 "meta": {
   "bank_name": str,
   "agent_self_description": str,      // how the agent describes itself, e.g. "the digital assistant of X Bank"
   "language": str,                    // e.g. "English"
   "currency_word": str,               // spoken after amounts, e.g. "dollars". Use "" if the brief and tool API never name a currency (do not guess)
   "source_notes": str                 // one or two lines: which memo overrides what, any ambiguity you resolved
 },
 "call_variables": [str],
 "customer_name": str,                 // how the agent names the customer it asks for, with placeholders, e.g. "{{title}} {{last_name}}"              // names of the variables every call starts with, from the brief (e.g. customer_id, now, ...)
 "params": {                           // body field NAMES in the tool API file (look them up, they can differ)
   "identity_field": str, "identity_answer": str,          // verify_identity: which question / the answer
   "promise_product": str, "promise_amount": str, "promise_date": str,
   "close_outcome": str, "close_reason": str, "transfer_queue": str
 },
 "opening_line": str,   // the EXACT first sentence(s) the agent says, ending with the question asking for the right person.
                        // Use {{variable}} placeholders for call variables, e.g. "Am I speaking with {{title}} {{last_name}}?".
                        // Include every element the brief requires in the first sentence (who is calling, recording notice...).
                        // Must not reveal anything the brief forbids before identity is verified.
 "system_rules": [str], // rules that hold in EVERY step: e.g. what to say when asked if it is a robot/AI; scam question answer;
                        // request for a human (ask once if you can help, then transfer to queue X if they insist; applies before and
                        // after verification if the brief says so); questions you cannot answer; words/topics forbidden before
                        // verification (list them); never offer terms not in the brief (discounts, waivers, plans...);
                        // customer says twice they do not want to talk (closing outcome before vs after verification).
                        // Quote required sentences exactly in double quotes.
 "person_check": {
   "confirm_examples": [str],          // answers that count as confirmation
   "rules": [str]                      // how to handle "hello", "who is this", unclear answers, how many asks, which close
                                       // (outcome/reason) when still unclear; what to do when a question comes first.
 },
 "third_party": {
   "rules": [str],                     // relative/other person, wrong number, deceased (exact sentence), what may NOT be said,
                                       // asking when the customer can be reached, what to do with a time outside the window,
                                       // each with the close_call outcome/reason. Offers to pay by a third party etc.
   "callback": {
     "days": [str],                    // Python expressions for the allowed callback DAYS (see EXPRESSIONS), e.g. ["today", "next_bd(today)"]
     "start": "HH:MM", "end": "HH:MM", // window, both included
     "after_now": bool,                // must a callback today be later than now?
     "fallback_text": str              // the window in words, used only if code fails
   },
   "callback_close": {"outcome": str, "reason": str, "farewell": str}   // after schedule_callback worked; farewell = thanks + goodbye
 },
 "identity": {
   "intro_line": str,                  // short sentence before the first security question (no debt words!)
   "questions": [ {"field": str,       // exact enum value of the tool's question field
                   "ask": str,         // the question as the agent says it
                   "answer_format": str} ],   // how to send the answer, e.g. "YYYY-MM-DD; read a date given only as numbers as day, month, year"
                                       // in the order the brief requires
   "response": {"ok": str, "verified": str, "all_verified": str, "wrong_left": str, "hint": str},
                                       // field names in the verify_identity RESPONSE ("" if the API has no such field)
   "hints": {str: str},                // each hint value the API can return -> what the agent must do (and whether it counts as wrong)
   "unreadable_text": str,             // what to ask when the answer could not be read
   "rules": [str],                     // wrong answers (how many in total end the check, ask the same question again?), customer
                                       // declines to answer / asks what the call is about instead (explain once, then which close),
                                       // anything else the brief says about identity
   "failed_close": {"outcome": str, "reason": str,
                    "farewell": str}   // spoken when the check failed: include what the brief requires (e.g. use the app or a branch),
                                       // then thanks and goodbye. Must not mention the debt.
 },
 "account": {
   "products_key": str, "id_field": str, "type_field": str, "past_due_field": str, "days_late_field": str,  // get_account response
   "types": { "<product type enum>": {"label": str, "floor_name": str} },
        // label: how to name the product in words, Python format with product fields, e.g. "credit card ending {card_last4}", "car loan"
        // floor_name: what the floor is called for that type, e.g. "minimum payment", "instalment"
   "floor_expr": str,                  // Python expression giving the floor amount (see EXPRESSIONS), e.g.
                                       // "product.get('minimum_payment') if product.get('type') == 'credit_card' else product.get('instalment_amount')"
                                       // use None if the brief has no floor
   "statement_intro_single": str,      // first words after verification when there is ONE product (who calls and why, as the brief requires)
   "statement_intro_multi": str,       // same for several products; may use {n} for the number of products
   "statement_each": str,              // ONE sentence per product, Python format with {label}, {past_due}, {days_late}, {floor}.
                                       // {past_due} already includes the currency word. Include all facts the brief requires.
   "first_ask_single": str,            // the first ask, exact wording from the brief if given
   "first_ask_multi": str,             // first ask with several products (e.g. whether they can pay all of them today)
   "facts_fail_line": str              // neutral line if account data fails, e.g. "Thank you, you are verified."
 },
 "dates": {
   "weekend": [int],                   // Python weekday numbers that are not business days (Mon=0 ... Sun=6), usually [5, 6]
   "holidays": ["YYYY-MM-DD"],         // every bank holiday listed in the brief
   "earliest_date": str,               // expression for the earliest allowed promise date, usually "today"
   "next_weekday": "following_calendar_week" | "next_occurrence"   // meaning of "next <weekday>" in the brief
 },
 "bands": [ {"name": str, "when": str, "deadline": str, "attempts": int} ],
        // when: Python boolean expression on days_late / past_due / product, e.g. "1 <= days_late <= 7"
        // deadline: expression for the last allowed promise date, e.g. "add_bd(today, 12)" for "the 12th business day after today"
        // attempts: persuasion attempts allowed in that band
 "attempts": [ {"amount": "full" | "floor" | "full_or_floor", "date": str} ],
        // the persuasion ladder in order; attempt k is used only if the band allows k attempts.
        // amount full_or_floor = full past-due amount, or the floor if the customer already said the full amount is too much.
        // date: expression; can use deadline, e.g. "last_bd_this_or_next_week(today)" or "deadline"
 "floor_offer_note": str,              // what the brief requires to be said whenever the floor is offered (e.g. that the rest stays overdue), "" if nothing
 "min_amount": "floor" | "full",       // smallest amount that may be recorded
 "guard": true,                        // keep true: code refuses promises outside the date/amount limits
 "promise": {
   "retries": int,                     // how many times to retry a failed create_promise
   "recorded_sentence": str,           // exact sentence required after a recorded promise ("" if none)
   "final_fail_text": str              // what to say and do when recording finally fails (which close / note)
 },
 "negotiation_rules": [str],
        // EVERYTHING the agent must do after verification, as short imperative rules in the brief's order:
        // first ask; never ask "how much can you pay" (if the brief says so); handling of a date the customer names
        // (weekend/holiday -> next business day; after deadline -> offer the deadline; vague period -> ask once for the exact
        // day, then propose the deadline; refusing the deadline = refusal); amounts (full first, floor once with the sentence
        // that the rest stays overdue, amounts between floor and full accepted, below floor...); what counts as a flat refusal;
        // the persuasion attempts and when to stop (refer to "the attempts listed in the account facts"); hardship (exact
        // sympathy, offer support transfer, outcomes); already paid; dispute; restructuring request; read-back wording and
        // "Do you confirm?"; one promise per product; changes after recording; failure handling; the exact sentence after a
        // recorded promise; multi-product order and confirmation rules; every closing outcome with its reason.
        // Write each rule so a small model can follow it without seeing the brief. Name tools (create_promise,
        // transfer_to_agent with queue X, add_note with reason Y, close_call with outcome/reason).
 "uses_notes": bool,                   // does the brief use add_note at all?
 "notes_rules": [str],                 // when to call add_note, with which reason, and when NOT to
 "transfers": { "<queue enum>": {"outcome": str, "reason": str, "farewell": str} },
        // for EVERY transfer queue in the tool API that the brief uses: the close_call outcome/reason after that transfer;
        // farewell = short line said right after the transfer worked (e.g. "I am transferring you now. Thank you, goodbye.")
 "closing": {
   "table": [ {"outcome": str, "reason": str, "when": str} ],   // the full outcome table ("" reason when none)
   "no_reason_outcomes": [str]         // outcomes that are sent without a reason
 },
 "tool_notes": { "<tool name>": { "<body field>": str } }   // extra guidance per tool field (formats, which enum when)
}

EXPRESSIONS (Python). Available names: today (date), now (datetime), product (dict from get_account), days_late,
past_due (numbers), deadline (only in attempt dates), and helpers:
  add_bd(d, n)      the n-th business day after d (d itself not counted)
  sub_bd(d, n), next_bd(d, include_same=False), prev_bd(d, include_same=True)
  add_days(d, n), end_of_month(d), last_bd_of_month(d), week_start(d) (Monday)
  last_bd_of_week(d)              last business day of d's week (Mon-Sun)
  last_bd_this_or_next_week(d)    last business day of this week; if that is today or already past, of next week
  is_bd(d), is_holiday(d), is_weekend(d), min_date(a, b), max_date(a, b), D("YYYY-MM-DD")
Business days = not in dates.weekend and not in dates.holidays. Write only expressions, no statements.
Use single quotes inside expressions. Never write "{{" or "}}" in expressions.

CODE DOES SOME STEPS. get_account is called by code right after identity passes, and the debt statement
(statement_intro + statement_each + first ask) is spoken by code. Never tell the agent to call get_account. The agent
model only has these tools: in greet close_call and transfer_to_agent; in third_party schedule_callback and close_call;
in identity verify_identity, close_call, transfer_to_agent; in negotiate create_promise, add_note (if uses_notes),
transfer_to_agent, close_call. Do not invent example amounts or currencies in rules; use placeholders like <amount>.

STYLE. Rules are instructions to the agent ("Say ...", "If the customer ..., then ..."). Keep the brief's exact required
sentences in double quotes. Do not add rules the brief does not have. Do not leave out rules it has.
'''


def extract_prompt(briefs, openapi_text):
    parts = ["Fill the spec for the following brief(s) and tool API.\n"]
    for i, (name, text) in enumerate(briefs):
        parts.append(f"=== BRIEF {i + 1}: {name} ===\n{text}\n")
    parts.append(f"=== TOOL API (OpenAPI) ===\n{openapi_text}\n")
    parts.append("Return the JSON spec now, in one ```json block.")
    return "\n".join(parts)


def parse_json(text):
    m = re.findall(r"```json\s*(.*?)```", text, re.S)
    raw = m[-1] if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(raw)


REQUIRED = ["meta", "call_variables", "params", "opening_line", "system_rules", "person_check", "third_party", "identity",
            "account", "dates", "bands", "attempts", "promise", "negotiation_rules", "transfers", "closing"]


def validate(spec, openapi, check_code):
    """Return a list of problems. check_code(spec) runs the generated code locally and returns problems."""
    from generate import openapi_tools
    errs = []
    for k in REQUIRED:
        if k not in spec:
            errs.append(f"missing key {k}")
    if errs:
        return errs
    api = openapi_tools(openapi)
    p = spec["params"]
    def has(tool, field):
        return field in (api.get(tool) or {}).get("props", {})
    for tool, key in [("verify_identity", "identity_field"), ("verify_identity", "identity_answer"), ("create_promise", "promise_product"),
                      ("create_promise", "promise_amount"), ("create_promise", "promise_date"), ("close_call", "close_outcome"),
                      ("transfer_to_agent", "transfer_queue")]:
        if not has(tool, p.get(key, "")):
            errs.append(f"params.{key}={p.get(key)!r} is not a body field of {tool}")
    def enum(tool, field):
        return ((api.get(tool) or {}).get("props", {}).get(field) or {}).get("enum")
    oe = enum("close_call", p.get("close_outcome"))
    re_ = enum("close_call", p.get("close_reason"))
    for row in spec["closing"]["table"]:
        if oe and row["outcome"] not in oe:
            errs.append(f"closing outcome {row['outcome']!r} not in tool enum {oe}")
        if re_ and row.get("reason") and row["reason"] not in re_:
            errs.append(f"closing reason {row['reason']!r} not in tool enum {re_}")
    fe = enum("verify_identity", p.get("identity_field"))
    for q in spec["identity"]["questions"]:
        if fe and q["field"] not in fe:
            errs.append(f"identity question field {q['field']!r} not in {fe}")
    qe = enum("transfer_to_agent", p.get("transfer_queue"))
    for q in spec["transfers"]:
        if qe and q not in qe:
            errs.append(f"transfer queue {q!r} not in {qe}")
    for c in [spec["identity"]["failed_close"], spec["third_party"]["callback_close"]] + list(spec["transfers"].values()):
        if oe and c.get("outcome") not in oe:
            errs.append(f"close outcome {c.get('outcome')!r} not in {oe}")
    for v in re.findall(r"\{\{\s*([\w.]+)\s*\}\}", spec["opening_line"]):
        if v not in spec["call_variables"]:
            errs.append(f"opening_line uses {{{{{v}}}}} which is not a call variable")
    for d in spec["dates"].get("holidays") or []:
        if not re.match(r"^\d{4}-\d{2}-\d{2}$", d):
            errs.append(f"holiday {d!r} is not YYYY-MM-DD")
    errs += check_code(spec)
    return errs
