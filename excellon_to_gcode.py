#!/usr/bin/env python3
"""
Excellon (multi-tool) → GRBL G-code drill script.

Usage:
    python excellon_to_gcode.py input.drl output.nc
"""

import sys
import re
from pathlib import Path

# ---- Parameters you’ll likely tweak ----
SAFE_Z   = 3.0      # retract height (mm)
DRILL_Z  = -1.6     # drilling depth (mm)
FEED     = 80.0     # drill feed (mm/min)
RPM      = 12000    # spindle RPM
# ----------------------------------------


def detect_units_and_format(lines):
    units = "METRIC"
    fraction_digits = 2

    for raw in lines:
        line = raw.strip().upper()
        if line.startswith("METRIC") or line.startswith("INCH"):
            units = "INCH" if line.startswith("INCH") else "METRIC"

            if "," in line:
                fmt = line.split(",", 1)[1]
                if "." in fmt:
                    _, frac = fmt.split(".", 1)
                    fraction_digits = len(frac)
            break

    return units, fraction_digits


def parse_excellon(path: Path):
    lines = path.read_text(errors="ignore").splitlines()
    units, frac_digits = detect_units_and_format(lines)
    scale = 10 ** (-frac_digits)

    tool_header_re = re.compile(r"^T(\d+)C([\d.]+)", re.IGNORECASE)
    tool_select_re = re.compile(r"^T(\d+)\s*$", re.IGNORECASE)
    coord_re = re.compile(r"^X\+?(-?\d+)Y\+?(-?\d+)", re.IGNORECASE)

    tools = {}
    current_tool = None
    header_finished = False

    for raw in lines:
        line = raw.strip()
        if not line:
            continue

        if not header_finished:
            if line.startswith("%"):
                header_finished = True

            m_hdr = tool_header_re.match(line)
            if m_hdr:
                tid = int(m_hdr.group(1))
                size = float(m_hdr.group(2))
                tools.setdefault(tid, {"size": size, "holes": []})
            continue

        upper = line.upper()
        if upper.startswith("M30"):
            break

        m_hdr = tool_header_re.match(line)
        if m_hdr:
            tid = int(m_hdr.group(1))
            size = float(m_hdr.group(2))
            tools.setdefault(tid, {"size": size, "holes": []})
            continue

        m_sel = tool_select_re.match(line)
        if m_sel and "C" not in upper:
            tid = int(m_sel.group(1))
            tools.setdefault(tid, {"size": None, "holes": []})
            current_tool = tid
            continue

        m_coord = coord_re.match(line)
        if m_coord and current_tool is not None:
            x_raw, y_raw = m_coord.groups()
            x = int(x_raw) * scale
            y = int(y_raw) * scale
            tools[current_tool]["holes"].append((x, y))

    return units, frac_digits, tools


def write_gcode(units, frac_digits, tools, out_path: Path):
    with out_path.open("w", encoding="utf-8") as g:
        g.write("(Excellon converted to G-code)\n")
        g.write(f"(Units: {units}, fraction digits: {frac_digits})\n")
        valid_tools = {tid: data for tid, data in tools.items() if data["holes"]}
        g.write("(Tools: " + ", ".join(
            f"T{tid} {data['size']}mm" for tid, data in sorted(valid_tools.items())
        ) + ")\n")

        g.write("G90 G94\n")  # absolute, feed per minute
        g.write("G21\n" if units == "METRIC" else "G20\n")
        g.write("G0 Z{:.3f}\n".format(SAFE_Z))
        g.write(f"M3 S{int(RPM)}\n")

        for tid, data in sorted(tools.items()):
            holes = data["holes"]
            if not holes:
                continue
            size = data["size"]
            g.write("\n(==== Tool T{}  Diameter={} mm ====)\n".format(
                tid, size if size is not None else "unknown"
            ))
            g.write("M0 (Change to tool T{} diameter {} mm)\n".format(
                tid, size if size is not None else "unknown"
            ))
            g.write(f"M3 S{int(RPM)}\n")

            for i, (x, y) in enumerate(holes, start=1):
                g.write(f"(Hole {i})\n")
                g.write("G0 Z{:.3f}\n".format(SAFE_Z))
                g.write("G0 X{:.3f} Y{:.3f}\n".format(x, y))
                g.write("G1 Z{:.3f} F{:.3f}\n".format(DRILL_Z, FEED))
                g.write("G0 Z{:.3f}\n".format(SAFE_Z))

        g.write("\nM5\n")
        g.write("G0 Z{:.3f}\n".format(SAFE_Z))
        g.write("G0 X0 Y0\n")
        g.write("M2\n")


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 2:
        print("Usage: python excellon_to_gcode.py input.drl output.nc")
        return 1

    in_file = Path(argv[0])
    out_file = Path(argv[1])

    if not in_file.exists():
        print(f"Input not found: {in_file}")
        return 1

    units, frac_digits, tools = parse_excellon(in_file)
    write_gcode(units, frac_digits, tools, out_file)

    print(f"Done. Units={units}, fraction_digits={frac_digits}")
    valid_tools = {tid: data for tid, data in tools.items() if data["holes"]}
    print("Tools:", ", ".join(f"T{tid} ({data['size']} mm)" for tid, data in sorted(valid_tools.items())))
    print(f"G-code written to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
