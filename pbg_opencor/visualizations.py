"""Visualization Step subclasses for pbg-opencor.

Visualizations follow the pbg-superpowers convention: each subclass overrides
``update()`` to consume per-step state via wires (like an Emitter), accumulates
history internally, and returns ``{'html': '<rendered figure>'}`` each step. The
composite spec wires the input ports to store paths.

See viva_superpowers.visualization for the base-class contract.
"""
from __future__ import annotations

from viva_superpowers.visualization import Visualization


class StateTimeSeriesPlots(Visualization):
    """Time-series HTML plot of an OpenCORProcess's state variables.

    Consumes the ``state`` map (``component/variable -> value``) and optionally
    ``time`` at each step, accumulates per-variable trajectories across calls,
    and emits a Plotly HTML figure on every update.
    """

    config_schema = {
        'title': {'_type': 'string', '_default': 'OpenCOR / CellML state variables'},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.times: list[float] = []
        self.history: dict[str, list[float]] = {}

    def inputs(self):
        return {
            'state': 'map[float]',
            'time': 'float',
        }

    def update(self, state, interval=1.0):
        t = state.get('time')
        if t is None:
            t = len(self.times) * (interval or 1.0)
        self.times.append(float(t))

        variables = state.get('state') or {}
        n = len(self.times)
        for name in variables:
            if name not in self.history:
                self.history[name] = [0.0] * (n - 1)
        for name in list(self.history.keys()):
            v = variables.get(name)
            self.history[name].append(float(v) if v is not None else 0.0)

        title = (self.config or {}).get('title', 'OpenCOR / CellML state variables')
        traces = []
        for name, ys in self.history.items():
            traces.append(
                '{"x":' + repr(self.times) + ',"y":' + repr(ys) +
                ',"type":"scatter","mode":"lines","name":"' + name + '"}'
            )
        html = (
            f'<div id="octs" style="height:380px"></div>'
            f'<script src="https://cdn.plot.ly/plotly-2.27.0.min.js"></script>'
            f'<script>Plotly.newPlot("octs",[{",".join(traces)}],'
            f'{{title:"{title}",margin:{{l:55,r:15,t:35,b:40}},'
            f'xaxis:{{title:"time"}},'
            f'yaxis:{{title:"value"}},'
            f'legend:{{orientation:"h",y:-0.2}}}},'
            f'{{responsive:true,displayModeBar:false}});</script>'
        )
        return {'html': html}
