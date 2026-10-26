"""Re-extract the Garmin Personal API Swagger document for re-verification (garmin spec 1).

The Swagger page of the Garmin developer site loads a script that embeds the OpenAPI document
as ``JSON.parse('...')``. This tool downloads that page and script, decodes the embedded
document (never executes it) and prints what the adapter relies on: servers, the activity
endpoints with their parameters, and the fields of the activity schemas. With ``--output`` it
also writes the whole document as JSON, for a manual diff; keep that file out of the
repository (Garmin's document is referenced, not redistributed).

Usage: ``uv run python tools/garmin_openapi.py [--output reports/garmin-openapi.json]``
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path
from typing import Any, Final

SITE: Final = "https://developer.garmin.com"
PAGE: Final = f"{SITE}/gc-developer-program/personal/swagger-docs/"
_SCRIPT: Final = re.compile(
    r'src="(/component---src-pages-gc-developer-program-personal-swagger[^"]+\.js)"'
)
_MARKER: Final = "JSON.parse('"
_ESCAPES: Final = {"n": "\n", "t": "\t", "r": "\r", "'": "'", '"': '"', "\\": "\\", "/": "/"}
ACTIVITY_PATHS: Final = (
    "/activitylist-service/activities/search/activities",
    "/activity-service/activity/{activityId}",
    "/download-service/files/activity/{activityId}",
)
SCHEMAS: Final = ("SummarizedActivityDTO", "ActivitySummaryDTO", "SummaryDTO", "ActivityTypeDTO")
_LIMIT: Final = 16 * 1024 * 1024


def _get(url: str) -> str:
    if not url.startswith(SITE + "/"):
        raise SystemExit(f"refusing to fetch {url}")
    headers = {"User-Agent": "coach-garmin-openapi-check"}
    request = urllib.request.Request(url, headers=headers)  # noqa: S310 - https only, checked
    with urllib.request.urlopen(request, timeout=60) as response:  # noqa: S310 - https only
        return str(response.read(_LIMIT).decode("utf-8", errors="replace"))


def _js_string(text: str, start: int) -> str:
    """Decode the single-quoted JavaScript string literal that starts at ``start``."""
    out: list[str] = []
    index = start
    while text[index] != "'":
        char = text[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        escape = text[index + 1]
        if escape == "u":
            out.append(chr(int(text[index + 2 : index + 6], 16)))
            index += 6
        elif escape == "x":
            out.append(chr(int(text[index + 2 : index + 4], 16)))
            index += 4
        else:
            out.append(_ESCAPES.get(escape, escape))
            index += 2
    return "".join(out)


def extract(script: str) -> dict[str, Any]:
    position = script.find('openapi":"')
    if position < 0:
        raise SystemExit("no OpenAPI document found in the Swagger page script")
    start = script.rfind(_MARKER, 0, position)
    if start < 0:
        raise SystemExit("the OpenAPI document is not embedded as JSON.parse('...') any more")
    document = json.loads(_js_string(script, start + len(_MARKER)))
    if not isinstance(document, dict):
        raise SystemExit("unexpected OpenAPI document")
    return document


def _describe(document: dict[str, Any]) -> None:
    print("title:", document.get("info"))
    print("servers:", [server.get("url") for server in document.get("servers", [])])
    paths = document.get("paths", {})
    print(f"paths ({len(paths)}):", ", ".join(sorted(paths)))
    for path in ACTIVITY_PATHS:
        operation = paths.get(path, {}).get("get")
        print(f"\nGET {path}:", "MISSING" if operation is None else operation.get("description"))
        for parameter in [] if operation is None else operation.get("parameters", []):
            print(f"  {parameter.get('name')} ({parameter.get('in')}): {parameter.get('schema')}")
        if operation is not None:
            for code, response in operation.get("responses", {}).items():
                print(f"  -> {code}: {sorted((response.get('content') or {}).keys())}")
    schemas = document.get("components", {}).get("schemas", {})
    for name in SCHEMAS:
        properties = schemas.get(name, {}).get("properties")
        print(f"\n{name}:", "MISSING" if properties is None else ", ".join(sorted(properties)))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", type=Path, help="write the full OpenAPI document here")
    args = parser.parse_args(argv)
    match = _SCRIPT.search(_get(PAGE))
    if match is None:
        print("the Swagger page no longer loads the expected script", file=sys.stderr)
        return 1
    document = extract(_get(SITE + match.group(1)))
    _describe(document)
    if args.output is not None:
        args.output.write_text(json.dumps(document, indent=1, sort_keys=True), encoding="utf-8")
        print(f"\nwritten to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
