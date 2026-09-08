"""Table tests for the pure calendar-bucketing logic app.py uses to drive
USAU's new core.pipeline.run_pipeline path (see the USAU v2 cutover task):
the isOngoing/isUpcoming/isRecentlyEnded date predicates (which now take
`date` objects directly -- previously `datetime`), and scrapeCalendar()'s
discover-and-bucket logic, which calls UsauSource().discover(year) directly
(no subprocess, no CSV) and buckets the resulting EventRefs by date using
those same predicates.

No network, no filesystem, no Flask app instance -- these are the same
pure functions/logic scrapeCalendar() uses, with UsauSource.discover()
faked out via monkeypatch so no real HTTP/Selenium/Tor happens.
"""
from datetime import date, timedelta

import app
from core.source import EventRef


TODAY = date.today()


class TestIsOngoing:
    def test_today_within_range_is_ongoing(self):
        assert app.isOngoing(TODAY - timedelta(days=1), TODAY + timedelta(days=1))

    def test_today_equals_start_is_ongoing(self):
        assert app.isOngoing(TODAY, TODAY + timedelta(days=1))

    def test_today_equals_end_is_ongoing(self):
        assert app.isOngoing(TODAY - timedelta(days=1), TODAY)

    def test_future_start_is_not_ongoing(self):
        assert not app.isOngoing(TODAY + timedelta(days=1), TODAY + timedelta(days=2))

    def test_past_end_is_not_ongoing(self):
        assert not app.isOngoing(TODAY - timedelta(days=2), TODAY - timedelta(days=1))


class TestIsUpcoming:
    def test_within_ten_days_is_upcoming(self):
        assert app.isUpcoming(TODAY + timedelta(days=10))

    def test_today_is_not_upcoming(self):
        assert not app.isUpcoming(TODAY)

    def test_eleven_days_out_is_not_upcoming(self):
        assert not app.isUpcoming(TODAY + timedelta(days=11))

    def test_past_start_is_not_upcoming(self):
        assert not app.isUpcoming(TODAY - timedelta(days=1))


class TestIsRecentlyEnded:
    def test_within_thirty_days_is_recently_ended(self):
        assert app.isRecentlyEnded(TODAY - timedelta(days=30))

    def test_yesterday_is_recently_ended(self):
        assert app.isRecentlyEnded(TODAY - timedelta(days=1))

    def test_today_is_not_recently_ended(self):
        assert not app.isRecentlyEnded(TODAY)

    def test_thirty_one_days_ago_is_not_recently_ended(self):
        assert not app.isRecentlyEnded(TODAY - timedelta(days=31))


class _StubUsauSource:
    """Fake UsauSource used to drive scrapeCalendar()'s bucketing logic
    without any real HTTP/Selenium/Tor -- mirrors test_pipeline.py's
    _FlakySource pattern of faking a Source's discover() to isolate the
    caller's own logic."""

    def __init__(self, refs):
        self._refs = refs

    def discover(self, year):
        return self._refs


class TestScrapeCalendar:
    def test_buckets_refs_by_date_into_the_three_usau_event_ref_lists(self, monkeypatch):
        ongoing_ref = EventRef(
            url="https://play.usaultimate.org/events/ongoing/schedule/Women/CollegeWomen/",
            start_date=TODAY - timedelta(days=1),
            end_date=TODAY + timedelta(days=1),
        )
        upcoming_ref = EventRef(
            url="https://play.usaultimate.org/events/upcoming/schedule/Women/CollegeWomen/",
            start_date=TODAY + timedelta(days=5),
            end_date=TODAY + timedelta(days=6),
        )
        recently_ended_ref = EventRef(
            url="https://play.usaultimate.org/events/ended/schedule/Women/CollegeWomen/",
            start_date=TODAY - timedelta(days=20),
            end_date=TODAY - timedelta(days=19),
        )
        refs = [ongoing_ref, upcoming_ref, recently_ended_ref]

        monkeypatch.setattr(app, "UsauSource", lambda: _StubUsauSource(refs))

        app.scrapeCalendar()

        assert app.ongoingUsauEventRefs == [ongoing_ref]
        assert app.upcomingUsauEventRefs == [upcoming_ref]
        assert app.recentlyEndedUsauEventRefs == [recently_ended_ref]

    def test_refs_with_no_start_date_are_skipped(self, monkeypatch):
        no_date_ref = EventRef(
            url="https://play.usaultimate.org/events/tbd/schedule/Women/CollegeWomen/",
            start_date=None,
            end_date=None,
        )
        monkeypatch.setattr(app, "UsauSource", lambda: _StubUsauSource([no_date_ref]))

        app.scrapeCalendar()

        assert app.ongoingUsauEventRefs == []
        assert app.upcomingUsauEventRefs == []
        assert app.recentlyEndedUsauEventRefs == []
