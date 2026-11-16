#!/usr/bin/env python3
"""Convert Influx query annotated CSV from backup.sh into line protocol on stdout."""

import csv, sys, datetime, re


def esc(s, chars):
    for c in ["\\"] + list(chars):
        s = s.replace(c, "\\" + c)
    return s


header = None
types = []
groups = []
for row in csv.reader(sys.stdin):
    if not row:
        continue
    if row[0] == "#datatype":
        types = row
        continue
    if row[0] == "#group":
        groups = row
        continue
    if row[0].startswith("#"):
        continue
    if "_measurement" in row and "_time" in row:
        header = row
        continue
    if header is None:
        raise ValueError("CSV header missing")
    d = dict(zip(header, row))
    v = d["_value"]
    typ = types[header.index("_value")]
    if typ == "long":
        v += "i"
    elif typ == "unsignedLong":
        v += "u"
    elif typ == "string":
        v = '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'
    elif typ == "boolean":
        v = v.lower()
    elif typ not in ("double",):
        raise ValueError("Unsupported field type " + typ)
    tags = []
    for i, k in enumerate(header):
        if (
            k
            and not k.startswith("_")
            and k not in ("result", "table")
            and i < len(groups)
            and groups[i] == "true"
        ):
            tags.append(esc(k, ",= ") + "=" + esc(d[k], ",= "))
    t = d["_time"]
    m = re.fullmatch(r"(.*T\d\d:\d\d:\d\d)(?:\.(\d+))?Z", t)
    if not m:
        raise ValueError("Expected UTC timestamp: " + t)
    seconds = int(datetime.datetime.fromisoformat(m[1] + "+00:00").timestamp())
    ns = seconds * 10**9 + int((m[2] or "").ljust(9, "0")[:9])
    measurement = esc(d["_measurement"], ", ")
    print(
        measurement
        + ("," + ",".join(tags) if tags else "")
        + " "
        + esc(d["_field"], ",= ")
        + "="
        + v
        + " "
        + str(ns)
    )
