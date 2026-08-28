"""Request pacing for RequestsTransport.

`sleep` and `clock` are injected throughout so these assert on pacing
decisions without the suite ever actually sleeping.
"""
import pytest

from core.fetch import RequestsTransport, _parse_retry_after


class FakeClock:
    """Monotonic clock that only advances when told to, including by sleeps."""

    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


def make_transport(clock, **kwargs):
    kwargs.setdefault("delay_range", (1.0, 3.0))
    return RequestsTransport(sleep=clock.sleep, clock=clock, **kwargs)


def test_first_request_is_not_delayed():
    clock = FakeClock()
    t = make_transport(clock)
    assert t._throttle() == 0.0
    assert clock.slept == []


def test_delay_applied_between_requests():
    clock = FakeClock()
    t = make_transport(clock)
    t._last_request_at = clock.now

    slept = t._throttle()

    assert slept > 0
    assert len(clock.slept) == 1
    assert 1.0 <= clock.slept[0] <= 3.0


def test_delay_is_jittered_not_constant():
    clock = FakeClock()
    t = make_transport(clock, delay_range=(0.5, 5.0))

    waits = []
    for _ in range(30):
        t._last_request_at = clock.now
        waits.append(t._throttle())

    assert all(0.5 <= w <= 5.0 for w in waits)
    # A fixed delay would collapse to a single value; jitter must not.
    assert len(set(waits)) > 1


def test_elapsed_time_counts_toward_the_delay():
    """A slow response should not be *added* to the pause -- the wait is
    measured from the previous request, so time already spent counts."""
    clock = FakeClock()
    t = make_transport(clock, delay_range=(2.0, 2.0))
    t._last_request_at = clock.now

    clock.advance(0.5)
    slept = t._throttle()

    assert slept == pytest.approx(1.5)


def test_no_sleep_when_more_than_the_delay_has_already_elapsed():
    clock = FakeClock()
    t = make_transport(clock, delay_range=(2.0, 2.0))
    t._last_request_at = clock.now

    clock.advance(10.0)

    assert t._throttle() == 0.0
    assert clock.slept == []


def test_zero_delay_range_disables_throttling():
    clock = FakeClock()
    t = make_transport(clock, delay_range=(0.0, 0.0))
    t._last_request_at = clock.now

    assert t._throttle() == 0.0
    assert clock.slept == []


def test_default_transport_is_unthrottled():
    """Existing callers must be unaffected until they opt in."""
    assert RequestsTransport()._delay_range == (0.0, 0.0)


@pytest.mark.parametrize("bad", [(-1.0, 2.0), (1.0, -2.0), (5.0, 1.0)])
def test_invalid_delay_range_rejected(bad):
    with pytest.raises(ValueError):
        RequestsTransport(delay_range=bad)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("5", 5.0),
        ("0", 0.0),
        ("2.5", 2.5),
        ("-3", 0.0),
        ("Wed, 21 Oct 2026 07:28:00 GMT", None),  # HTTP-date form unsupported
        ("", None),
        (None, None),
    ],
)
def test_parse_retry_after(value, expected):
    assert _parse_retry_after(value) == expected


def test_wfdf_source_opts_into_throttling():
    from sources.wfdf.source import REQUEST_DELAY_RANGE, WfdfSource

    transport = WfdfSource().make_transport()

    assert transport._delay_range == REQUEST_DELAY_RANGE
    assert transport._delay_range[0] > 0, "WFDF should not be scraped at full speed"


def test_source_default_transport_is_plain():
    from sources.example.source import ExampleSource

    assert ExampleSource().make_transport()._delay_range == (0.0, 0.0)
