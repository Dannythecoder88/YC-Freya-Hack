"""Python snippets that run INSIDE the phone agent (Freya code nodes and tool hooks).

Everything here is generic: brief-specific values arrive through the `cfg` dict that the
spec extractor produced from the brief. The cfg is embedded as hex-encoded JSON so the
snippet never contains '{{' or '}}' (the platform may treat those as template markers).

Each builder function returns Python source. The same source is executed locally by
selftest.py before the workflow is uploaded, so a crash shows up at build time.
"""
import json

# ---------------------------------------------------------------- shared helpers (in-sandbox)
HELPERS = r'''
import json, re, datetime
CFG = json.loads(bytes.fromhex(CFG_HEX).decode('utf-8'))
WD = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
MN = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December']
HOL = set(CFG.get('holidays') or [])
WEEKEND = set(CFG.get('weekend') if CFG.get('weekend') is not None else [5, 6])
ONE = datetime.timedelta(days=1)

def parse_now(s):
    s = str(s or '').strip()
    m = re.search(r'(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ](\d{1,2}):(\d{2}))?', s)
    if m:
        return datetime.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4) or 0), int(m.group(5) or 0))
    m = re.search(r'(\d{1,2})[./](\d{1,2})[./](\d{4})(?:[T ,]+(\d{1,2}):(\d{2}))?', s)
    if m:
        return datetime.datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), int(m.group(4) or 0), int(m.group(5) or 0))
    return datetime.datetime.now()

def D(s):
    if isinstance(s, datetime.datetime): return s.date()
    if isinstance(s, datetime.date): return s
    return datetime.date.fromisoformat(str(s)[:10])

def is_bd(d): return d.weekday() not in WEEKEND and d.isoformat() not in HOL
def is_holiday(d): return d.isoformat() in HOL
def is_weekend(d): return d.weekday() in WEEKEND
def add_days(d, n): return d + datetime.timedelta(days=n)

def next_bd(d, include_same=False):
    x = d if include_same else d + ONE
    for _ in range(60):
        if is_bd(x): return x
        x += ONE
    return x

def prev_bd(d, include_same=True):
    x = d if include_same else d - ONE
    for _ in range(60):
        if is_bd(x): return x
        x -= ONE
    return x

def add_bd(d, n):
    x, c = d, 0
    while c < n:
        x += ONE
        if is_bd(x): c += 1
    return x

def sub_bd(d, n):
    x, c = d, 0
    while c < n:
        x -= ONE
        if is_bd(x): c += 1
    return x

def week_start(d): return d - datetime.timedelta(days=d.weekday())

def last_bd_of_week(d):
    s = week_start(d); x = s + datetime.timedelta(days=6)
    while x >= s:
        if is_bd(x): return x
        x -= ONE
    return None

def last_bd_this_or_next_week(d):
    x = last_bd_of_week(d)
    if x is None or x <= d:
        x = last_bd_of_week(d + datetime.timedelta(days=7))
    return x

def end_of_month(d):
    n = (d.replace(day=28) + datetime.timedelta(days=4))
    return n - datetime.timedelta(days=n.day)

def last_bd_of_month(d): return prev_bd(end_of_month(d), True)

def bd_between(a, b):
    c, x = 0, a
    while x < b:
        x += ONE
        if is_bd(x): c += 1
    return c

def min_date(*a): return min(x for x in a if x is not None)
def max_date(*a): return max(x for x in a if x is not None)

def spoken(d, year=True):
    return WD[d.weekday()] + ' ' + str(d.day) + ' ' + MN[d.month - 1] + ((' ' + str(d.year)) if year else '')

def short(d): return WD[d.weekday()][:3] + ' ' + str(d.day) + ' ' + MN[d.month - 1][:3]

def money(x):
    try: x = float(x)
    except Exception: return str(x)
    s = '%.2f' % x
    if s.endswith('.00'): s = s[:-3]
    return s

def say_money(x):
    w = CFG.get('currency_word') or ''
    return money(x) + ((' ' + w) if w else '')

def num(x, default=None):
    try: return float(x)
    except Exception: return default
'''

