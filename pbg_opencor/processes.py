"""OpenCOR / CellML process-bigraph wrappers, backed by libOpenCOR.

libOpenCOR (https://opencor.ws) is the library form of OpenCOR: it loads a
CellML model, builds a SED-ML simulation around it, and integrates it with
SUNDIALS (CVODE for ODEs, KINSOL for the NLA subsystem). These wrappers bridge
that real engine into process-bigraph, so Physiome Model Repository CellML
models run the same way COPASI/Tellurium run SBML in the sibling wrappers.

Three surfaces, mirroring pbg-copasi / pbg-tellurium:

* ``OpenCORProcess``          — time-driven bridge Process. Integrates the model
                                one ``interval`` per ``update()``, can be coupled
                                (a sibling process may write state variables in).
* ``OpenCORUTCStep``          — one-shot uniform time course Step: run the whole
                                trajectory and emit it as arrays.
* ``OpenCORSteadyStateStep``  — run the model to equilibrium (long time course)
                                and emit the final state, matching the
                                "run-to-steady-state" semantics of the siblings.

Variable naming: libOpenCOR reports every variable as ``component/variable``
(e.g. ``membrane/V``). We keep that fully-qualified name as the map key so
multi-component Physiome models don't collide. Coupling inputs use the same
keying; a ``component/variable`` key is split on the last ``/`` to target a
change.
"""
from __future__ import annotations

import os
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from process_bigraph import Process, Step


# --------------------------------------------------------------------------- #
# Model loading / SED-ML assembly helpers                                      #
# --------------------------------------------------------------------------- #

def _resolve_model(model_source: str, model_file: str, model_string: str) -> Tuple[str, Optional[str]]:
    """Return ``(file_ref, tmp_path)``.

    ``file_ref`` is a path/URL that ``libopencor.File`` can open. If the model
    was supplied inline as ``model_string``, it is written to a temp ``.cellml``
    file whose path is returned as ``tmp_path`` (caller owns cleanup).
    """
    if model_string:
        fd, tmp = tempfile.mkstemp(suffix=".cellml")
        with os.fdopen(fd, "w") as fh:
            fh.write(model_string)
        return tmp, tmp
    ref = model_file or model_source
    if not ref:
        raise ValueError(
            "OpenCOR wrapper requires one of 'model_file', 'model_source', "
            "or 'model_string' (inline CellML).")
    return ref, None


def _new_document(file_ref: str):
    """Load ``file_ref`` as a CellML file and return a fresh SED-ML document.

    libOpenCOR auto-populates the document with one model, one uniform time
    course simulation, and one task when the CellML analyses as an ODE model.
    Raises with the CellML/analyser diagnostics if the file does not load.
    """
    import libopencor as loc

    f = loc.File(file_ref)
    if f.has_errors:
        msgs = "; ".join(f.error(i).description for i in range(f.error_count))
        raise RuntimeError(f"libOpenCOR could not load model '{file_ref}': {msgs}")
    doc = loc.SedDocument(f)
    if doc.has_errors:
        msgs = "; ".join(doc.error(i).description for i in range(doc.error_count))
        raise RuntimeError(f"libOpenCOR could not build a SED-ML document: {msgs}")
    if doc.simulation_count == 0:
        raise RuntimeError(
            f"model '{file_ref}' produced no runnable simulation "
            "(is it an ODE/DAE model?)")
    return doc


def _make_ode_solver(name: str):
    import libopencor as loc

    solvers = {
        "cvode": loc.SolverCvode,
        "forward_euler": loc.SolverForwardEuler,
        "euler": loc.SolverForwardEuler,
        "heun": loc.SolverHeun,
        "rk2": loc.SolverSecondOrderRungeKutta,
        "rk4": loc.SolverFourthOrderRungeKutta,
    }
    key = (name or "cvode").lower()
    if key not in solvers:
        raise ValueError(f"unknown ode_solver '{name}'; choose one of {sorted(solvers)}")
    return solvers[key]()


