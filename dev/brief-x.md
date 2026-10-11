# Material for the Harbor Savings Bank call project

Everything below is fictional and synthetic. It is a test brief written by the builder team, not a bank document.

---

**From:** Priya Lang (Head of Early Collections, Harbor Savings Bank)
**To:** Automation vendor
**Subject:** Phone assistant for overdue accounts – what we need

Hi team,

as discussed, the assistant phones customers who are behind on one to three accounts. Attached are the policy
extract and the code list. Please note Martin's memo at the bottom; it changes two things in the policy.

The assistant introduces itself as "Harbor Savings' automated assistant" and must say in the first sentence that
the call may be recorded for quality. It must not say anything about money owed before the customer passed security.
We address customers by first name and last name ("Am I speaking with Jordan Lee?").

Customers can ask anything; when it is not in the material, the assistant says it does not have that information.

Best,
Priya

---

## Policy extract: COL-7 Outbound early collections (v2.3)

**7.1 Call inputs.** customer_id, first_name, last_name, now (local date and time of the call, ISO).

**7.2 Right party.** Confirmation is a clear yes or the customer stating their name. If the answer is unclear, ask a second time; if still unclear, end the call (code list: NR / UNCLEAR).

**7.3 Third parties.** Do not disclose the reason for the call. Offer a callback on one of the next two business days (not today), between 09:00 and 18:00 inclusive. If the third party proposes something outside this, ask once more; otherwise end without callback (NR / NO_CB). Wrong number: NR / WRONG_NO.
If told the customer passed away: say "Please accept our condolences." and end (NR / DECEASED).

**7.4 Security.** Two questions in this order: postcode of the registered address, then year of birth. Postcode is sent without spaces, upper case. A wrong answer may be repeated; two wrong answers in total fail security (NV / FAILED). After failing: advise the customer to call the number on the back of their card, no account details. A customer who refuses to answer twice: NV / REFUSED.

**7.5 Disclosure.** After security, say the call is about overdue amounts on their accounts with Harbor Savings and state each account: product name, amount overdue and days past due.

**7.6 Dates.** Business days are Monday to Friday excluding bank holidays (2026-11-03, 2026-11-26, 2026-12-24, 2026-12-25, 2026-12-31). The latest acceptable date depends on days past due:

| Days past due | Segment | Latest date | Attempts |
|---|---|---|---|
| 1–10 | S1 | 10 calendar days after today; if that is not a business day, the business day before | 1 |
| 11–30 | S2 | 5th business day after today | 2 |
| 31–60 | S3 | 3rd business day after today | 2 |

Today counts as a valid date if it is a business day. "Next Monday" means the first Monday after today.

**7.7 Amounts.** The minimum acceptable amount is the `min_due` returned for the account. Never ask the customer how much they can pay. Start with the full overdue amount.

**7.8 Persuasion.** A refusal uses one attempt. Attempt 1: full amount on the latest date of the segment. Attempt 2: the minimum due on the latest date. Stop after the allowed attempts (NP / REFUSED).

**7.9 Special cases.** Hardship (illness, unemployment, bereavement): express sympathy in one sentence, no persuasion, offer the hardship team (queue hardship_team; outcome TR / HARDSHIP); if declined NP / HARDSHIP. Customer disputes the debt: NP / DISPUTE, no promise. Customer says already paid: tell them payments can take two business days to show, no promise, outcome PAID. Request for a person: transfer to queue agent (TR / AGENT) after offering help once.

**7.10 Confirmation.** Read every promise back and ask "Is that correct?". Record only after yes. After recording, say "A confirmation letter will be sent to you." If the booking service fails, retry once; if it fails again, apologise and end NP / SYSTEM.

**7.11 Several accounts.** One promise per account; per-account outcome codes go into a note (reason codes: REFUSED, HARDSHIP, DISPUTE, PAID, SYSTEM) for every account without a promise.

---

**MEMO** from Martin Oduya, Compliance, 2 October 2026

Effective immediately, COL-7 changes as follows:
1. Security: THREE wrong answers in total fail security, not two.
2. Segment S2 latest date is the 6th business day after today (was 5th).
All else unchanged.

---

## Code list (call result)

| Code | Meaning |
|---|---|
| PTP | promise to pay recorded (no sub-code) |
| PAID | customer reports payment made (no sub-code) |
| TR | transferred; sub-code HARDSHIP or AGENT |
| NP | no promise; sub-code REFUSED, HARDSHIP, DISPUTE, SYSTEM |
| NV | not verified; sub-code FAILED, REFUSED |
| NR | not reached; sub-code CALLBACK, NO_CB, WRONG_NO, DECEASED, UNCLEAR |
