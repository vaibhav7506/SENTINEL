from app.workers.features import due_tick


def test_early_timer_wake_does_not_repeat_previous_window():
    assert due_tick(179.999, 60, 120) is None
    assert due_tick(180.0, 60, 120) == 180
    assert due_tick(180.001, 60, 180) is None


def test_restart_and_late_cycle_use_current_bounded_window():
    assert due_tick(169.0, 60, None) == 120
    assert due_tick(245.0, 60, 180) == 240
