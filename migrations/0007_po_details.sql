-- TIM RENO -- Item 4: Purchase Order product search, payment terms/delivery
-- info, DRAFT watermark, signature line, and duplicate.
--
-- Only the schema piece lives here (payment terms + delivery info are
-- per-PO fields, negotiated per order/supplier -- separate from Item 3's
-- settings_po_terms boilerplate legal text, which is the same on every PO).
-- All three columns are nullable/optional, so every existing PO is
-- unaffected until someone fills these in from the new "Order details"
-- panel on the PO detail page.
ALTER TABLE purchase_orders ADD COLUMN payment_terms TEXT;
ALTER TABLE purchase_orders ADD COLUMN expected_delivery_date TEXT;
ALTER TABLE purchase_orders ADD COLUMN ship_to_address TEXT;
