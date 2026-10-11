"""Dev only: run the generated facts code on the practice cases and check expected promise dates are allowed."""
import json, sys
sys.path.insert(0, 'builder')
import generate, selftest
spec = json.load(open(sys.argv[1])); api = json.load(open('tools/openapi.json'))
wf, code = generate.build(spec, api)
cases = json.load(open('public-test-set.json'))
for c in cases:
    if len(sys.argv) > 2 and c['brief'] not in sys.argv[2]: continue
    prods = [dict(p, product_id=f"p{i+1}") for i, p in enumerate(c['products'])]
    g = selftest._exec(code['facts'], {'vars': {'now': c['variables']['now'], 'x_products': prods}})
    v = g['vars']; rules = json.loads(v['x_rules'])
    bad = []
    for p in c['expected']['promises']:
        r = rules[p['product_id']]
        if not (r['earliest'] <= p['date'] <= r['deadline']): bad.append(f"{p} outside {r['earliest']}..{r['deadline']}")
        if not (r['min'] - 0.005 <= p['amount'] <= r['max'] + 0.005): bad.append(f"{p} amount outside {r['min']}..{r['max']}")
    print(c['id'], 'OK' if not bad else bad, {k: (r['band'], r['deadline']) for k, r in rules.items()})
if '-v' in sys.argv:
    print(v['x_negotiate_opening']); print(v['x_facts']); print(v['x_calendar']); print(v['x_weekdays'])
