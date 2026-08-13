"""Tests for the OpenCOR composite factories and declarative composite specs."""
from pathlib import Path

import pytest
from process_bigraph import Composite, gather_emitter_results

from pbg_opencor.composites import (
    make_opencor_document,
    make_opencor_utc_document,
    register_opencor,
    build_composite,
    list_composite_specs,
)

FIXTURES = Path(__file__).parent / "fixtures"
DECAY = str(FIXTURES / "decay.cellml")


def test_specs_are_discoverable():
    specs = list_composite_specs()
    assert 'decay' in specs
    assert 'van-der-pol' in specs


def test_make_document_runs():
    core = register_opencor()
    doc = make_opencor_document(model_file=DECAY, interval=1.0)
    sim = Composite({'state': doc}, core=core)
    sim.run(3.0)
    results = gather_emitter_results(sim)[('emitter',)]
    assert len(results) >= 2
    assert 'main/x' in results[-1]['state']


def test_make_utc_document_runs():
    core = register_opencor()
    doc = make_opencor_utc_document(model_file=DECAY, end_time=4.0, number_of_steps=4)
    sim = Composite({'state': doc}, core=core)
    sim.run(1.0)
    results = gather_emitter_results(sim)[('emitter',)]
    traj = results[-1]['result']
    assert traj['time'][-1] == pytest.approx(4.0)


def test_build_composite_decay():
    sim = build_composite('decay')
    sim.run(2.0)
    results = gather_emitter_results(sim)[('emitter',)]
    assert 'main/x' in results[-1]['state']


def test_build_composite_van_der_pol():
    sim = build_composite('van-der-pol', overrides={'end_time': 10.0, 'number_of_steps': 50})
    sim.run(1.0)
    results = gather_emitter_results(sim)[('emitter',)]
    traj = results[-1]['result']
    assert len(traj['time']) == 51
    assert 'main/x' in traj['state']
