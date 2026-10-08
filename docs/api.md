# frappe_wms API and webhooks (version 1.0)

## Calling the API
Every whitelisted function in `frappe_wms/api/` is an endpoint: `POST /api/method/frappe_wms.api.<module>.<function>` (also under `/api/v2/method/`), JSON body = the function's arguments, answer `{"message": ...}`. Authenticate with `Authorization: token <api_key>:<api_secret>` (user > API Access). Roles decide what a call may do, exactly as in the desk. Calls with an `idempotency_key` argument can be retried with the same key without posting twice.

## The machine-readable description
`GET /api/method/frappe_wms.api.docs.openapi` (System Manager / WMS Administrator) returns an OpenAPI 3 document built from the code: every endpoint, its arguments (required/default) and its docstring. It is generated on request, so it always matches the deployed version. Breaking changes will move to a new `API_VERSION` in `api/docs.py`.

## Webhooks (events out)
Frappe's own Webhook doctype does the sending (retries, request log, HMAC `X-Frappe-Webhook-Signature` when a secret is set). `frappe_wms.api.docs.webhook_events` lists the curated events; `frappe_wms.api.docs.subscribe_webhook(event, url, secret)` creates the Webhook for one. The body is the whole document as JSON; the `X-WMS-Event` header carries the event name.

| Event | Fires when |
|---|---|
| goods_receipt.posted | a Goods Receipt is submitted |
| goods_issue.posted | a Goods Issue is submitted |
| inbound_delivery.status_changed / outbound_delivery.status_changed | the delivery's status changes |
| warehouse_task.confirmed | a warehouse task becomes Confirmed |
| shipment.status_changed | a WMS Shipment's status changes |
| dock_appointment.status_changed | a yard appointment's status changes |

Any other doctype event can be added by hand as a normal Webhook.
