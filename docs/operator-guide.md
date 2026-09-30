# Operator guide by role

Short, practical instructions for each job in the warehouse. The RF screens are
at `/wms` (see [rf-devices.md](rf-devices.md) for device setup). Supervisors
and planners work in the desk: **WMS Monitor** and **Repack Center**.

## Everyone: the basics

- **Start of shift.** Open the app, log in with your own user, and log on to
  a resource (your device or vehicle). If your job uses a queue, pick it on
  the log-on screen. *Get Work* then hands you the next task.
- **Scanning.** Point and scan; you rarely need to tap a field. Every scan
  beeps and flashes: green means accepted, red means wrong. A red message
  says what you scanned and what was expected, and waits until you read it.
- **Quantities.** The quantity box proposes the full amount. Type what you
  actually have. If the product comes in cases or pallets, choose the
  **Counting unit** and count in those; the app converts to single units.
- **Something is wrong** (bin empty, damaged goods, label missing): press
  **Exception** on the task, choose the reason, and add a comment if asked.
  Never confirm a quantity you do not have.
- **Lost connection.** Your entries stay on the device. Press **Retry** when
  the network is back; nothing is posted twice.
- **Interrupted.** Unfinished work appears on the home screen under
  *Unfinished work*. Tap it to continue exactly where you stopped.
- **End of shift.** *Device & session > Log off* frees the resource for the
  next person.

## Receiver (Inbound > Receive)

1. Open **Receive** and choose the delivery (supplier, ASN or PO number).
2. Scan the product or the supplier's GS1 pallet label. A GS1 label fills in
   product, batch, expiry, serial and pallet number (SSCC) by itself.
3. Scan or create the **Handling Unit** (pallet) it goes on. The next line
   keeps the same HU until you scan another.
4. Enter the quantity (choose *Counting unit* for cases). Batch and serial
   fields appear only when the product needs them.
5. **Add to receipt** for each line, then **Post** once everything on the truck
   is counted. Posting creates the putaway tasks, or cross-dock tasks when an
   outbound order is waiting for the goods.
6. More than the delivery line expects is refused: put the extra aside and
   tell your supervisor.

**Deconsolidate** splits a mixed pallet to several destinations. **Quality**
records the pass/fail result of an inspection.

## Putaway and internal moves (Inbound > Putaway Tasks, Internal)

Every task follows the same four steps:

1. **Scan source.** The bin or HU you take from.
2. **Quantity.** Confirm or change it. Less than planned keeps the rest open.
3. **Scan destination.** The bin shown; scanning another is refused.
4. **Review & confirm.**

- **Move** is an unplanned move you decide yourself: scan from, product, quantity, to.
- **Close Movement** takes an HU one step further along its route.
- **Handling Units** looks up, creates, nests, blocks or recycles pallets and totes.
- **Repack** moves goods between HUs.

## Picker (Outbound > Picking, Pick Tasks)

1. **Picking > Auto** gives you the next task from your queues. **Manual**
   finds tasks by delivery, wave, order, HU or bin.
2. Scan the source bin, confirm the quantity (a delivery ordered in cases
   proposes cases), and scan the destination tote or pallet.
3. A cluster pick shows which delivery gets how much. Keep the orders apart.
4. Short in the bin? Use **Exception** with the code for a shortage and enter
   the quantity found. The task closes at that amount and a replenishment
   request is raised for the bin.

## Packer (Outbound > Pack, or the desk Repack Center)

1. Log on to your **packing table** (work center).
2. **Pack product**: scan source HU → product → quantity → destination carton.
   Stations set to *one unit per scan* pack one piece per product scan.
3. **New HU** creates a carton or pallet; its label prints if the station is
   set up for it.
4. **Close HU**: weigh it and enter the weight. The station may require the
   weight, check it against the calculated weight, or check the delivery is
   complete. Closed HUs may move on to the outbound area by themselves.
5. One HU never mixes deliveries. A tote picked for several deliveries asks
   which delivery each pack step is for.
6. What your station offers (pack HU, unpack, pack by instruction, missing
   quantity…) is set by your supervisor per table.

## Loader and shipping (Outbound > Load, Ship)

1. Open **Load** and choose the shipment for your door.
2. Load **last stop first**: the screen names the next HU and each HU's stop.
   Loading out of order warns or is refused, depending on the warehouse.
3. Scan each HU as it goes on the truck. When everything is loaded, goods
   issue posts by itself.
4. **Depart** when the truck leaves. This also completes its dock appointment.

**Ship** is a manual fallback for posting a goods issue without loading.

## Gate and dock (Inbound / Outbound > Yard)

1. **Yard** lists today's trucks: at a door, in the yard, expected.
2. A truck arrives: tap it, scan the yard spot where it parks (or leave it
   empty) → **Check in**.
3. Its door is free: scan the door (or leave it empty for the booked door) →
   **To door**. For outbound trucks, that door becomes the loading door.
4. Unloaded or loaded → **Unloaded / loaded**, then **Check out** when it
   leaves the site.
5. No appointment? **+ Truck without appointment**: registration, delivers or
   collects. The warehouse may ask you to confirm, or refuse it.

## Inventory controller (Internal > Count, Tasks)

1. Open **Count**. Counts are created by the cycle count plan or by a
   supervisor.
2. Scan each bin and count what is there, per product, batch and HU. With
   blind counting you do not see the expected quantity.
3. The count posts when every line is counted. Differences outside tolerance
   go to a recount or to supervisor approval.

## Kitting and VAS (Internal > Kitting, Outbound > VAS)

- **Kitting**: open the order and check *Needed at the work center*.
  **Stage components** creates tasks that bring what is missing. When
  everything is there, scan the output HU (if required) → **Complete**. The
  kit posts, and a putaway task follows when the order asks for it.
- **VAS**: tap each step when it is done (labelling, shrink-wrapping,
  …). The order completes after the last step.

## Supervisor (desk: WMS Monitor)

| Where | What you do there |
|---|---|
| **Overview** / **KPIs** | Open work, throughput, exception rate, performance per person |
| **Inbound / Outbound Monitor** | Every delivery with its status. Allocate, release waves, create pick tasks. **Complete short** closes a delivery with what was done (and the ERPNext order remainder, if configured) |
| **Warehouse Tasks** | Find tasks; raise an exception on one or reverse a confirmed one. Selection screens take patterns, lists and saved variants; **Load next** pages through large results |
| **Yard & Doors** | Door board, the day's appointments, booking, check-in and walk-ins |
| **Repack Center** | The packing tables, as the packers see them |
| **Alerts** | ERPNext postings not yet done (**Retry now**), requests without tasks (**Create tasks**), aged exceptions, negative quants, counts waiting for approval |
| **Difference Analyzer** | Count and pick differences over a period |
| **Stock Overview / Stock Movements** | Where everything is and how it got there |

You also receive an hourly notification when something in your warehouse
needs attention (*WMS Settings > Alerts*).

## Process engineer / administrator (desk)

- Set up structure and rules with the **Configurator** (`/configurator`):
  warehouse, storage types, bins, determination rules, process types,
  printers, work centers, yard, ERP integration. It exports a profile you
  can apply to another company.
- Per warehouse: *ERP Integration* (how ERPNext documents reach the warehouse
  and how results post back), *Kitting*, *Loading*, *Yard and Dock
  Appointments*.
- Per work center (packing table): the Repack Center customizing.
- *WMS Settings*: scan verification, blind counting, alerts, ledger
  archiving.
- Roles and warehouses: give each user their WMS role and, where needed, a
  User Permission on *WMS Warehouse*.
