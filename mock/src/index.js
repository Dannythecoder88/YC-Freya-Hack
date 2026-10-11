// Freya Bank statement services mock (Cloudflare Worker), written from the case's service contract and test customers.
// Stateless: every answer is the exact stored response for (service, customer, card, requested week), HTTP status
// included, with the stored delay when there is one.
import DATA from "./test-customers.json";

// Exact paths from the integration document go here (path -> service). Until then any path whose letters spell a
// service name (cards/list, cards.list, cardsList, /api/v1/cards-list, ...) resolves to it.
const ROUTES = {};
const SERVICES = ["cards.list", "cards.balance", "statement.week.get", "statement.week.decide", "statement.week.change", "contact.email.get", "statement.pdf.send"];
const norm = s => String(s || "").toLowerCase().replace(/[^a-z0-9]/g, "");
const BY_NORM = Object.fromEntries(SERVICES.map(s => [norm(s), s]));
const CUSTOMERS = Object.fromEntries(DATA.customers.map(c => [String(c.customerNo), c]));

const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json; charset=utf-8" } });
const errorBody = (code, msg) => ({ result: { guid: crypto.randomUUID().replace(/-/g, ""), returnCode: code, messageText: msg, code, message: msg, type: "ERROR", details: { guid: null, rootCause: null, errorOrigin: "mock" } } });

function serviceFor(path) {
  if (ROUTES[path]) return ROUTES[path];
  const n = norm(path);
  for (const [k, s] of Object.entries(BY_NORM)) if (n.endsWith(k)) return s;
  return null;
}

// find a value in the request under any of several spellings (top level or one level down)
function pick(body, names) {
  const want = names.map(norm);
  const scan = obj => {
    if (!obj || typeof obj !== "object") return undefined;
    for (const [k, v] of Object.entries(obj)) if (want.includes(norm(k)) && v !== undefined && v !== null && v !== "") return v;
    return undefined;
  };
  let v = scan(body);
  if (v === undefined) for (const x of Object.values(body || {})) { v = scan(x); if (v !== undefined) break; }
  return v;
}

function byCard(table, card) {
  if (!table || card === undefined) return undefined;
  const c = String(card).replace(/\s/g, "");
  if (table[c]) return table[c];
  const last4 = c.replace(/\D/g, "").slice(-4);
  const keys = Object.keys(table).filter(k => k.replace(/\D/g, "").slice(-4) === last4);
  return keys.length === 1 ? table[keys[0]] : undefined;
}

export default {
  async fetch(req) {
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (path === "/" || path === "/health") return json({ ok: true, service: "Freya Bank statement services mock", customers: DATA.customers.length });
    let body = {};
    if (req.method !== "GET") { try { body = await req.json(); } catch { body = {}; } }
    for (const [k, v] of url.searchParams) if (!(k in body)) body[k] = v;

    const service = serviceFor(path);
    if (!service) return json(errorBody("FB_IVR_CHANNEL_404", "Unknown service " + path), 404);
    const custNo = pick(body, ["customerNo", "customerNumber", "customer_no", "customerId", "customer_id", "musteriNo"]);
    const cust = CUSTOMERS[String(custNo ?? "").replace(/\D/g, "")];
    if (!cust) return json(errorBody("FB_IVR_CHANNEL_102", "Müşteri bulunamadı."), 404);

    let entry = cust.services[service];
    if (entry && !("http" in entry)) {               // keyed by card (and maybe by requested week)
      const card = pick(body, ["cardNumber", "card_number", "maskedCardNumber", "cardNo", "kartNo"]);
      entry = byCard(entry, card);
      if (entry && !("http" in entry)) {
        const week = pick(body, ["newStatementWeek", "statementWeek", "week", "new_statement_week"]);
        entry = entry[String(week ?? "").replace(/\D/g, "")];
      }
    }
    if (!entry) return json(errorBody("FB_IVR_CHANNEL_400", "Geçersiz istek."), 400);
    const delay = entry.delaySeconds ?? entry.gecikmeSaniye;
    if (delay) await new Promise(r => setTimeout(r, Number(delay) * 1000));
    return json(entry.response ?? entry.yanit ?? {}, entry.http || 200);
  },
};
