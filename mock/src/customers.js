// Test customers from the case folder. Filled in from the zip's test-customer file at 17:00 UTC.
// Shape: { "<customer_id>": { identity: { <field>: "<answer>" }, products: [ { product_id, ... } ] } }
export const CUSTOMERS = {
  "c_demo": { identity: { date_of_birth: "1988-03-14", phone_last4: "4471" },
              products: [{ product_id: "p1", type: "credit_card", card_last4: "4821", past_due: 420.0, days_late: 6, minimum_payment: 126.0 }] },
};