def _apply_changes(model, changes: Dict[str, float]) -> None:
    """Apply ``{component/variable: value}`` overrides to a SED-ML model.

    Sets each target's value via a SedChangeAttribute. For a constant this
    overrides the parameter; for a state variable it overrides the initial
    value (the mechanism this wrapper uses to continue integration and to
    inject coupled state each step).
    """
    import libopencor as loc

    for name, value in changes.items():
        if "/" in name:
            component, variable = name.rsplit("/", 1)
        else:
            component, variable = "", name
        model.add_change(loc.SedChangeAttribute(component, variable, repr(float(value))))


def _configure_utc(sim, *, start: float, end: float, steps: int,
                   ode_solver: str, abs_tol: float, rel_tol: float,
                   max_step: float) -> None:
    """Configure a SedUniformTimeCourse in place."""
    sim.initial_time = float(start)
    sim.output_start_time = float(start)
    sim.output_end_time = float(end)
    sim.number_of_steps = int(steps)
    solver = _make_ode_solver(ode_solver)
    if ode_solver.lower() == "cvode":
        solver.absolute_tolerance = float(abs_tol)
        solver.relative_tolerance = float(rel_tol)
        if max_step > 0:
            solver.maximum_step = float(max_step)
    sim.ode_solver = solver


def _run(doc):
    """Instantiate + run a document, returning its first SedInstanceTask.

    Raises with the instance diagnostics on failure.
    """
    inst = doc.instantiate()
    inst.run()
    if inst.has_errors:
        msgs = "; ".join(inst.error(i).description for i in range(inst.error_count))
        raise RuntimeError(f"libOpenCOR run failed: {msgs}")
    return inst.tasks[0]


def _task_names(task) -> Dict[str, List[str]]:
    return {
        "state": [task.state_name(i) for i in range(task.state_count)],
        "rate": [task.rate_name(i) for i in range(task.rate_count)],
        "constant": [task.constant_name(i) for i in range(task.constant_count)],
        "computed_constant": [task.computed_constant_name(i)
                              for i in range(task.computed_constant_count)],
        "algebraic": [task.algebraic_variable_name(i)
                      for i in range(task.algebraic_variable_count)],
    }


def _row(task, idx: int) -> Dict[str, Dict[str, float]]:
    """Read one output row (time index ``idx``) into named maps."""
    def col(getter, count, namer):
        return {namer(i): float(getter(i)[idx]) for i in range(count)}

    variables = {}
    variables.update(col(task.computed_constant, task.computed_constant_count,
                         task.computed_constant_name))
    variables.update(col(task.algebraic_variable, task.algebraic_variable_count,
                         task.algebraic_variable_name))
    return {
        "state": col(task.state, task.state_count, task.state_name),
        "rates": col(task.rate, task.rate_count, task.rate_name),
        "constants": col(task.constant, task.constant_count, task.constant_name),
        "variables": variables,
        "time": float(task.voi[idx]),
    }


# --------------------------------------------------------------------------- #
# Shared config surface                                                        #
# --------------------------------------------------------------------------- #

_BASE_CONFIG = {
    "model_source": {"_type": "string", "_default": ""},   # path or URL to a .cellml
    "model_file": {"_type": "string", "_default": ""},     # local path (takes precedence)
    "model_string": {"_type": "string", "_default": ""},   # inline CellML contents
    "ode_solver": {"_type": "string", "_default": "cvode"},
    "absolute_tolerance": {"_type": "float", "_default": 1e-9},
    "relative_tolerance": {"_type": "float", "_default": 1e-7},
    "maximum_step": {"_type": "float", "_default": -1.0},   # <=0 => solver default
    "state_overrides": {"_type": "map[float]", "_default": {}},
    "constant_overrides": {"_type": "map[float]", "_default": {}},
}


class _BaseOpenCOR:
    """Model-loading + name-discovery mixin shared by the Process and Steps."""

    def _resolve(self):
        cfg = self.config
        return _resolve_model(cfg["model_source"], cfg["model_file"], cfg["model_string"])

    def _discover(self):
        """Build once to learn variable names + the model's initial row."""
        file_ref, tmp = self._resolve()
        try:
            doc = _new_document(file_ref)
            sim = doc.simulations[0]
            _configure_utc(
                sim, start=0.0, end=1.0, steps=1,
                ode_solver=self.config["ode_solver"],
                abs_tol=self.config["absolute_tolerance"],
                rel_tol=self.config["relative_tolerance"],
                max_step=self.config["maximum_step"])
            task = _run(doc)
            names = _task_names(task)
            initial = _row(task, 0)
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        return names, initial