# ---------------------------------------------------------------- generated rule functions
def rule_functions(cfg):
    """Turn the spec's Python expressions into functions. Expressions see: product (dict),
    days_late (number), past_due (number), today (date), now (datetime), and every helper."""
    bands = cfg["bands"]
    lines = ["def band_index(product, days_late, past_due, today, now):"]
    for i, b in enumerate(bands):
        lines.append(f"    if ({b['when']}): return {i}")
    lines.append("    return -1")
    lines.append("def band_deadline(i, product, days_late, past_due, today, now):")
    for i, b in enumerate(bands):
        lines.append(f"    if i == {i}: return ({b['deadline']})")
    lines.append("    return today")
    lines.append("def earliest_date(today, now):")
    lines.append(f"    return ({cfg.get('earliest_date') or 'today'})")
    lines.append("def floor_amount(product, days_late, past_due, today, now):")
    lines.append(f"    return ({cfg['floor_expr']})")
    lines.append("def attempt_date(k, deadline, product, days_late, past_due, today, now):")
    for k, a in enumerate(cfg["attempts"]):
        lines.append(f"    if k == {k}: return ({a['date']})")
    lines.append("    return deadline")
    days = (cfg.get("callback") or {}).get("days") or []
    lines.append("def callback_days(today, now):")
    lines.append("    return [" + ", ".join(f"({d})" for d in days) + "]")
    return "\n".join(lines) + "\n"


def _hex(cfg):
    return json.dumps(cfg, ensure_ascii=False).encode("utf-8").hex()


def _prelude(cfg):
    return f"CFG_HEX = '{_hex(cfg)}'\n" + HELPERS + rule_functions(cfg)


# ---------------------------------------------------------------- setup node (call start)
SETUP_BODY = r'''
try:
    now = parse_now(vars.get('now'))
    today = now.date()
    vars['x_today'] = today.isoformat()
    vars['x_today_text'] = spoken(today) + ', ' + ('%02d:%02d' % (now.hour, now.minute))
    cb = CFG.get('callback') or {}
    parts = []
    for d in callback_days(today, now):
        d = D(d)
        start, end = cb.get('start') or '00:00', cb.get('end') or '23:59'
        if d == today and cb.get('after_now', True):
            nowhm = '%02d:%02d' % (now.hour, now.minute)
            if nowhm >= end:
                continue
            if nowhm >= start:
                parts.append(spoken(d) + ' (today, ' + d.isoformat() + ') at a time later than ' + nowhm + ' and no later than ' + end)
                continue
        parts.append(spoken(d) + ' (' + d.isoformat() + ') from ' + start + ' to ' + end + ' (both included)')
    vars['x_callback_text'] = ' OR '.join(parts) if parts else 'no callback time is possible'
    vars['x_setup_state'] = 'ok'
except Exception as e:
    vars['x_today_text'] = str(vars.get('now'))
    vars['x_callback_text'] = str((CFG.get('callback') or {}).get('fallback_text') or 'see the rules')
    vars['x_setup_state'] = 'error ' + str(e)[:200]
'''

def setup_code(cfg):
    return _prelude(cfg) + SETUP_BODY


