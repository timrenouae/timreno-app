-- Item 5 follow-up: a Material Requisition can now capture who's receiving
-- the material and where -- receiver name, receiver mobile number, and a
-- text box for a location URL (e.g. a pasted Google Maps link), entered
-- once when the requisition is created (or edited later while it's still
-- open). All nullable -- existing requisitions are unaffected.
ALTER TABLE material_requisitions ADD COLUMN receiver_name TEXT;
ALTER TABLE material_requisitions ADD COLUMN receiver_phone TEXT;
ALTER TABLE material_requisitions ADD COLUMN location_url TEXT;

-- The same three fields carry onto the resulting Purchase Order once a
-- requisition is awarded (so the vendor sees exactly who to deliver to and
-- where, right on the order they're fulfilling) -- also settable directly
-- on a PO created the normal way, for the same reason Item 4 put
-- ship_to_address there.
ALTER TABLE purchase_orders ADD COLUMN receiver_name TEXT;
ALTER TABLE purchase_orders ADD COLUMN receiver_phone TEXT;
ALTER TABLE purchase_orders ADD COLUMN location_url TEXT;

-- The vendor's own "yes, got it, we're on it" acknowledgement for a PO --
-- distinct from `status` (which the receiving flow already drives) and
-- from Item 5's requisition_vendor.status ('submitted' just means they
-- priced it, not that they've accepted the resulting order). Set once, by
-- the vendor themselves, from their portal; staff see it on the PO detail
-- page as a badge instead of having to phone and ask.
ALTER TABLE purchase_orders ADD COLUMN delivery_accepted_at TEXT;