# --------------------------------------------------------------------------- #
# Time-driven bridge Process                                                    #
# --------------------------------------------------------------------------- #

class OpenCORProcess(_BaseOpenCOR, Process):
    """Bridge Process wrapping a CellML model integrated by libOpenCOR.

    Integrates the model forward by ``interval`` on each ``update()`` call.
    State continuation is done by re-instantiating the SED-ML document each
    step with the tracked state values (and any coupled state written in by a
    sibling process) applied as initial-value changes — so the wrapper is both
    a faithful integrator and composable.

    Inputs
    ------
    state : maybe[map[float]]
        Optional ``component/variable -> value`` overrides for state variables,
        written by the surrounding bigraph before the step is integrated.

    Outputs
    -------
    state, rates, constants, variables : overwrite[map[float]]
        Absolute values at the end of the step (state variables, their
        derivatives, model constants/parameters, and computed/algebraic
        variables respectively).
    time : overwrite[float]
        The model's variable of integration after the step.
    """

    config_schema = dict(_BASE_CONFIG)

    def __init__(self, config=None, core=None):
        super().__init__(config=config, core=core)
        self._names = None
        self._state: Dict[str, float] = {}
        self._time = 0.0
        self._last = None

    def inputs(self):
        return {"state": "maybe[map[float]]"}

    def outputs(self):
        return {
            "state": "overwrite[map[float]]",
            "rates": "overwrite[map[float]]",
            "constants": "overwrite[map[float]]",
            "variables": "overwrite[map[float]]",
            "time": "overwrite[float]",
        }

    def _build(self):
        if self._names is not None:
            return
        names, initial = self._discover()
        self._names = names
        # Apply configured state overrides on top of the model's initial values.
        self._state = dict(initial["state"])
        self._state.update(self.config["state_overrides"])
        self._time = initial["time"]
        self._last = initial

    def initial_state(self):
        self._build()
        return {
            "state": dict(self._state),
            "rates": dict(self._last["rates"]),
            "constants": dict(self._last["constants"]),
            "variables": dict(self._last["variables"]),
            "time": self._time,
        }

    def update(self, state, interval):
        self._build()

        # Merge coupled state written by the surrounding bigraph.
        incoming = (state or {}).get("state") or {}
        for name, val in incoming.items():
            if name in self._state:
                self._state[name] = float(val)

        file_ref, tmp = self._resolve()
        try:
            doc = _new_document(file_ref)
            model = doc.models[0]
            _apply_changes(model, self._state)
            _apply_changes(model, self.config["constant_overrides"])
            sim = doc.simulations[0]
            _configure_utc(
                sim, start=0.0, end=float(interval), steps=1,
                ode_solver=self.config["ode_solver"],
                abs_tol=self.config["absolute_tolerance"],
                rel_tol=self.config["relative_tolerance"],
                max_step=self.config["maximum_step"])
            task = _run(doc)
            row = _row(task, -1)  # final output row
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)

        # Track absolute state; advance the clock (libOpenCOR ran 0..interval).
        self._state = dict(row["state"])
        self._time += float(interval)
        self._last = row
        return {
            "state": dict(self._state),
            "rates": dict(row["rates"]),
            "constants": dict(row["constants"]),
            "variables": dict(row["variables"]),
            "time": self._time,
        }


# --------------------------------------------------------------------------- #
# One-shot uniform time course Step                                            #
# --------------------------------------------------------------------------- #