# ---------------------------------------------------------------- facts node (after get_account)
FACTS_BODY = r'''
def _load_products():
    raw = vars.get('x_products')
    for cand in (raw,):
        if isinstance(cand, list): return cand
        if isinstance(cand, dict) and isinstance(cand.get(CFG['products_key']), list): return cand[CFG['products_key']]
        if isinstance(cand, str) and cand.strip():
            s = cand.strip()
            for t in (s, s.replace("'", '"').replace('True', 'true').replace('False', 'false').replace('None', 'null')):
                try:
                    v = json.loads(t)
                    if isinstance(v, list): return v
                    if isinstance(v, dict) and isinstance(v.get(CFG['products_key']), list): return v[CFG['products_key']]
                except Exception:
                    pass
    # flattened keys such as <x>.data.products.0.past_due
    found = dict()
    pat = re.compile(r'\.' + re.escape(CFG['products_key']) + r'\.(\d+)\.([A-Za-z_][A-Za-z0-9_]*)$')
    for k in list(vars.keys()):
        m = pat.search(str(k))
        if m:
            found.setdefault(int(m.group(1)), dict())[m.group(2)] = vars.get(k)
    return [found[i] for i in sorted(found)]

def _label(p):
    t = CFG['types'].get(str(p.get(CFG['type_field'])), dict())
    tpl = t.get('label') or str(p.get(CFG['type_field'], 'product')).replace('_', ' ')
    try:
        return tpl.format(**p)
    except Exception:
        return re.sub(r'\{[^}]*\}', '', tpl).strip()

def _floor_name(p):
    t = CFG['types'].get(str(p.get(CFG['type_field'])), dict())
    return t.get('floor_name') or 'minimum amount'

try:
    now = parse_now(vars.get('now'))
    today = now.date()
    prods = _load_products()
    if not prods:
        raise ValueError('no products found')
    rules, blocks, statements = dict(), [], []
    earliest = earliest_date(today, now)
    max_deadline = today
    n = len(prods)
    for idx, p in enumerate(prods):
        pid = str(p.get(CFG['id_field']) or ('p' + str(idx + 1)))
        dl_days = num(p.get(CFG['days_late_field']), 0)
        due = num(p.get(CFG['past_due_field']), 0)
        bi = band_index(p, dl_days, due, today, now)
        band = CFG['bands'][bi] if bi >= 0 else None
        deadline = band_deadline(bi, p, dl_days, due, today, now) if band else today
        deadline = D(deadline)
        max_deadline = max(max_deadline, deadline)
        floor = floor_amount(p, dl_days, due, today, now)
        floor = num(floor, None)
        n_att = int(band.get('attempts', 1)) if band else 0
        label = _label(p)
        fname = _floor_name(p)
        ladder = []
        for k, a in enumerate(CFG['attempts']):
            if k >= n_att: break
            ad = D(attempt_date(k, deadline, p, dl_days, due, today, now))
            if ad > deadline: ad = deadline
            amt = a.get('amount', 'full')
            fnote = (' (when you offer the ' + fname + ', also say: ' + CFG['floor_offer_note'] + ')') if CFG.get('floor_offer_note') else ''
            if amt == 'floor' and floor is not None:
                what = 'the ' + fname + ' of ' + say_money(floor) + fnote
            elif amt == 'full_or_floor' and floor is not None:
                what = 'the full ' + say_money(due) + ' (or the ' + fname + ' of ' + say_money(floor) + ' if the customer already said the full amount is too much' + fnote + ')'
            else:
                what = 'the full ' + say_money(due)
            ladder.append('Attempt ' + str(k + 1) + ': propose ' + what + ' on ' + spoken(ad) + ' (' + ad.isoformat() + ').')
        min_amt = floor if (CFG.get('min_amount') == 'floor' and floor is not None) else due
        rules[pid] = dict(label=label, full=due, floor=floor, min=min_amt, max=due, deadline=deadline.isoformat(), earliest=earliest.isoformat(), band=(band or dict()).get('name', 'none'), attempts=n_att)
        b = []
        b.append('PRODUCT ' + str(idx + 1) + ' of ' + str(n) + ': ' + label + ' (product_id ' + pid + ')')
        b.append('- Past due: ' + say_money(due) + ' (number for tools: ' + ('%.2f' % due) + '). Days late: ' + money(dl_days) + '.')
        if floor is not None:
            b.append('- Floor (' + fname + '): ' + say_money(floor) + ' (number for tools: ' + ('%.2f' % floor) + '). Any amount from ' + money(min_amt) + ' to ' + money(due) + ' can be recorded; never more than ' + money(due) + '.')
            if CFG.get('floor_offer_note'):
                b.append('- Whenever you offer the ' + fname + ', say in the same message: ' + CFG['floor_offer_note'])
        b.append('- Band: ' + rules[pid]['band'] + '. Persuasion attempts allowed for this product: ' + str(n_att) + '.')
        b.append('- Promise date must be from ' + spoken(earliest) + ' to the DEADLINE ' + spoken(deadline) + ' (' + deadline.isoformat() + '), and a business day.')
        for l in ladder: b.append('- ' + l)
        blocks.append('\n'.join(b))
        st = CFG.get('statement_each') or '{label}: {past_due}, {days_late} days late.'
        try:
            statements.append(st.format(label=label, past_due=say_money(due), amount=say_money(due), days_late=money(dl_days), floor=(say_money(floor) if floor is not None else '')))
        except Exception:
            statements.append(label + ': ' + say_money(due) + ', ' + money(dl_days) + ' days late.')
    # date table
    rows = []
    end = max(add_days(max_deadline, 3), add_days(today, 10))
    d = today
    pids = list(rules.keys())
    while d <= end and len(rows) < 45:
        tag = ''
        if d == today: tag = ' [today]'
        elif d == add_days(today, 1): tag = ' [tomorrow]'
        if not is_bd(d):
            why = 'bank holiday' if is_holiday(d) else 'weekend'
            nb = next_bd(d)
            okp = [q for q in pids if nb <= D(rules[q]['deadline'])]
            if okp:
                st = 'NOT allowed (' + why + '); offer the next business day ' + spoken(nb, False) + ' instead'
                if len(okp) < len(pids): st += ' for ' + ', '.join(okp) + '; for the others it is after their deadline'
            else:
                st = 'NOT allowed (' + why + ') and the next business day is after the deadline: offer the deadline instead'
        elif d < earliest:
            st = 'NOT allowed (too early)'
        else:
            okp = [q for q in pids if d <= D(rules[q]['deadline'])]
            if len(okp) == len(pids): st = 'allowed'
            elif okp: st = 'allowed for ' + ', '.join(okp) + ' only; AFTER the deadline of ' + ', '.join([q for q in pids if q not in okp])
            else: st = 'AFTER the deadline: not allowed, offer the deadline instead'
            last = [q for q in pids if d == D(rules[q]['deadline'])]
            if last: st += '  <- DEADLINE (last allowed day) of ' + ', '.join(last)
        rows.append(spoken(d) + ' = ' + d.isoformat() + tag + ': ' + st)
        d = add_days(d, 1)
    rows.append('Any date after ' + spoken(end, False) + ': after every deadline, not allowed.')
    # weekday words
    words = []
    ws = week_start(today)
    for i in range(7):
        this_d = ws + datetime.timedelta(days=i)
        if CFG.get('next_weekday') == 'next_occurrence':
            nxt = today + datetime.timedelta(days=((i - today.weekday()) % 7) or 7)
        else:
            nxt = this_d + datetime.timedelta(days=7)
        tw = '"this ' + WD[i] + '" = ' + short(this_d) + (' (already past)' if this_d < today else (' (today)' if this_d == today else ''))
        words.append(tw + '; "next ' + WD[i] + '" = ' + short(nxt))
    vars['x_facts'] = '\n\n'.join(blocks)
    vars['x_calendar'] = '\n'.join(rows)
    vars['x_weekdays'] = '\n'.join(words)
    vars['x_rules'] = json.dumps(rules)
    vars['x_statement'] = ' '.join(statements)
    intro = CFG.get('statement_intro_multi' if n > 1 else 'statement_intro_single') or ''
    ask = CFG.get('first_ask_multi' if n > 1 else 'first_ask_single') or ''
    try:
        intro = intro.format(n=n)
    except Exception:
        pass
    vars['x_negotiate_opening'] = ' '.join([x for x in [intro, ' '.join(statements), ask] if x]).strip()
    vars['x_n_products'] = str(n)
    vars['x_product_ids'] = ', '.join(pids)
    vars['x_facts_state'] = 'ok'
except Exception as e:
    vars['x_facts'] = 'The account facts could not be prepared (' + str(e)[:150] + '). Use the get_account result shown in the conversation and the rules.'
    vars['x_calendar'] = 'not available'
    vars['x_weekdays'] = 'not available'
    vars['x_rules'] = '{}'
    vars['x_statement'] = ''
    vars['x_negotiate_opening'] = str(CFG.get('facts_fail_line') or 'Thank you, you are verified.')
    vars['x_n_products'] = '1'
    vars['x_product_ids'] = ''
    vars['x_facts_state'] = 'error'
'''

