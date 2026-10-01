#!/usr/bin/env python3
"""Frappe WMS on-site print agent - standard library only, no Frappe install needed.

Runs next to the printers (a small PC or the packing station computer), asks the WMS for the
print jobs of one printer (a WMS Resource of type Printer with Connection = Print Agent),
prints them locally and reports each one back as printed or failed.

    python3 wms_print_agent.py --url https://erp.example.com --key API_KEY --secret API_SECRET \\
        --device MAD1-PRN-01 --zpl tcp:192.168.10.50:9100 --pdf lp:Office_Laser

Targets:
    tcp:HOST:PORT   raw socket (label printers, port 9100)
    lp:QUEUE        the operating system's print queue (CUPS lp / lpr); "lp:" = default printer
    file:DIR        write each job to a file (testing)

The API user needs a WMS role that may print (WMS Operator, WMS Packer, WMS Integration User...).
"""
import argparse
import base64
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request


def call(args, method, **params):
    data = urllib.parse.urlencode({k: v if isinstance(v, str) else json.dumps(v) for k, v in params.items()}).encode()
    req = urllib.request.Request(f"{args.url.rstrip('/')}/api/method/{method}", data=data,
                                 headers={"Authorization": f"token {args.key}:{args.secret}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode()).get("message")


def send(target, data, filename, printer_name=None):
    kind, _, rest = target.partition(":")
    if kind == "tcp":
        host, _, port = rest.rpartition(":")
        with socket.create_connection((host, int(port or 9100)), timeout=15) as conn:
            conn.sendall(data)
    elif kind == "lp":
        queue = printer_name or rest
        with tempfile.NamedTemporaryFile(suffix=os.path.splitext(filename)[1], delete=False) as f:
            f.write(data)
            path = f.name
        try:
            cmd = ["lp"] + (["-d", queue] if queue else []) + (["-o", "raw"] if filename.endswith(".zpl") else []) + [path]
            subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        finally:
            os.unlink(path)
    elif kind == "file":
        os.makedirs(rest or ".", exist_ok=True)
        with open(os.path.join(rest or ".", filename), "wb") as f:
            f.write(data)
    else:
        raise ValueError(f"unknown target {target}")


def run_once(args):
    jobs = call(args, "frappe_wms.api.printing.agent_claim", output_device=args.device, limit=args.batch) or []
    for job in jobs:
        try:
            if job["format"] == "zpl":
                target, data = args.zpl, job["content"].encode("utf-8")
            else:
                target, data = args.pdf, base64.b64decode(job["content"])
            if not target:
                raise RuntimeError(f"no --{job['format']} target configured on this agent")
            send(target, data, job["filename"], job.get("printer") if target.startswith("lp:") else None)
            call(args, "frappe_wms.api.printing.mark_printed", spool_name=job["spool"])
            print(f"printed {job['spool']}", flush=True)
        except Exception as e:  # report and carry on with the next job
            call(args, "frappe_wms.api.printing.mark_failed", spool_name=job["spool"], reason=str(e)[:400])
            print(f"FAILED {job['spool']}: {e}", file=sys.stderr, flush=True)
    return len(jobs)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--url", required=True)
    p.add_argument("--key", required=True)
    p.add_argument("--secret", required=True)
    p.add_argument("--device", required=True, help="WMS Resource name of the printer")
    p.add_argument("--zpl", help="target for label jobs, e.g. tcp:10.0.0.5:9100")
    p.add_argument("--pdf", help="target for document jobs, e.g. lp:Office")
    p.add_argument("--interval", type=float, default=3.0)
    p.add_argument("--batch", type=int, default=10)
    p.add_argument("--once", action="store_true", help="process the queue once and exit")
    args = p.parse_args(argv)
    while True:
        try:
            n = run_once(args)
        except Exception as e:  # the WMS is unreachable: wait and try again
            print(f"poll failed: {e}", file=sys.stderr, flush=True)
            n = 0
        if args.once:
            return 0
        if not n:
            time.sleep(args.interval)


if __name__ == "__main__":
    sys.exit(main())