class OpenCORUTCStep(_BaseOpenCOR, Step):
    """Run a full uniform time course in one shot and emit the trajectory.

    Config adds ``start_time`` / ``end_time`` / ``number_of_steps`` on top of
    the shared model + solver knobs. Emits a nested ``result`` dict:
    ``{"time": [...], "state": {name: [...]}, "rates": {...},
    "variables": {...}, "constants": {name: value}}``.
    """

    config_schema = {
        **_BASE_CONFIG,
        "start_time": {"_type": "float", "_default": 0.0},
        "end_time": {"_type": "float", "_default": 10.0},
        "number_of_steps": {"_type": "integer", "_default": 100},
    }

    def inputs(self):
        return {"state": "maybe[map[float]]"}

    def outputs(self):
        return {"result": "numeric_result"}

    def _simulate(self, incoming: Dict[str, float]):
        file_ref, tmp = self._resolve()
        try:
            doc = _new_document(file_ref)
            model = doc.models[0]
            _apply_changes(model, self.config["state_overrides"])
            _apply_changes(model, self.config["constant_overrides"])
            _apply_changes(model, incoming or {})
            sim = doc.simulations[0]
            _configure_utc(
                sim, start=self.config["start_time"], end=self.config["end_time"],
                steps=self.config["number_of_steps"],
                ode_solver=self.config["ode_solver"],
                abs_tol=self.config["absolute_tolerance"],
                rel_tol=self.config["relative_tolerance"],
                max_step=self.config["maximum_step"])
            task = _run(doc)
            names = _task_names(task)

            def series(getter, count, namer):
                return {namer(i): [float(v) for v in getter(i)] for i in range(count)}

            variables = {}
            variables.update(series(task.computed_constant, task.computed_constant_count,
                                    task.computed_constant_name))
            variables.update(series(task.algebraic_variable, task.algebraic_variable_count,
                                    task.algebraic_variable_name))
            result = {
                "time": [float(v) for v in task.voi],
                "state": series(task.state, task.state_count, task.state_name),
                "rates": series(task.rate, task.rate_count, task.rate_name),
                "variables": variables,
                "constants": {names["constant"][i]: float(task.constant(i)[0])
                              for i in range(task.constant_count)},
            }
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        return result

    def initial_state(self):
        return {"result": {"time": [], "state": {}, "rates": {}, "variables": {}, "constants": {}}}

    def update(self, inputs):
        incoming = (inputs or {}).get("state") or {}
        return {"result": self._simulate(incoming)}


# --------------------------------------------------------------------------- #
# Steady-state (run-to-equilibrium) Step                                       #
# --------------------------------------------------------------------------- #

class OpenCORSteadyStateStep(_BaseOpenCOR, Step):
    """Integrate a CellML model over a long horizon and emit the final state.

    This mirrors the "run to steady state" semantics of
    ``TelluriumSteadyStateStep`` / ``CopasiSteadyStateStep``: rather than
    solving the algebraic system, it advances the ODEs to ``settle_time`` and
    returns the final row. Suited to models that relax to a fixed point.
    """

    config_schema = {
        **_BASE_CONFIG,
        "settle_time": {"_type": "float", "_default": 1000.0},
        "number_of_steps": {"_type": "integer", "_default": 1000},
    }

    def inputs(self):
        return {"state": "maybe[map[float]]"}

    def outputs(self):
        return {"steady_state": "map[float]", "results": "numeric_result"}

    def update(self, inputs):
        incoming = (inputs or {}).get("state") or {}
        file_ref, tmp = self._resolve()
        try:
            doc = _new_document(file_ref)
            model = doc.models[0]
            _apply_changes(model, self.config["state_overrides"])
            _apply_changes(model, self.config["constant_overrides"])
            _apply_changes(model, incoming)
            sim = doc.simulations[0]
            _configure_utc(
                sim, start=0.0, end=self.config["settle_time"],
                steps=self.config["number_of_steps"],
                ode_solver=self.config["ode_solver"],
                abs_tol=self.config["absolute_tolerance"],
                rel_tol=self.config["relative_tolerance"],
                max_step=self.config["maximum_step"])
            task = _run(doc)
            final = _row(task, -1)
        finally:
            if tmp and os.path.exists(tmp):
                os.remove(tmp)
        return {
            "steady_state": dict(final["state"]),
            "results": {
                "time": [final["time"]],
                "state": final["state"],
                "rates": final["rates"],
                "variables": final["variables"],
                "constants": final["constants"],
            },
        }
