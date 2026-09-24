# WMS Configurator

A guided, web-based configurator for `frappe_wms`. Walks you through the ~35
configuration doctypes (warehouse/storage structure, stock & movement types,
process rules, determination rules, numbering, routing, resources) that
today have to be set up one desk form at a time, starting from a ready-made
preset if you want one. Produces a portable JSON "profile" you can export,
version, hand to someone setting up a second site, or apply straight to a
running site.

It's a static site - no backend, no build step. Everything lives in the
browser (autosaved to `localStorage`) until you export or apply it.

## Running it

Any static file server works. From this directory:

```
cd app
python3 -m http.server 8000
```

Then open `http://localhost:8000`. To host it for real, copy the `app/`
directory to any static host (it's self-contained - no server-side code).

## Using it

1. Pick a starting preset (or Blank) on the first screen.
2. Walk the steps in the left sidebar - each one explains what it's for and
   any real dependency it has on an earlier step (e.g. Routes need a
   Door-role Storage Bin created two steps earlier).
3. Use the **Storage Bins** step's pattern generator for ranges of bins
   instead of adding them one at a time.
4. On **Review & Apply**:
   - **Export** downloads the profile as JSON - keep it in version control,
     hand it to a colleague, or apply it later.
   - **Apply on the server (bench)** is the safe path for a real site - see
     below.
   - **Apply directly to a connected site** pushes it live over the REST
     API - needs the Connect setup described below.

Presets and manual edits are equally "just a profile" - a preset only
pre-fills the form, nothing is special about it afterward.

## Applying a profile: bench (recommended for production)

No CORS or API key needed - runs with normal Frappe privileges on the
server:

```
bench --site <site> execute frappe_wms.setup.import_profile.import_profile \
  --kwargs "{'path': '/path/to/your-profile.wms-profile.json'}"
```

This is idempotent: doctypes with a natural key (e.g. `Storage Bin.bin_code`,
`WMS Warehouse.warehouse_code`) are updated in place on re-run; doctypes with
no natural key (most of the rule doctypes, e.g. `Bin Determination Rule`)
are only inserted if an identical row doesn't already exist.

Pass `dry_run=True` in the kwargs to see what would be created/updated
without writing anything.

## Applying a profile: direct connection

Useful while iterating, or when you don't have server access. Click
**Connect** in the top bar and enter:

- **Site URL** - e.g. `https://wms.local`
- **API key / secret** - generate one for a user with rights to create the
  doctypes you're configuring (User → API Access in the desk)

This requires the target site to accept cross-origin requests from wherever
the configurator is hosted. Add to that site's `site_config.json`:

```json
{
  "allow_cors": "https://your-configurator-host"
}
```

(or `"*"` for local testing only - never in production). Restart the bench
after changing it. Click **Test connection** in the Connect dialog to
verify.

Credentials are kept only in this browser tab's `sessionStorage` - never
written into an exported profile, and gone when the tab closes.

## Regenerating the schema

`app/field_help.json` is hand-written guidance (per-doctype intro plus a description for every field) layered over the generated schema at load time, so re-running the extractor never loses it. When a doctype gains a field, add its description there. The **How it fits together** step draws its diagram from the schema's Link fields, so new links appear on their own.

`app/schema.json` and `app/apply_order.json` are generated from the live
doctype JSON files in `../frappe_wms`, not hand-maintained. Re-run after
changing any in-scope doctype's fields:

```
python3 tools/extract_schema.py
```

The list of doctypes the configurator manages is `IN_SCOPE_DOCTYPES` at the
top of `tools/extract_schema.py`. If you add a new configuration doctype to
`frappe_wms`, add it there, re-run the extractor, add it to a step in
`app/js/wizard.js`, and add its name to `APPLY_ORDER` in
`frappe_wms/setup/import_profile.py` (keep it after anything it links to).

## Production mirror

`frappe_wms/www/configurator/` is a copy of this `app/` directory, served by
the Frappe site itself at `/configurator` - same-origin with the site's own
REST API, so the direct-push "Apply to site" feature needs no CORS setup
when used against that same site. It differs from `app/` in exactly one
way: `index.html` has a `<base href="/configurator/">` tag, since Frappe
serves `/configurator` and `/configurator/` identically and plain relative
URLs would break on the no-trailing-slash form.

If you change anything under `app/`, re-copy it:

```
cp -r app/* ../frappe_wms/www/configurator/
```

then re-add the `<base>` tag to the copied `index.html` (it's stripped by a
plain copy) and re-run `python3 tools/extract_schema.py` first if the
doctypes changed, so both copies of `schema.json`/`apply_order.json` agree.
Access is gated by `frappe_wms/www/configurator/index.py` to the WMS
Administrator / System Manager roles.

## What's out of scope

Transactional/instance records (Goods Receipt, Warehouse Task, Handling Unit
instances, Shipments, stock ledger/balance, ...) aren't configuration and
aren't in the profile. Neither are ERPNext-side masters the rules merely
reference (Item, Item Group, Company, Warehouse) - those are assumed to
already exist on the target site. User Permission rows are also out of
scope, since they need real Users and aren't portable between systems.
