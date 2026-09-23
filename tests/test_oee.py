import pytest

from ingestor.oee import availability, compute_oee, performance, quality


def test_textbook_example():
    # 8 h shift, 60 min stopped -> run 420 min; ideal cycle 1.0 s; 19,271 parts, 423 scrap.
    run_s, down_s = 420 * 60, 60 * 60
    r = compute_oee(run_s, 0, down_s, good=19271 - 423, scrap=423, ideal_cycle_time_s=1.0)
    assert r.availability == pytest.approx(0.875)
    assert r.performance == pytest.approx(19271 / 25200)
    assert r.quality == pytest.approx(18848 / 19271)
    assert r.oee == pytest.approx(r.availability * r.performance * r.quality)
    assert r.oee == pytest.approx(0.6544, abs=1e-4)


def test_idle_counts_as_availability_loss():
    assert availability(run_s=30, idle_s=20, down_s=10) == pytest.approx(0.5)


def test_performance_capped_at_one():
    # Reported parts faster than ideal (e.g. wrong ideal cycle time) never exceed 100 %.
    assert performance(total_parts=200, ideal_cycle_time_s=1.0, run_s=100) == 1.0


def test_undefined_components_are_none():
    assert availability(0, 0, 0) is None
    assert performance(0, 2.0, 0) is None
    assert quality(0, 0) is None
    r = compute_oee(0, 0, 60, good=0, scrap=0, ideal_cycle_time_s=2.0)
    assert r.availability == 0.0 and r.performance is None and r.quality is None and r.oee is None


def test_perfect_machine():
    r = compute_oee(3600, 0, 0, good=1800, scrap=0, ideal_cycle_time_s=2.0)
    assert (r.availability, r.performance, r.quality, r.oee) == (1.0, 1.0, 1.0, 1.0)


@pytest.mark.parametrize("args", [(-1, 0, 0), (0, -1, 0), (0, 0, -1)])
def test_negative_durations_rejected(args):
    with pytest.raises(ValueError):
        availability(*args)


def test_invalid_inputs_rejected():
    with pytest.raises(ValueError):
        performance(10, 0, 100)
    with pytest.raises(ValueError):
        quality(-1, 0)
