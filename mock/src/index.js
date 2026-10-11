// Freya Bank statement services mock (Cloudflare Worker), written from the case's service contract
// (sources/documents/2026-10-01-service-contract.md) and test-environment/test-customers.{md,json}.
// Stateless: each answer is the exact stored response (status + body) for the request's data fields,
// found the way test-customers.md says; unknown customer/card/week -> the service's 404 from contract section 4,
// or a 400 INVALID_ARGUMENTS_ERROR where the service has no 404.
import DATA from "./test-customers.json";

const SERVICES = ["cards.list", "cards.balance", "statement.week.get", "statement.week.decide", "statement.week.change", "contact.email.get", "statement.pdf.send"];
const NOT_FOUND = {   // contract section 4
  "cards.list": [404, "FB_IVR_CHANNEL_102", "WARNING", "Müşteriye ait geçerli kart bilgisi bulunamadı."],
  "cards.balance": [404, "FB_IVR_CHANNEL_120", "WARNING", "Kart bulunamadı."],
  "statement.week.get": [404, "FB_IVR_CHANNEL_121", "WARNING", "Kart bulunamadı."],
  "statement.pdf.send": [404, "FB_IVR_CHANNEL_107", "WARNING", "Pdf dokümanı bulunamadı"],
};
const norm = s => String(s ?? "").toLowerCase().replace(/[^a-z0-9]/g, "");
const BY_NORM = Object.fromEntries(SERVICES.map(s => [norm(s), s]));

// indexes: customer number -> customer; masked card number -> customer (per-card services carry only the card)
const CUSTOMERS = {}, CARD_OWNER = {};
for (const c of DATA.customers) {
  CUSTOMERS[String(c.customerNo)] = c;
  for (const [svc, v] of Object.entries(c.services)) if (v && !("http" in v)) for (const card of Object.keys(v)) CARD_OWNER[card] = c;
}

let seq = 0;
const guid = () => (Date.now().toString(16) + (++seq).toString(16).padStart(6, "0") + "0".repeat(32)).slice(0, 32);
const json = (o, status = 200) => new Response(JSON.stringify(o), { status, headers: { "content-type": "application/json; charset=utf-8", "Access-Control-Allow-Origin": "*" } });
function error(status, code, type, msg) {
  const g = guid();
  return json({ result: { guid: g, returnCode: code, messageText: msg, code, message: msg, type, details: { guid: g, rootCause: type === "ERROR" ? "InvalidArguments" : "NotFound", errorOrigin: "ivr-gateway-test:ivr-channel-v0" } } }, status);
}
const invalid = msg => error(400, "INVALID_ARGUMENTS_ERROR", "ERROR", msg);
const notFound = svc => NOT_FOUND[svc] ? error(...NOT_FOUND[svc]) : invalid("record not found");

function serviceFor(path) {
  const n = norm(path);
  for (const [k, s] of Object.entries(BY_NORM)) if (n.endsWith(k)) return s;
  return null;
}
const empty = v => v === undefined || v === null || String(v).trim() === "";
const digits = v => String(v ?? "").replace(/\D/g, "");

function cardEntry(table, card) {
  if (!table) return undefined;
  const c = String(card).trim();
  if (table[c]) return table[c];
  return undefined;
}

// Freya's "Test API Request" button may send variables unfilled ("{{customerNumber}}"); answer those with the
// first test customer so the panel test works (as in the case's example Worker).
const isTemplate = v => typeof v === "string" && (v.startsWith("{{") || v.startsWith("%7B%7B"));
const SAMPLE = DATA.customers[0];
const CORS = { "Access-Control-Allow-Origin": "*" };

export default {
  async fetch(req) {
    if (req.method === "OPTIONS") return new Response(null, { headers: { ...CORS, "Access-Control-Allow-Methods": "GET, POST, OPTIONS", "Access-Control-Allow-Headers": "*" } });
    const url = new URL(req.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";
    if (path === "/" || path === "/health") return json({ ok: true, service: "Freya Bank statement services mock", customers: DATA.customers.length });
    if (norm(path).endsWith("oauth2token")) return json({ access_token: "test-token", token_type: "Bearer", expires_in: 1800 });

    let body = {};
    try { body = await req.json(); } catch { body = {}; }
    // contract envelope: { data: {...}, call: {...} }; also accept the fields at the top level
    const d = (body && typeof body.data === "object" && body.data) ? { ...body, ...body.data } : (body || {});

    if (isTemplate(d.customerNo)) d.customerNo = SAMPLE.customerNo;
    if (isTemplate(d.cardNumber)) d.cardNumber = Object.keys(Object.values(SAMPLE.services).find(v => v && !("http" in v)) || {})[0] || d.cardNumber;
    if (isTemplate(d.newStatementWeek)) d.newStatementWeek = "2";
    const svc = serviceFor(path);
    if (!svc) return error(404, "NOT_FOUND", "ERROR", "unknown service " + path);

    let entry;
    if (svc === "cards.list" || svc === "contact.email.get" || svc === "statement.pdf.send") {
      if (empty(d.customerNo)) return invalid("customer number cannot be empty");
      if (svc === "statement.pdf.send") {
        for (const f of ["cardNumber", "currencyType", "statementDate", "emailAddress"])
          if (empty(d[f])) return invalid(f + " cannot be empty");
      }
      const cust = CUSTOMERS[digits(d.customerNo)];
      entry = cust ? cust.services[svc] : undefined;
    } else {
      if (empty(d.cardNumber)) return invalid("card number cannot be empty");
      const owner = CARD_OWNER[String(d.cardNumber).trim()];
      entry = owner ? cardEntry(owner.services[svc], d.cardNumber) : undefined;
      if (entry && (svc === "statement.week.decide" || svc === "statement.week.change")) {
        if (empty(d.newStatementWeek)) return invalid("newStatementWeek cannot be empty");
        entry = entry[digits(d.newStatementWeek)];
      }
    }
    if (!entry || !("http" in entry)) return notFound(svc);
    const delay = entry.delaySeconds ?? entry.gecikmeSaniye;
    if (delay) await new Promise(r => setTimeout(r, Number(delay) * 1000));
    return json(entry.response ?? entry.yanit ?? {}, entry.http);
  },
};
