"""Summarise TEAM Engine TestNG XML results: python summarize.py results.xml [...]"""

import sys
import xml.etree.ElementTree as ET


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
