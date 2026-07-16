"""Tests for the 2GIS link resolver (fam/geo2gis.py).

Network is never touched: the redirect-follow seam _fetch_location is
injected. Mirrors weather.py's _urlopen injection discipline.
"""
from fam import geo2gis


# --- T1: _extract_lonlat (pure, no network) --------------------------------

def test_extract_from_geo_url():
    # long /geo/ link: .../geo/<id>/LON,LAT (order is LON,LAT)
    url = "https://2gis.kz/almaty/geo/70000001030764296/76.781529,43.233821"
    assert geo2gis._extract_lonlat(url) == (43.233821, 76.781529)


def test_extract_from_branches_url_uses_path_not_map_center():
    # real short-link target: coords in path, ?m=<center> is a DIFFERENT point
    url = ("https://2gis.kz/almaty/branches/70000001022013095/firm/"
           "70000001022013096/76.899298,43.205156?m=76.893175,43.218503/12")
    assert geo2gis._extract_lonlat(url) == (43.205156, 76.899298)


def test_extract_none_when_no_coords():
    assert geo2gis._extract_lonlat("https://2gis.kz/almaty/firm/12345") is None


def test_extract_rejects_out_of_range():
    # lat 200 is impossible -> reject (guards against swaps/garbage)
    assert geo2gis._extract_lonlat("https://2gis.kz/x/76.0,200.0") is None


def test_extract_ignores_query_only_coords():
    # coords ONLY in ?m= (map center), none in path -> None
    assert geo2gis._extract_lonlat("https://2gis.kz/almaty/firm/1?m=76.8,43.2/12") is None


# --- T2: resolve_place_coords direct parse + host guard (no network) --------

def test_resolve_direct_no_network():
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return None
    url = "https://2gis.kz/almaty/geo/700/76.781529,43.233821"
    assert geo2gis.resolve_place_coords(url, _fetch_location=fetch) == (43.233821, 76.781529)
    assert calls == []  # direct parse -> no network


def test_resolve_non_2gis_host_guarded():
    calls = []
    def fetch(url, timeout):
        calls.append(url)
        return "https://2gis.kz/x/76.0,43.0"
    # non-2GIS host, no direct coords -> None, network NOT touched (anti-SSRF)
    assert geo2gis.resolve_place_coords("https://evil.example/redir", _fetch_location=fetch) is None
    assert calls == []


# --- T3: short-link expansion (fetch seam injected) ------------------------

def test_resolve_short_link_expands():
    def fetch(url, timeout):
        assert "go.2gis.com" in url
        return ("https://2gis.kz/almaty/branches/70000001022013095/firm/"
                "70000001022013096/76.899298,43.205156?m=76.893175,43.218503/12")
    got = geo2gis.resolve_place_coords("https://go.2gis.com/01L46", _fetch_location=fetch)
    assert got == (43.205156, 76.899298)


def test_resolve_short_link_location_without_coords():
    def fetch(url, timeout):
        return "https://2gis.kz/almaty/firm/12345"
    assert geo2gis.resolve_place_coords("https://go.2gis.com/01L46", _fetch_location=fetch) is None


def test_resolve_short_link_fetch_failure():
    def fetch(url, timeout):
        return None  # network error / no Location
    assert geo2gis.resolve_place_coords("https://go.2gis.com/01L46", _fetch_location=fetch) is None


def test_resolve_short_link_no_infinite_recursion():
    # Location resolves to ANOTHER short link -> at most one expansion, then None
    def fetch(url, timeout):
        return "https://go.2gis.com/another"
    assert geo2gis.resolve_place_coords("https://go.2gis.com/01L46", _fetch_location=fetch) is None


def test_resolve_never_raises_on_bad_input():
    assert geo2gis.resolve_place_coords("not a url", _fetch_location=lambda u, t: None) is None
    assert geo2gis.resolve_place_coords("", _fetch_location=lambda u, t: None) is None


def test_is_2gis_link():
    assert geo2gis.is_2gis_link("https://go.2gis.com/01L46")
    assert geo2gis.is_2gis_link("https://2gis.kz/almaty/geo/1/76.0,43.0")
    assert not geo2gis.is_2gis_link("https://maps.google.com/x")
    assert not geo2gis.is_2gis_link("Invictus, ул. Абая 10")
    assert not geo2gis.is_2gis_link("")


# --- T4: scheme guard (only http/https are ever expanded) ------------------

def test_non_http_scheme_rejected_no_fetch():
    # host allowlist alone still lets ftp://go.2gis.com/... reach urllib's
    # FTP handler; the scheme guard must reject it before any fetch, and
    # before the inline-coords fast path too.
    called = []

    def fetch(url, timeout):
        called.append(url)
        return None

    assert geo2gis.resolve_place_coords(
        "ftp://go.2gis.com/01L46", _fetch_location=fetch) is None
    assert called == []


def test_is_2gis_link_rejects_non_http():
    assert geo2gis.is_2gis_link("ftp://go.2gis.com/abc") is False
    assert geo2gis.is_2gis_link("https://go.2gis.com/abc") is True
