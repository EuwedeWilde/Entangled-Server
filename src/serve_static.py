#!/usr/bin/env python3
"""Minimal static server for the Entangled web UI.

Unlike `python -m http.server`, this refuses to serve sensitive paths
(the virtualenv, backend source, git data, dotfiles). It only hands out
the front-end assets that visitors actually need.

Usage:  python3 serve_static.py [port]   (default port 8000)
"""
import sys
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

# Path prefixes that must never be served, even if requested directly.
BLOCKED_PREFIXES = (".venv", "server", ".git", "__pycache__")
# Only these file extensions are allowed to be served.
ALLOWED_EXT = (
    ".html", ".js", ".css", ".wasm", ".json", ".map",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp",
    ".woff", ".woff2", ".ttf", ".otf", ".eot",
)


class SafeHandler(SimpleHTTPRequestHandler):
    def _is_allowed(self, path: str) -> bool:
        # Normalise and split into parts relative to the serving dir.
        rel = path.lstrip("/").split("?", 1)[0].split("#", 1)[0]
        norm = os.path.normpath(rel)
        # Reject anything trying to climb out of the directory.
        if norm.startswith("..") or os.path.isabs(norm):
            return False
        parts = norm.split(os.sep)
        # Block sensitive directories and any dotfile/dotdir.
        for part in parts:
            if part in BLOCKED_PREFIXES:
                return False
            if part.startswith(".") and part not in ("", "."):
                return False
        # Allow the directory root (serves index) and allowed extensions.
        if norm in ("", "."):
            return True
        # Allow directories (so e.g. /assets/ works); files must match ext.
        full = os.path.join(os.getcwd(), norm)
        if os.path.isdir(full):
            return True
        return norm.lower().endswith(ALLOWED_EXT)

    def do_GET(self):
        if not self._is_allowed(self.path):
            self.send_error(403, "Forbidden")
            return
        return super().do_GET()

    def do_HEAD(self):
        if not self._is_allowed(self.path):
            self.send_error(403, "Forbidden")
            return
        return super().do_HEAD()

    # Quieter logging: skip the per-file noise, keep it to one line.
    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    httpd = ThreadingHTTPServer(("0.0.0.0", port), SafeHandler)
    print(f"Static server (hardened) on port {port}, dir {os.getcwd()}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
