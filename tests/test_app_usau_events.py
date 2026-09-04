"""Table tests for the pure calendar-bucketing logic app.py uses to drive
USAU's new core.pipeline.run_pipeline path (see the USAU v2 cutover task):
the CSV-row -> EventRef adapter (_usau_event_ref_from_row) and the
isOngoing/isUpcoming/isRecentlyEnded date predicates, which now take `date`
objects directly (previously `datetime`).

No network, no filesystem, no Flask app instance -- these are the same
pure functions scrapeCalendar() calls while bucketing _calendar.csv rows.
"""
from datetime import date, timedelta

import app


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


class TestUsauEventRefFromRow:
    def test_builds_event_ref_from_calendar_row(self):
        row = [
            "https://play.usaultimate.org/events/Commonwealth-Cup-2026/schedule/Women/CollegeWomen/",
            "Axton",
            "VA",
            "2026-02-21",
            "2026-02-22",
        ]
        ref = app._usau_event_ref_from_row(row, 2026)

        assert ref.url == row[0]
        assert ref.city == "Axton"
        assert ref.state == "VA"
        assert ref.start_date == date(2026, 2, 21)
        assert ref.end_date == date(2026, 2, 22)
        assert ref.extra == {"year": 2026}

    def test_extra_year_is_the_passed_scrape_year_not_parsed_from_dates(self):
        row = ["https://play.usaultimate.org/events/x/schedule/Women/CollegeWomen/", "", "", "2025-12-30", "2026-01-02"]
        ref = app._usau_event_ref_from_row(row, 2026)
        assert ref.extra["year"] == 2026
