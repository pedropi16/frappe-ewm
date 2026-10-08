# SAP EWM gap review (2026-10-08)

Source: SAP S/4HANA 2025 FPS02 EWM docs (help.sap.com, fetched with `~/.claude/tools/sap_help.py`) compared with the code. Yard rows were verified in code; other rows come from a code inventory plus greps ("no code found").
Note: README.md and app_gap.md still say yard is out of scope - the code has appointments, transportation units and yard tasks. Fix those texts when yard work lands.

## Yard management

| SAP concept | App today | Status |
|---|---|---|
| Yard structure: parking, checkpoints, doors, yard sections; checkpoint = bin + process type | Yard/Door are storage-type roles; "Checkpoint" is only a Route Stop label | Y1 |
| Vehicle groups TUs; TU built from means of transport + packaging material | No Vehicle, no Means of Transport; `unit_type` unused | Y2 |
| TU activity Planned/Active/Completed | Flat TU status derived from appointment | Y3 |
| Arrival/departure at checkpoint is a prerequisite for yard moves | Check-in takes any free yard spot, no checkpoint | Y1 |
| Yard movement = HU task, needs GR posted + TU active | Manual yard task, no stock/HU link | Y4 |
| Door determination by process type / route / calendar | `free_door` = first free door by sequence | Y5 |
| Shipping & receiving cockpit | Monitor board = schedule only | Y6 |
| Process gated by TU/appointment | receipt.py / shipping.py never check it | Y7 |
| Seals editable, required | `seal_number` stored, never validated | Y8 |
| Appointment scheduling (recurrence, capacity, carrier) | Fixed 30 min slots 06-22, free-text carrier | Y9 |
| Delivery "Assign TU" status, locks delivery | None; no ERP link | Y10 |
| S&R authorizations | shared matrix | Y11 |

## Other areas
- Inbound: expected goods receipt (ASN) object; QM trigger points; returns item types / rough inspection.
- Outbound: delivery splitting; cartonization (planned shipping HUs); wave template condition technique, collective retrieval, aggregated release-order-parts page.
- Inventory: four-eyes recount; Difference Analyzer / compare-stock; fuller stock key (best-before, GR date, country of origin, special stock, PSA); slotting index + rearrangement; dangerous goods; per-diem storage billing.
- Labor/RF: shifts, indirect labor; RF screen configuration (design choice).
- Integrations: documented versioned API, webhooks, TM freight orders, GTS/customs, MES/JIT/kanban supply, real PLC layouts, opportunistic and transportation cross-docking, EDI/carrier APIs.

## Order of work
1. Yard (Y1-Y11)  2. Docs fix  3. Delivery split + cartonization  4. ASN + returns  5. Four-eyes recount + Difference Analyzer  6. Dangerous goods  7. API + webhooks

## Status (2026-10-08, deployed to production)
Done: Y1 checkpoints (storage role + arrival/departure, optional-required), Y3 TU activity, Y5 door determination rules, Y7 receipt/loading gate (Off/Warn/Block), Y8 seal at departure, Y10 yard status on delivery/shipment, Y2 (Means of Transport master + payload check only), four-eyes recount.
Corrections to the review: an expected-GR/ASN object is effectively the replicated Inbound Delivery; `analyze_differences` already exists as a variance report; partial goods issue already exists.
Still open: Vehicle grouping several TUs (Y2), HU-based yard moves (Y4), shipping & receiving cockpit with auto TU creation (Y6), recurring appointments / carrier capacity (Y9), yard-specific roles (Y11); delivery splitting (changes ERPNext documents - needs a design call), cartonization, returns item types, dangerous goods, API/webhooks, TM/GTS/MES integrations.

Update: delivery split done and deployed (`services/delivery_split.py`, "Split Delivery" button on Outbound Delivery). Open quantity moves to a new `-S<n>` delivery; a delivery replicated from a draft Delivery Note gets a second draft Delivery Note, a Sales-Order-sourced one gets its Delivery Notes from its own goods issue.

Update: cartonization (weight/volume, Planned Shipping HUs, packing station proposal) and dangerous goods storage control (hazard class, segregation, points limit) done. Not covered: DG on transport/shipping documents, 3D cartonization.
Update: documented API (OpenAPI generated from code) and webhook catalogue done - docs/api.md. Not covered: TM/GTS/MES/kanban connectors, EDI/carrier APIs.
