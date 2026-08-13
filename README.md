# pbg-opencor

Process-bigraph wrappers for **OpenCOR / CellML**, backed by
[libOpenCOR](https://opencor.ws). This is the CellML counterpart to the
SBML-focused [`pbg-copasi`](https://github.com/vivarium-collective/pbg-copasi)
and [`pbg-tellurium`](https://github.com/vivarium-collective/pbg-tellurium)
wrappers: it lets **Physiome Model Repository** CellML models run inside a
process-bigraph composite, with SUNDIALS (CVODE / KINSOL) doing the
integration.

Physiome / PMR models are distributed as CellML and generally require OpenCOR
to run. `libOpenCOR` is OpenCOR's library form — a pip-installable wheel that
loads a CellML file, builds a SED-ML simulation around it, and integrates it —
so these wrappers give you the real OpenCOR engine without the desktop GUI.

## What it wraps

| Class | Kind | What it does |
|---|---|---|
| `OpenCORProcess` | `Process` | Time-driven bridge. Integrates the model one `interval` per `update()`; a sibling process can write state variables in (closed-loop coupling). Emits absolute `state`, `rates`, `constants`, `variables`, `time`. |
| `OpenCORUTCStep` | `Step` | One-shot **uniform time course**. Runs the full trajectory and emits it as arrays (`result` = `{time, state, rates, variables, constants}`). |
| `OpenCORSteadyStateStep` | `Step` | Runs the model to equilibrium over a long horizon and emits the final `state` (matching the "run-to-steady-state" semantics of the COPASI/Tellurium siblings). |

Every variable is keyed by its fully-qualified CellML name, `component/variable`
(e.g. `membrane/V`), so multi-component Physiome models never collide.

## Installation

```bash
uv venv .venv && source .venv/bin/activate
uv pip install -e ".[dev]"     # editable, from a clone
```

`libopencor` ships as a binary wheel; no OpenCOR desktop install is needed.

## Quick start

```python
from process_bigraph import Composite, gather_emitter_results
from pbg_opencor.composites import make_opencor_utc_document, register_opencor

core = register_opencor()

# Any CellML file — local path, or a Physiome Model Repository raw URL.
doc = make_opencor_utc_document(
    model_file="tests/fixtures/van_der_pol.cellml",
    end_time=20.0, number_of_steps=400)

sim = Composite({"state": doc}, core=core)
sim.run(1.0)                                  # the Step runs once
result = gather_emitter_results(sim)[("emitter",)][-1]["result"]
print(result["time"][:3], result["state"].keys())
```

### Running a real Physiome model

```python
from process_bigraph import allocate_core
from pbg_opencor.processes import OpenCORUTCStep

core = allocate_core(); core.register_link("OpenCORUTCStep", OpenCORUTCStep)
# model_source accepts a path OR a URL (libOpenCOR fetches it via libcurl):
step = OpenCORUTCStep(config={
    "model_source": "https://models.physiomeproject.org/workspace/4e4/@@rawfile/"
                    "<hash>/ohara_rudy_cipa_v1_2017.cellml",
    "end_time": 1000.0, "number_of_steps": 1000}, core=core)
traj = step.update({})["result"]
V = traj["state"]["membrane/v"]               # cardiac action potential, mV
```

The O'Hara-Rudy CiPA 2017 cardiac model (49 state variables, 256 computed
variables) runs unmodified through this wrapper.

## Composite specs

Two declarative `*.composite.yaml` specs ship in `pbg_opencor/composites/` and
are discovered by the dashboard's composite explorer once the package is
installed in a workspace:

| Spec | Class | Model |
|---|---|---|
| `decay` | `OpenCORProcess` | first-order decay `dx/dt = -a·x` |
| `van-der-pol` | `OpenCORUTCStep` | Van der Pol oscillator |

```python
from pbg_opencor.composites import build_composite, list_composite_specs
print(list_composite_specs())                 # ['decay', 'van-der-pol']
sim = build_composite("van-der-pol", overrides={"end_time": 20.0})
sim.run(1.0)
```

## Config reference

Shared by all three classes:

| Key | Default | Meaning |
|---|---|---|
| `model_source` | `""` | path or URL to a `.cellml` file |
| `model_file` | `""` | local path (takes precedence over `model_source`) |
| `model_string` | `""` | inline CellML source |
| `ode_solver` | `"cvode"` | `cvode`, `euler`, `heun`, `rk2`, `rk4` |
| `absolute_tolerance` | `1e-9` | CVODE abs tolerance |
| `relative_tolerance` | `1e-7` | CVODE rel tolerance |
| `maximum_step` | `-1` | CVODE max step (`<=0` → solver default) |
| `state_overrides` | `{}` | `{component/variable: initial_value}` |
| `constant_overrides` | `{}` | `{component/variable: value}` |

`OpenCORUTCStep` adds `start_time`, `end_time`, `number_of_steps`;
`OpenCORSteadyStateStep` adds `settle_time`, `number_of_steps`.

## Architecture

`libopencor.File(...)` loads the CellML; `SedDocument(file)` auto-builds a
model + uniform-time-course simulation + task. The wrappers configure the
simulation (solver, horizon), apply parameter/state overrides as
`SedChangeAttribute`s, instantiate + run, and read results back from the
`SedInstanceTask` (`state`, `rate`, `constant`, `computed_constant`,
`algebraic_variable`, `voi`). `OpenCORProcess` re-instantiates each step with
the tracked state re-applied as initial values, which is what makes it both a
faithful continuation and composable with sibling processes.

## Limitations

- CVODE surfaces genuine solver failures (stiff models) as a clear
  `RuntimeError` rather than silently returning garbage.
- Some older PMR models are CellML 1.0 with unresolved imports; libOpenCOR
  reports the analyser diagnostics via the same `RuntimeError`.
- `OpenCORSteadyStateStep` relaxes ODEs to a fixed point; it is not KINSOL's
  algebraic steady-state solve.

## Tests

```bash
pytest -q          # 16 tests, all offline against bundled CellML fixtures
```