def facts_code(cfg):
    return _prelude(cfg) + FACTS_BODY


# ---------------------------------------------------------------- tool hooks
# A compact prelude for hooks (no rule functions needed).
HOOK_PRELUDE = r'''
import json, re
CFG = json.loads(bytes.fromhex(CFG_HEX).decode('utf-8'))
def _truthy(v): return str(v).strip().lower() in ('true', '1', 'yes')
def _data(r):
    if isinstance(r, dict):
        r.pop('header', None)
        d = r.get('data')
        if isinstance(d, dict): return d
        if d is None or isinstance(d, str):
            nd = dict(raw=d) if d is not None else dict()
            r['data'] = nd
            return nd
    return None
def _failed(r, d):
    sc = r.get('status_code') if isinstance(r, dict) else None
    try:
        if sc is not None and int(sc) >= 400: return True
    except Exception:
        pass
    if isinstance(r, dict) and str(r.get('status', '')).lower() in ('error', 'failed', 'failure'): return True
    if isinstance(d, dict) and d.get(CFG.get('ok_field', 'ok')) is not None and not _truthy(d.get(CFG.get('ok_field', 'ok'))): return True
    return False
'''

def _hook(cfg, body):
    return f"CFG_HEX = '{_hex(cfg)}'\n" + HOOK_PRELUDE + body


