"""Unit tests for the OpenCOR / CellML process-bigraph wrappers.

All tests run offline against the bundled CellML fixtures.
"""
import math
from pathlib import Path

import pytest
from process_bigraph import allocate_core

from pbg_opencor.processes import (
    OpenCORProcess,
    OpenCORUTCStep,
    OpenCORSteadyStateStep,
)

FIXTURES = Path(__file__).parent / "fixtures"
DECAY = str(FIXTURES / "decay.cellml")        # dx/dt = -a*x, x0=10, a=0.5
VDP = str(FIXTURES / "van_der_pol.cellml")    # Van der Pol oscillator


@pytest.fixture
def core():
    c = allocate_core()
    c.register_link('OpenCORProcess', OpenCORProcess)
    c.register_link('OpenCORUTCStep', OpenCORUTCStep)
    c.register_link('OpenCORSteadyStateStep', OpenCORSteadyStateStep)
    return c


def test_instantiation(core):
    proc = OpenCORProcess(config={'model_file': DECAY}, core=core)
    assert proc.config['ode_solver'] == 'cvode'
    assert proc.config['model_file'].endswith('decay.cellml')


def test_missing_model_raises(core):
    proc = OpenCORProcess(config={}, core=core)
    with pytest.raises(ValueError):
        proc.initial_state()


def test_initial_state(core):
    proc = OpenCORProcess(config={'model_file': DECAY}, core=core)
    state = proc.initial_state()
    assert 'state' in state and 'rates' in state and 'time' in state
    assert state['state']['main/x'] == pytest.approx(10.0)
    assert state['constants']['main/a'] == pytest.approx(0.5)
    assert state['time'] == pytest.approx(0.0)


def test_single_update_advances_time_and_integrates(core):
    proc = OpenCORProcess(config={'model_file': DECAY}, core=core)
    proc.initial_state()
    out = proc.update({}, interval=1.0)
    assert out['time'] == pytest.approx(1.0)
    # x(1) = 10 * exp(-0.5) ~ 6.0653
    assert out['state']['main/x'] == pytest.approx(10 * math.exp(-0.5), rel=1e-4)


def test_multi_step_continuation_matches_analytic(core):
    proc = OpenCORProcess(config={'model_file': DECAY}, core=core)
    proc.initial_state()
    for _ in range(4):
        out = proc.update({}, interval=1.0)
    # After 4 unit steps: x(4) = 10 * exp(-2) ~ 1.3534
    assert out['time'] == pytest.approx(4.0)
    assert out['state']['main/x'] == pytest.approx(10 * math.exp(-2), rel=1e-4)


def test_constant_override(core):
    proc = OpenCORProcess(
        config={'model_file': DECAY, 'constant_overrides': {'main/a': 2.0}},
        core=core)
    proc.initial_state()
    out = proc.update({}, interval=1.0)
    # faster decay: x(1) = 10 * exp(-2)
    assert out['state']['main/x'] == pytest.approx(10 * math.exp(-2), rel=1e-4)


def test_state_override(core):
    proc = OpenCORProcess(
        config={'model_file': DECAY, 'state_overrides': {'main/x': 5.0}},
        core=core)
    init = proc.initial_state()
    assert init['state']['main/x'] == pytest.approx(5.0)


def test_coupling_input_injected(core):
    """A sibling writing 'state' should reset the integrator's state."""
    proc = OpenCORProcess(config={'model_file': DECAY}, core=core)
    proc.initial_state()
    out = proc.update({'state': {'main/x': 100.0}}, interval=1.0)
    # started this step from x=100 => x(1) = 100 * exp(-0.5) ~ 60.65
    assert out['state']['main/x'] == pytest.approx(100 * math.exp(-0.5), rel=1e-4)


def test_utc_step_returns_trajectory(core):
    step = OpenCORUTCStep(
        config={'model_file': DECAY, 'end_time': 4.0, 'number_of_steps': 4},
        core=core)
    out = step.update({})
    res = out['result']
    assert res['time'] == pytest.approx([0.0, 1.0, 2.0, 3.0, 4.0])
    xs = res['state']['main/x']
    assert len(xs) == 5
    assert xs[0] == pytest.approx(10.0)
    assert xs[-1] == pytest.approx(10 * math.exp(-2), rel=1e-4)


def test_utc_step_solver_choice(core):
    step = OpenCORUTCStep(
        config={'model_file': VDP, 'end_time': 10.0, 'number_of_steps': 50,
                'ode_solver': 'rk4'},
        core=core)
    out = step.update({})
    res = out['result']
    assert 'main/x' in res['state']
    assert len(res['time']) == 51


def test_steady_state_decays_to_zero(core):
    step = OpenCORSteadyStateStep(
        config={'model_file': DECAY, 'settle_time': 50.0, 'number_of_steps': 500},
        core=core)
    out = step.update({})
    assert out['steady_state']['main/x'] == pytest.approx(0.0, abs=1e-6)
