#!/usr/bin/env python3
"""Stamps every module import, stylesheet and script tag with a content hash (?v=...).

Frappe serves the configurator's static files with a multi-hour Cache-Control, so after a
deploy a browser can run a mix of old and new modules. Versioned URLs make a changed file a new
URL. Every import of a module must carry the *same* version, otherwise the browser loads two
copies of it - so this rewrites all of them together. Idempotent; run after any change to
app/js or app/css (see README), then copy to frappe_wms/www/configurator.

    python3 tools/version_assets.py
"""
import hashlib
import re
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
WWW = Path(__file__).resolve().parents[2] / "frappe_wms" / "www" / "configurator"

def digest():
    h = hashlib.sha1()
    for f in sorted([*(APP / "js").glob("*.js"), *(APP / "css").glob("*.css")]):
        text = re.sub(r"\?v=[0-9a-f]+", "", f.read_text())  # ignore the stamp itself
        h.update(f.name.encode()); h.update(text.encode())
    return h.hexdigest()[:10]

def stamp(text, v):
    text = re.sub(r"(from\s+\"\./[A-Za-z0-9_]+\.js)(\?v=[0-9a-f]+)?\"", rf'\1?v={v}"', text)
    text = re.sub(r"(import\(\"\./[A-Za-z0-9_]+\.js)(\?v=[0-9a-f]+)?\"", rf'\1?v={v}"', text)
    text = re.sub(r"((?:src|href)=\"(?:js|css)/[A-Za-z0-9_]+\.(?:js|css))(\?v=[0-9a-f]+)?\"", rf'\1?v={v}"', text)
    return text

def main():
    v = digest()
    for f in [*(APP / "js").glob("*.js"), APP / "index.html", WWW / "index.html"]:
        if f.exists():
            f.write_text(stamp(f.read_text(), v))
    print("asset version", v)

if __name__ == "__main__":
    main()