GENERIC_POST = r'''
try:
    d = _data(result)
    if d is not None:
        if _failed(result, d):
            d['what_to_do_next'] = CFG.get('fail_text') or 'This call FAILED; nothing was done. Do not say that it worked.'
        else:
            d['what_to_do_next'] = CFG.get('ok_text') or 'Done. Now speak to the customer.'
except Exception:
    pass
'''

def generic_post_hook(ok_text, fail_text, ok_field="ok"):
    return _hook({"ok_text": ok_text, "fail_text": fail_text, "ok_field": ok_field}, GENERIC_POST)


IDENTITY_PRE = r'''
try:
    b = config.get('body') if isinstance(config, dict) else None
    if isinstance(b, dict):
        f = b.get(CFG['field_param'])
        if f is not None: vars['x_last_field'] = str(f)
        a = b.get(CFG['answer_param'])
        if isinstance(a, str): b[CFG['answer_param']] = a.strip()
except Exception:
    pass
'''

IDENTITY_POST = r'''
try:
    d = _data(result)
    if d is not None:
        qs = CFG['questions']
        field = str(vars.get('x_last_field') or '')
        passed = [q['field'] for q in qs if str(vars.get('x_passed_' + q['field'])) == 'yes']
        rf = CFG['resp']
        sc = result.get('status_code') if isinstance(result, dict) else None
        verified = d.get(rf.get('verified') or 'verified')
        allv = d.get(rf.get('all_verified') or 'all_verified')
        left = d.get(rf.get('wrong_left') or 'wrong_answers_left')
        hint = d.get(rf.get('hint') or 'hint')
        okv = d.get(rf.get('ok') or 'ok')
        if _truthy(verified) and field:
            vars['x_passed_' + field] = 'yes'
            if field not in passed: passed.append(field)
        remaining = [q for q in qs if q['field'] not in passed]
        try:
            left_n = int(float(left)) if left is not None else None
        except Exception:
            left_n = None
        try:
            server_err = sc is not None and int(sc) >= 500
        except Exception:
            server_err = False
        if server_err:
            msg = 'The check service had an error; nothing was counted. Say you need to check that again and call verify_identity again with the same answer.'
        elif _truthy(allv) or (CFG.get('all_by_count') and not remaining):
            vars['x_id_state'] = 'verified'
            msg = 'Identity fully verified.'
        elif hint and str(hint) in CFG.get('hints', dict()):
            msg = CFG['hints'][str(hint)]
        elif okv is not None and not _truthy(okv) and verified is None:
            msg = 'The answer could not be checked (it was not counted as wrong). ' + (CFG.get('unreadable_text') or 'Ask the customer to say it again clearly.')
        elif left_n is not None and left_n <= 0 and not _truthy(verified):
            vars['x_id_state'] = 'failed'
            msg = 'The identity check has FAILED. Do not discuss the account.'
        elif _truthy(verified):
            if remaining:
                msg = 'Correct. Now ask the next security question: ' + remaining[0]['ask']
            else:
                vars['x_id_state'] = 'verified'
                msg = 'Identity fully verified.'
        else:
            cur = [q for q in qs if q['field'] == field]
            ask = cur[0]['ask'] if cur else (remaining[0]['ask'] if remaining else '')
            msg = 'That answer is WRONG' + ((' (' + str(left_n) + ' wrong answers left)') if left_n is not None else '') + '. Do not reveal the correct answer. Ask the same question again: ' + ask
        vars['x_id_msg'] = msg
        d['what_to_do_next'] = msg
except Exception:
    pass
'''

def identity_hooks(cfg):
    return _hook(cfg, IDENTITY_PRE), _hook(cfg, IDENTITY_POST)


