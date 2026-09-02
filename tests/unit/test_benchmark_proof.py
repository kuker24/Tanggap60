def _check_benchmark(failed, success, runs, hermes_success, fallback, p95, max_t, ram, disk, hermes_configured=True):
    if hermes_configured:
        ok = (
            failed == 0
            and success == runs
            and hermes_success == runs
            and fallback == 0
            and p95 < 60
            and max_t < 60
            and ram >= 1024
            and disk >= 2048
        )
    else:
        ok = failed == 0 and success == runs and p95 < 60 and max_t < 60 and ram >= 1024 and disk >= 2048
    return ok

def test_benchmark_9_10_hermes_fails():
    assert _check_benchmark(0, 10, 10, 9, 1, 20, 20, 2000, 3000) is False

def test_benchmark_10_10_pass():
    assert _check_benchmark(0, 10, 10, 10, 0, 20, 20, 2000, 3000) is True

def test_benchmark_reasoning_fallback_fails_even_if_app_success():
    assert _check_benchmark(0, 10, 10, 10, 1, 20, 20, 2000, 3000) is False

def test_benchmark_no_hermes_allows_no_cli():
    assert _check_benchmark(0, 10, 10, 0, 0, 20, 20, 2000, 3000, hermes_configured=False) is True
