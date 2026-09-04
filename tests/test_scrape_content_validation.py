"""Table tests for the two pure content-validation checks
scrape.py's makeProxiedRequestSelenium uses to decide whether a Selenium
page load actually returned the requested tournament (vs. a blank/failed
navigation, or a non-schedule URL the check doesn't apply to) before
trusting page_source.

extractBreadcrumbTournamentSlug: see the USAU scraper bug where a blank
`<html><head></head><body></body></html>` response was silently returned as
a "successful" fetch because validation only fired when breadcrumbs were
present.

expectedTournamentSlugFromUrl: see the follow-up regression where tightening
the above check made every team-page fetch fail too -- team page URLs
(".../events/teams/?EventTeamId=...") also contain "/events/" but aren't
schedule pages and never carry a matching breadcrumb, so they must be
excluded from validation, not just tolerated when breadcrumbs are missing.

No network, no Selenium -- plain strings/HTML in, slug or None out.
"""
from scrape import expectedTournamentSlugFromUrl, extractBreadcrumbTournamentSlug


def _breadcrumbs_html(*, home_href="/", tournament_href=None, only_one_link=False):
    if tournament_href is None:
        links = f'<a href="{home_href}">Home</a>'
    elif only_one_link:
        links = f'<a href="{tournament_href}">Only Link</a>'
    else:
        links = f'<a href="{home_href}">Home</a><a href="{tournament_href}">Tournament</a>'
    return f'<html><body><div class="breadcrumbs">{links}</div></body></html>'


class TestExtractBreadcrumbTournamentSlug:
    def test_real_page_returns_slug(self):
        html = _breadcrumbs_html(tournament_href="/events/2026-Pro-Championships/")
        assert extractBreadcrumbTournamentSlug(html) == "2026-Pro-Championships"

    def test_blank_page_has_no_breadcrumbs_returns_none(self):
        assert extractBreadcrumbTournamentSlug("<html><head></head><body></body></html>") is None

    def test_missing_breadcrumbs_div_returns_none(self):
        assert extractBreadcrumbTournamentSlug("<html><body><p>hi</p></body></html>") is None

    def test_breadcrumbs_with_only_one_link_returns_none(self):
        html = _breadcrumbs_html(tournament_href="/events/foo/", only_one_link=True)
        assert extractBreadcrumbTournamentSlug(html) is None

    def test_second_link_with_no_href_returns_none(self):
        html = (
            '<html><body><div class="breadcrumbs">'
            '<a href="/">Home</a><a>Tournament</a>'
            "</div></body></html>"
        )
        assert extractBreadcrumbTournamentSlug(html) is None

    def test_trailing_slash_is_stripped(self):
        html = _breadcrumbs_html(tournament_href="/events/Some-Event/")
        assert extractBreadcrumbTournamentSlug(html) == "Some-Event"


class TestExpectedTournamentSlugFromUrl:
    def test_schedule_url_returns_slug(self):
        url = "http://play.usaultimate.org/events/2026-Pro-Championships/schedule/Men/Club-Men/"
        assert expectedTournamentSlugFromUrl(url) == "2026-Pro-Championships"

    def test_team_page_url_returns_none(self):
        url = "https://play.usaultimate.org/events/teams/?EventTeamId=Fwe9McnuiOZ18hBXQcHIR%2bSycuDBY1KeVsaWoQ7BG8o%3d"
        assert expectedTournamentSlugFromUrl(url) is None

    def test_url_without_events_returns_none(self):
        assert expectedTournamentSlugFromUrl("https://usaultimate.org/club/schedule/") is None

    def test_events_url_without_schedule_segment_returns_none(self):
        assert expectedTournamentSlugFromUrl("https://play.usaultimate.org/events/2026-Foo/") is None
