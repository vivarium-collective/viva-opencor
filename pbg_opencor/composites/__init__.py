"""OpenCOR composite documents + composite-spec discovery.

Two flavors of composite construction live in this package (mirroring
pbg-tellurium):

1. **Hand-coded factories** — ``make_opencor_document(...)`` /
   ``make_opencor_utc_document(...)`` build a PBG state-dict programmatically
   for callers that want full control over the CellML model + wiring.

2. **Declarative ``*.composite.yaml``** — sibling files in this directory
   follow the pbg-superpowers composite-spec convention. ``build_composite()``
   loads one by name and instantiates ``process_bigraph.Composite`` with
   parameter substitution. The dashboard's composite explorer discovers these
   automatically once the package is installed in a workspace.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from process_bigraph import allocate_core
from process_bigraph.emitter import RAMEmitter

from pbg_opencor.processes import (
    OpenCORProcess,
    OpenCORUTCStep,
    OpenCORSteadyStateStep,
)

_COMPOSITES_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Hand-coded composite factories (programmatic API)
# ---------------------------------------------------------------------------

def _model_config(model_source='', model_file='', model_string=''):
    return {
        'model_source': model_source,
        'model_file': model_file,
        'model_string': model_string,
    }


def make_opencor_document(
    model_source='',
    model_file='',
    model_string='',
    ode_solver='cvode',
    absolute_tolerance=1e-9,
    relative_tolerance=1e-7,
    state_overrides=None,
    constant_overrides=None,
    interval=1.0,
):
    """Composite document wiring an OpenCORProcess (time-driven bridge) to an
    emitter. Returns a dict ready for ``Composite({'state': doc}, core=...)``.
    """
    config = _model_config(model_source, model_file, model_string)
    config.update({
        'ode_solver': ode_solver,
        'absolute_tolerance': absolute_tolerance,
        'relative_tolerance': relative_tolerance,
        'state_overrides': state_overrides or {},
        'constant_overrides': constant_overrides or {},
    })
    return {
        'opencor': {
            '_type': 'process',
            'address': 'local:OpenCORProcess',
            'config': config,
            'interval': interval,
            'inputs': {},
            'outputs': {
                'state': ['stores', 'state'],
                'rates': ['stores', 'rates'],
                'variables': ['stores', 'variables'],
                'time': ['stores', 'time'],
            },
        },
        'stores': {},
        'emitter': {
            '_type': 'step',
            'address': 'local:ram-emitter',
            'config': {
                'emit': {
                    'state': 'map[float]',
                    'rates': 'map[float]',
                    'time': 'float',
                    'global_time': 'float',
                },
            },
            'inputs': {
                'state': ['stores', 'state'],
                'rates': ['stores', 'rates'],
                'time': ['stores', 'time'],
                'global_time': ['global_time'],
            },
        },
    }


def make_opencor_utc_document(
    model_source='',
    model_file='',
    model_string='',
    start_time=0.0,
    end_time=10.0,
    number_of_steps=100,
    ode_solver='cvode',
    state_overrides=None,
    constant_overrides=None,
):
    """Composite document wiring a one-shot OpenCORUTCStep to an emitter."""
    config = _model_config(model_source, model_file, model_string)
    config.update({
        'start_time': start_time,
        'end_time': end_time,
        'number_of_steps': number_of_steps,
        'ode_solver': ode_solver,
        'state_overrides': state_overrides or {},
        'constant_overrides': constant_overrides or {},
    })
    return {
        'utc': {
            '_type': 'step',
            'address': 'local:OpenCORUTCStep',
            'config': config,
            'inputs': {},
            'outputs': {'result': ['utc_results', 'result']},
        },
        'utc_results': {},
        'emitter': {
            '_type': 'step',
            'address': 'local:ram-emitter',
            'config': {'emit': {'result': 'numeric_result'}},
            'inputs': {'result': ['utc_results', 'result']},
        },
    }


def register_opencor(core=None):
    """Return a core with the OpenCOR Process + Steps, the RAM emitter, and the
    state-time-series Visualization registered."""
    if core is None:
        core = allocate_core()
    from pbg_opencor.types import register_opencor_types
    register_opencor_types(core)
    core.register_link('OpenCORProcess', OpenCORProcess)
    core.register_link('OpenCORUTCStep', OpenCORUTCStep)
    core.register_link('OpenCORSteadyStateStep', OpenCORSteadyStateStep)
    core.register_link('ram-emitter', RAMEmitter)
    from pbg_opencor.visualizations import StateTimeSeriesPlots
    core.register_link('StateTimeSeriesPlots', StateTimeSeriesPlots)
    return core


# ---------------------------------------------------------------------------
# Declarative composite-spec loader (*.composite.yaml)
# ---------------------------------------------------------------------------

_FULL_PLACEHOLDER = re.compile(r"^\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}$")
_INLINE_PLACEHOLDER = re.compile(r"\$\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _cast(value: Any, declared_type: str | None) -> Any:
    if declared_type is None:
        return value
    if declared_type == "float":
        return float(value)
    if declared_type == "int":
        return int(value)
    if declared_type in ("string", "str"):
        return str(value)
    if declared_type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)
    return value


def _substitute(state: Any, params: dict, overrides: dict) -> Any:
    if isinstance(state, dict):
        return {k: _substitute(v, params, overrides) for k, v in state.items()}
    if isinstance(state, list):
        return [_substitute(v, params, overrides) for v in state]
    if isinstance(state, str):
        m = _FULL_PLACEHOLDER.match(state)
        if m:
            pname = m.group(1)
            pdef = params.get(pname, {})
            raw = overrides.get(pname, pdef.get("default"))
            return _cast(raw, pdef.get("type"))
        if _INLINE_PLACEHOLDER.search(state):
            return _INLINE_PLACEHOLDER.sub(
                lambda mm: str(overrides.get(mm.group(1), params.get(mm.group(1), {}).get("default", ""))),
                state,
            )
    return state


def list_composite_specs() -> list[str]:
    """Return short names of every ``*.composite.yaml`` shipped in this package."""
    return [p.name[: -len(".composite.yaml")]
            for p in sorted(_COMPOSITES_DIR.glob("*.composite.yaml"))]


def load_composite_spec(name: str) -> dict:
    """Load and parse a named composite spec (stem, no suffix)."""
    path = _COMPOSITES_DIR / f"{name}.composite.yaml"
    if not path.is_file():
        raise FileNotFoundError(f"composite spec not found: {path}")
    return yaml.safe_load(path.read_text())


def build_composite(name: str, *, overrides: dict | None = None, core=None):
    """Load a ``*.composite.yaml`` by name and instantiate a Composite.

    overrides: parameter overrides (keys must match spec.parameters)
    core:      optional pre-built core; otherwise ``register_opencor()`` is used
    """
    from process_bigraph import Composite

    spec = load_composite_spec(name)
    if not isinstance(spec, dict) or "state" not in spec or "name" not in spec:
        raise ValueError(f"composite '{name}' missing required keys (name, state)")
    if core is None:
        core = register_opencor()
    params = spec.get("parameters") or {}
    state = _substitute(spec.get("state") or {}, params, overrides or {})
    return Composite({"state": state}, core=core)
