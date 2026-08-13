#!/usr/bin/env python3
"""Run the bundled OpenCOR composites and write a self-contained HTML report.

    python demo/demo_report.py   # writes demo/report.html

Runs entirely offline against the bundled CellML fixtures:
  * decay        — OpenCORProcess time-driven bridge (per-step state)
  * van-der-pol  — OpenCORUTCStep one-shot trajectory
"""
from __future__ import annotations

import json
from pathlib import Path

from process_bigraph import Composite, gather_emitter_results
from pbg_opencor.composites import build_composite

HERE = Path(__file__).resolve().parent


def _run_decay():
    sim = build_composite("decay")
    sim.run(10.0)
    rows = gather_emitter_results(sim)[("emitter",)]
    times = [r["time"] for r in rows]
    x = [r["state"].get("main/x", 0.0) for r in rows]
    return times, x


def _run_vdp():
    sim = build_composite("van-der-pol", overrides={"end_time": 20.0, "number_of_steps": 400})
    sim.run(1.0)
    res = gather_emitter_results(sim)[("emitter",)][-1]["result"]
    return res["time"], res["state"]["main/x"], res["state"]["main/y"]


def _plot(div_id, traces, title, xlab, ylab):
    return (
        f'<div id="{div_id}" style="height:380px"></div>'
        f'<script>Plotly.newPlot("{div_id}",{json.dumps(traces)},'
        f'{{title:{json.dumps(title)},margin:{{l:55,r:15,t:40,b:45}},'
        f'xaxis:{{title:{json.dumps(xlab)}}},yaxis:{{title:{json.dumps(ylab)}}},'
        f'legend:{{orientation:"h",y:-0.2}}}},{{responsive:true,displayModeBar:false}});</script>'
    )


def main() -> int:
    dt, dx = _run_decay()
    vt, vx, vy = _run_vdp()

    decay_html = _plot(
        "decay", [{"x": dt, "y": dx, "type": "scatter", "mode": "lines+markers",
                   "name": "main/x"}],
        "First-order decay (OpenCORProcess, CVODE)", "time", "x")
    vdp_html = _plot(
        "vdp",
        [{"x": vt, "y": vx, "type": "scatter", "mode": "lines", "name": "main/x"},
         {"x": vt, "y": vy, "type": "scatter", "mode": "lines", "name": "main/y"}],
        "Van der Pol oscillator (OpenCORUTCStep, CVODE)", "time", "value")

    html = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<title>pbg-opencor demo</title>"
        "<script src='https://cdn.plot.ly/plotly-2.27.0.min.js'></script>"
        "<style>body{font-family:system-ui,sans-serif;max-width:900px;margin:2rem auto;"
        "padding:0 1rem;color:#222}h1{font-size:1.4rem}p{color:#555}</style></head><body>"
        "<h1>pbg-opencor — CellML models via libOpenCOR</h1>"
        "<p>Two CellML models run through the OpenCOR process-bigraph wrappers, "
        "integrated by SUNDIALS CVODE. Everything below is computed by the real "
        "libOpenCOR engine.</p>"
        f"{decay_html}<hr>{vdp_html}"
        "</body></html>"
    )
    out = HERE / "report.html"
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
