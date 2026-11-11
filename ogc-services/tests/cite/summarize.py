# Summarises TEAM Engine TestNG XML results; exits 1 on any failure.
# Usage: python summarize.py results.xml [...]   (called by run_cite.sh)

import sys
import xml.etree.ElementTree as ET


# Prints totals and non-passing tests of one result file; returns the failure count.
def summarize(path: str) -> int:
    root = ET.parse(path).getroot()
    attrs = root.attrib
    print(f"{path}: total={attrs['total']} passed={attrs['passed']} failed={attrs['failed']} skipped={attrs['skipped']}")
    for method in root.iter("test-method"):
        if method.get("is-config") == "true" or method.get("status") == "PASS":
            continue
        message = method.find(".//exception/message")
        text = (message.text or "").strip().replace("\n", " ")[:200] if message is not None else ""
        print(f"  {method.get('status')} {method.get('name')}: {text}")
    return int(attrs["failed"])


if __name__ == "__main__":
    sys.exit(1 if sum(summarize(p) for p in sys.argv[1:]) else 0)
