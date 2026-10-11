// Mock of the bank's services for the phone agent. One Durable Object per customer keeps that customer's state
// (wrong answers, verification, promises, notes, the call log), so concurrent calls never share state.
import { CUSTOMERS } from "./customers.js";

const MAX_WRONG = 3;                 // adjust to the service contract
const json = (o, s = 200) => new Response(JSON.stringify(o), { status: s, headers: { "content-type": "application/json" } });

export default {
  async fetch(req, env) {
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, "");
    if (req.method === "GET" && (path === "" || path === "/health")) return json({ ok: true, service: "bank mock" });
    let body = {};
    if (req.method === "GET" && path.startsWith("/ledger/")) {
      const id = decodeURIComponent(path.slice(8));
      return env.CUSTOMER.get(env.CUSTOMER.idFromName(id)).fetch("https://do/ledger", { method: "POST", body: JSON.stringify({ customer_id: id }) });
    }
    try { body = await req.json(); } catch { return json({ ok: false, error: "body must be JSON" }, 400); }
    const id = String(body.customer_id || "");
    if (!id) return json({ ok: false, error: "customer_id missing" });
    const stub = env.CUSTOMER.get(env.CUSTOMER.idFromName(id));
    return stub.fetch("https://do" + path, { method: "POST", body: JSON.stringify(body) });
  },
};

export class Customer {
  constructor(state) { this.state = state; }

  async load(id) {
    let s = await this.state.storage.get("s");
    if (!s) s = { id, wrong: 0, passed: {}, verified: false, closed: false, promises: [], notes: [], callbacks: [], transfers: [], outcome: null, reason: null, calls: [] };
    return s;
  }

  async fetch(req) {
    const path = new URL(req.url).pathname;
    const b = await req.json();
    const s = await this.load(String(b.customer_id));
    const cust = CUSTOMERS[s.id];
    const op = path.replace(/^\//, "");
    let r;
    if (op === "ledger") return json(s);
    if (!cust) r = { ok: false, error: "unknown customer_id" };
    else if (s.closed) r = { ok: false, error: "call already closed" };
    else r = this.handle(op, b, s, cust);
    s.calls.push({ tool: op, args: b, result: r, at: new Date().toISOString() });
    await this.state.storage.put("s", s);
    return json(r);
  }

  handle(op, b, s, c) {
    switch (op) {
      case "verify_identity": {
        if (s.wrong >= MAX_WRONG) return { ok: false, error: "identity check locked", verified: false, wrong_answers_left: 0 };
        const field = String(b.field || ""), ans = String(b.answer || "").trim();
        if (!(field in c.identity)) return { ok: false, error: "unknown field" };
        if (field === "date_of_birth" && !/^\d{4}-\d{2}-\d{2}$/.test(ans)) return { ok: false, error: "date must be YYYY-MM-DD" };
        const want = String(c.identity[field]);
        let ok = ans.replace(/\s/g, "").toUpperCase() === want.replace(/\s/g, "").toUpperCase();
        const res = { ok: true, verified: ok };
        if (ok) s.passed[field] = true;
        else {
          if (field === "date_of_birth") {          // day and month swapped
            const [y, m, d] = ans.split("-");
            if (`${y}-${d}-${m}` === want && d !== m) return { ok: true, verified: false, hint: "day_month_swapped", wrong_answers_left: MAX_WRONG - s.wrong };
          }
          s.wrong += 1;
        }
        s.verified = Object.keys(c.identity).every(k => s.passed[k]);
        return { ...res, wrong_answers_left: MAX_WRONG - s.wrong, all_verified: s.verified };
      }
      case "get_account":
        if (!s.verified) return { ok: false, error: "identity not verified" };
        return { ok: true, products: c.products };
      case "create_promise": {
        if (!s.verified) return { ok: false, error: "identity not verified" };
        const p = c.products.find(x => x.product_id === b.product_id);
        if (!p) return { ok: false, error: "unknown product_id" };
        if (s.promises.find(x => x.product_id === b.product_id)) return { ok: false, error: "this product already has a promise" };
        const pr = { promise_id: "pr" + (s.promises.length + 1), product_id: b.product_id, amount: Number(b.amount), date: String(b.date) };
        s.promises.push(pr);
        return { ok: true, promise_id: pr.promise_id };
      }
      case "add_note":
        s.notes.push({ product_id: b.product_id, reason: b.reason, text: b.text || "" });
        return { ok: true };
      case "schedule_callback":
        s.callbacks.push({ date: b.date, time: b.time });
        return { ok: true };
      case "transfer_to_agent":
        s.transfers.push({ queue: b.queue });
        return { ok: true };
      case "close_call":
        s.closed = true; s.outcome = b.outcome || null; s.reason = b.reason || null;
        return { ok: true };
      default:
        return { ok: false, error: "unknown operation " + op };
    }
  }
}
