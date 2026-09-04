"""Table tests for extractBreadcrumbTournamentSlug, the pure content-check
scrape.py's makeProxiedRequestSelenium uses to decide whether a Selenium
page load actually returned the requested tournament (vs. a blank/failed
navigation) before trusting page_source. See the USAU scraper bug where a
blank `<html><head></head><body></body></html>` response was silently
returned as a "successful" fetch because validation only fired when
breadcrumbs were present.

No network, no Selenium -- plain HTML strings in, slug or None out.
"""
from scrape import extractBreadcrumbTournamentSlug


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