PROMISE_PRE = r'''
try:
    b = config.get('body') if isinstance(config, dict) else None
    if isinstance(b, dict):
        pid = str(b.get(CFG['pid_param']) or '')
        vars['x_last_pid'] = pid
        vars['x_blocked'] = ''
        try:
            rules = json.loads(vars.get('x_rules') or '{}')
        except Exception:
            rules = dict()
        a = b.get(CFG['amount_param'])
        try:
            amt = round(float(str(a).replace(',', '').replace('$', '').strip()), 2)
            b[CFG['amount_param']] = amt
        except Exception:
            amt = None
        dt = str(b.get(CFG['date_param']) or '').strip()[:10]
        b[CFG['date_param']] = dt
        r = rules.get(pid)
        why = ''
        if CFG.get('guard') and r:
            import datetime
            hol = set(CFG.get('holidays') or [])
            wk = set(CFG.get('weekend') if CFG.get('weekend') is not None else [5, 6])
            try:
                dd = datetime.date.fromisoformat(dt)
                if dd.isoformat() < r['earliest']: why = 'the date ' + dt + ' is before the earliest allowed date ' + r['earliest']
                elif dd.isoformat() > r['deadline']: why = 'the date ' + dt + ' is after the deadline ' + r['deadline'] + ' of this product'
                elif dd.weekday() in wk or dd.isoformat() in hol: why = 'the date ' + dt + ' is not a business day'
            except Exception:
                why = 'the date ' + dt + ' is not a valid YYYY-MM-DD date'
            if not why and amt is not None:
                if r.get('min') is not None and amt < float(r['min']) - 0.005: why = 'the amount ' + str(amt) + ' is below the minimum ' + str(r['min'])
                elif r.get('max') is not None and amt > float(r['max']) + 0.005: why = 'the amount ' + str(amt) + ' is above the past-due amount ' + str(r['max'])
        if why:
            vars['x_blocked'] = why
            b[CFG['pid_param']] = 'blocked-not-allowed'
except Exception:
    pass
'''

PROMISE_POST = r'''
try:
    d = _data(result)
    if d is not None:
        pid = str(vars.get('x_last_pid') or '')
        why = str(vars.get('x_blocked') or '')
        if why:
            d['what_to_do_next'] = 'NOT RECORDED: the bank rules do not allow it, because ' + why + '. Nothing was recorded. Do not say it was recorded. Apply the rules: offer an allowed date or amount instead and read it back again.'
            vars['x_blocked'] = ''
        elif _failed(result, d) and re.search(r'already', str(d.get('error') or d.get('message') or d.get('raw') or ''), re.I):
            vars['x_recorded_' + pid] = 'yes'
            d['what_to_do_next'] = 'The bank already holds a promise for ' + pid + ' from earlier, so it is already on record. Do not call create_promise again for ' + pid + '. ' + (CFG.get('ok_text') or '')
        elif _failed(result, d):
            k = 'x_fail_' + pid
            try:
                n = int(vars.get(k) or 0) + 1
            except Exception:
                n = 1
            vars[k] = str(n)
            if n <= int(CFG.get('retries', 1)):
                d['what_to_do_next'] = 'This attempt FAILED and nothing was recorded. Do not say it was recorded. Call create_promise again right now with exactly the same values (retry ' + str(n) + ' of ' + str(CFG.get('retries', 1)) + ').'
            else:
                d['what_to_do_next'] = 'FAILED again. The promise for ' + pid + ' could NOT be recorded. ' + (CFG.get('final_fail_text') or 'Tell the customer you could not record it.')
        else:
            vars['x_recorded_' + pid] = 'yes'
            d['what_to_do_next'] = 'RECORDED. ' + (CFG.get('ok_text') or '')
except Exception:
    pass
'''

def promise_hooks(cfg):
    return _hook(cfg, PROMISE_PRE), _hook(cfg, PROMISE_POST)


CLOSE_PRE = r'''
try:
    b = config.get('body') if isinstance(config, dict) else None
    if isinstance(b, dict):
        o = str(b.get(CFG['outcome_param']) or '')
        r = b.get(CFG['reason_param'])
        if o in CFG.get('no_reason', []) or r is None or str(r).strip() in ('', 'none', 'null', 'None'):
            b.pop(CFG['reason_param'], None)
except Exception:
    pass
'''

def close_pre_hook(cfg):
    return _hook(cfg, CLOSE_PRE)


def assert_no_braces(code, where):
    if "{{" in code or "}}" in code:
        i = code.find("{{") if "{{" in code else code.find("}}")
        raise ValueError(f"{where}: generated code contains a double brace near: {code[max(0, i - 60):i + 20]!r}")
