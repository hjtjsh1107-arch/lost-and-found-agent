"""Export the police product classification from the public data API."""

import argparse
import csv
import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from time import sleep
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, build_opener
from xml.etree import ElementTree


API_PAGE = "https://www.data.go.kr/data/15000651/openapi.do"
API_URL = "https://apis.data.go.kr/1320000/CmmnCdService/getThngClCd"
FIELDS = ("major_code", "major_name", "minor_code", "minor_name")
KST = timezone(timedelta(hours=9))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Keep the service key on the documented HTTPS host.
        return None


def parse_categories(payload):
    response = ElementTree.fromstring(payload)
    result = response.findtext("./header/resultCode")
    if result != "00":
        reason = result or response.findtext("./cmmMsgHeader/returnReasonCode", "")
        code = reason if re.fullmatch(r"[0-9]{1,4}", reason) else "unknown"
        raise ValueError(f"API did not return success (result code {code}).")

    items = response.find("./body/items")
    if items is None:
        raise ValueError("API response is missing the category list.")

    categories = []
    for item in items.findall("item"):
        code = item.findtext("prdtCd", "").strip()
        name = item.findtext("prdtNm", "").strip()
        parent = item.findtext("hiPrdtCd", "").strip()
        if not re.fullmatch(r"[A-Z0-9]{6}", code) or not name:
            raise ValueError("API response contains an invalid category code or name.")
        categories.append((code, name, parent))
    if not categories:
        raise ValueError("API returned an empty category list.")
    return categories


def fetch_categories(opener, key, parent=None):
    params = {"serviceKey": key}
    if parent is not None:
        params["PRDT_CL_CD_01"] = parent
    try:
        with opener.open(f"{API_URL}?{urlencode(params)}", timeout=20) as response:
            return parse_categories(response.read())
    except HTTPError as exc:
        raise ValueError(f"Classification API returned HTTP {exc.code}.") from None
    except (URLError, TimeoutError, OSError):
        raise ValueError("Could not reach the classification API.") from None


def collect_categories(fetch):
    majors = fetch()
    major_codes = {code for code, _, _ in majors}
    if len(majors) != len(major_codes):
        raise ValueError("API returned a duplicate major category code.")
    if any(parent in major_codes for _, _, parent in majors):
        raise ValueError("The initial category list contains a nested category.")

    rows = []
    seen_codes = set(major_codes)
    for major_code, major_name, _ in majors:
        children = fetch(major_code)
        if not children:
            raise ValueError(f"No minor categories returned for {major_code}.")
        for minor_code, minor_name, parent in children:
            if parent != major_code:
                raise ValueError(f"Invalid parent for {minor_code} under {major_code}.")
            if minor_code in seen_codes:
                raise ValueError(f"Duplicate category code: {minor_code}.")
            seen_codes.add(minor_code)
            rows.append(dict(zip(FIELDS, (major_code, major_name, minor_code, minor_name))))
    return rows


def write_snapshot(output_dir, rows, metadata):
    output_dir.mkdir(parents=True, exist_ok=True)
    snapshot = {
        **metadata,
        "major_count": len({row["major_code"] for row in rows}),
        "minor_count": len(rows),
        "categories": rows,
    }
    (output_dir / "categories.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    with (output_dir / "categories.csv").open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return snapshot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path, default=Path(__file__).resolve().parents[1] / "data"
    )
    args = parser.parse_args()
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        parser.error("Set DATA_GO_KR_SERVICE_KEY to the approved, decoded API key.")

    opener = build_opener(NoRedirect())

    def fetch(parent=None):
        if parent is not None:
            sleep(0.25)
        return fetch_categories(opener, key, parent)

    try:
        rows = collect_categories(fetch)
        snapshot = write_snapshot(
            args.output_dir,
            rows,
            {
                "status": "api_snapshot",
                "collected_on": datetime.now(KST).date().isoformat(),
                "sources": [API_PAGE],
                "web_comparison": "not_verified",
            },
        )
    except (ValueError, ElementTree.ParseError, OSError) as exc:
        parser.exit(1, f"{exc}\n")
    print(f"Exported {snapshot['major_count']} major and {snapshot['minor_count']} minor categories.")


if __name__ == "__main__":
    main()
