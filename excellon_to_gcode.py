#!/usr/bin/env python3
"""
Excellon (multi-tool) → GRBL G-code drill script with CNC origin offset.

Usage:
    python excellon_to_gcode.py input.drl output.nc
"""

import sys
import re
from pathlib import Path

# ---- Parameters you’ll likely tweak ----
SAFE_Z   = 4.0      # retract height (mm)
TOOLCHANGE_Z   = 50      # retract height (mm)
DRILL_Z  = -2.5     # drilling depth (mm)
FEED     = 90.0     # drill feed (mm/min)
RPM      = 15000    # spindle RPM

# CNC work coordinate system origin (in machine coordinates, mm)
# Example: if PCB center should be at X=100, Y=50 in CNC coordinates:
# CNC_WCS_ORIGIN_X = 100.0
# CNC_WCS_ORIGIN_Y = 50.0
CNC_WCS_ORIGIN_X = 173.16 - 75.0 + 1
CNC_WCS_ORIGIN_Y = 118.0 - 5.0 + 0.15
# ----------------------------------------


def detect_units_and_format(lines):
    """Detect METRIC/INCH and fractional digits from lines like METRIC,0000.00."""
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
            # Header usually ends at '%'
            if line.startswith("%"):
                header_finished = True

            # Tool definitions in header (e.g. T01C0.8)
            m_hdr = tool_header_re.match(line)
            if m_hdr:
                tid = int(m_hdr.group(1))
                size = float(m_hdr.group(2))
                tools.setdefault(tid, {"size": size, "holes": []})
            continue

        upper = line.upper()

        # End of program
        if upper.startswith("M30"):
            break

        # Tool definition after header (sometimes repeated)
        m_hdr = tool_header_re.match(line)
        if m_hdr:
            tid = int(m_hdr.group(1))
            size = float(m_hdr.group(2))
            tools.setdefault(tid, {"size": size, "holes": []})
            continue

        # Tool selection (e.g. T01)
        m_sel = tool_select_re.match(line)
        if m_sel and "C" not in upper:
            tid = int(m_sel.group(1))
            tools.setdefault(tid, {"size": None, "holes": []})
            current_tool = tid
            continue

        # Coordinate line
        m_coord = coord_re.match(line)
        if m_coord and current_tool is not None:
            x_raw, y_raw = m_coord.groups()
            x = int(x_raw) * scale
            y = int(y_raw) * scale
            tools[current_tool]["holes"].append((x, y))

    return units, frac_digits, tools


def write_gcode(units, frac_digits, tools, out_path: Path):
    # Ignore tools that have no holes
    valid_tools = {tid: data for tid, data in tools.items() if data["holes"]}

    with out_path.open("w", encoding="utf-8") as g:
        g.write("(Excellon converted to G-code)\n")
        g.write(f"(Units: {units}, fraction digits: {frac_digits})\n")
        g.write("(Tools: " + ", ".join(
            f"T{tid} {data['size']}mm" for tid, data in sorted(valid_tools.items())
        ) + ")\n")
        g.write(f"(CNC WCS origin: X={CNC_WCS_ORIGIN_X} Y={CNC_WCS_ORIGIN_Y})\n")

        g.write(f"G10 L2 P1 X0 Y0 (Zero the WCS OFFSET for X, Y so that MCS = WCS for X, Y)\n\n")

        g.write("G90 G94\n")  # absolute, feed per minute
        g.write("G21\n" if units == "METRIC" else "G20\n")
        g.write(f"M3 S{int(RPM)}\n")

        for tid, data in sorted(valid_tools.items()):
            holes = data["holes"]
            size = data["size"]

            g.write("G0 Z{:.3f}\n".format(TOOLCHANGE_Z))
            g.write("\n(==== Tool T{}  Diameter={} mm ====)\n".format(
                tid, size if size is not None else "unknown"
            ))
            g.write("M0 (Change to tool T{} diameter {} mm)\n".format(
                tid, size if size is not None else "unknown"
            ))
            g.write(f"M3 S{int(RPM)}\n")

            for i, (x_local, y_local) in enumerate(holes, start=1):
                # Apply CNC origin offset: local PCB coords → machine coords
                x_abs = x_local + CNC_WCS_ORIGIN_X
                y_abs = y_local + CNC_WCS_ORIGIN_Y

                g.write(f"(Hole {i} local X={x_local:.3f} Y={y_local:.3f})\n")
                #g.write("G0 Z{:.3f}\n".format(SAFE_Z))
                g.write("G0 X{:.3f} Y{:.3f}\n".format(x_abs, y_abs))
                g.write("G0 Z{:.3f}\n".format(SAFE_Z))
                g.write("G1 Z{:.3f} F{:.3f}\n".format(DRILL_Z, FEED))
                g.write("G0 Z{:.3f}\n".format(SAFE_Z))

        g.write("\nM5\n")
        g.write("G0 Z{:.3f}\n".format(TOOLCHANGE_Z))
        g.write("G0 X3 Y280\n")
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
    valid_tools = {tid: data for tid, data in tools.items() if data["holes"]}

    write_gcode(units, frac_digits, tools, out_file)

    print(f"Done. Units={units}, fraction_digits={frac_digits}")

    print("Tools:", ", ".join(
        f"T{tid} ({data['size']} mm)" for tid, data in sorted(valid_tools.items())
    ))
    print(f"CNC WCS origin: X={CNC_WCS_ORIGIN_X} Y={CNC_WCS_ORIGIN_Y}")
    print(f"G-code written to {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
