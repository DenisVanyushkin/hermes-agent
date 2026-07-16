"""Resolve 2GIS map links to (lat, lon) coordinates.

Two shapes of 2GIS link occur in practice:

  * a "long" link that already carries the place coordinates in its path,
    e.g. .../geo/<id>/76.781529,43.233821 or
    .../branches/<id>/firm/<id>/76.899298,43.205156?m=76.8,43.2/12 -- the
    coordinate order is LON,LAT (longitude first), and the ?m=<center>
    query is the map centre (a DIFFERENT point), never the place;

  * a "short" go.2gis.com/<code> link that carries NO coordinates and must
    be expanded via one HTTP redirect: GET (HEAD returns 204) yields a 307
    whose Location header is the long link above. The final 2gis.kz page
    itself 403s bots, so we read the Location and never follow to the end.

Design mirrors weather.py: stdlib only, the network seam (_fetch_location)
is injected so unit tests never touch the real network, and the public
function NEVER raises -- any failure returns None, meaning "no coordinates
resolved" (the caller then stores the place without coords, as before).
"""
import sys
import urllib.request
from urllib.parse import urlsplit

# Hosts we will expand a redirect for. A user-pasted link to any other host
# is never fetched (anti-SSRF): unknown host + no inline coords -> None.
ALLOWED_HOSTS = ("go.2gis.com", "2gis.kz", "2gis.ru", "2gis.com")

DEFAULT_TIMEOUT = 8


def _scheme_ok(url):
    # Host allowlist alone still lets ftp://go.2gis.com/... reach
    # urllib's FTP handler; only web URLs are ever expanded.
    try:
        return urlsplit(url).scheme.lower() in ("http", "https")
    except Exception:
        return False


def is_2gis_link(text):
    """True if text looks like a 2GIS URL on an allowed host."""
    if not text or "://" not in text:
        return False
    text = text.strip()
    host = urlsplit(text).hostname or ""
    host = host.lower()
    if not _scheme_ok(text):
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _host_allowed(url):
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def _is_short(url):
    host = (urlsplit(url).hostname or "").lower()
    return host == "go.2gis.com" or host.endswith(".go.2gis.com")


def _extract_lonlat(url):
    """Return (lat, lon) from a URL whose path ends in a LON,LAT segment.

    The coordinates are the LAST path segment matching <float>,<float>,
    taken from the path only (the query string, e.g. ?m=<center>, is
    dropped first). Order in the URL is LON,LAT; we return (lat, lon) to
    match fam places' (lat, lon) convention. Out-of-range values are
    rejected (guards against swapped/garbage coordinates). None if no such
    segment.
    """
    try:
        path = urlsplit(url).path
    except Exception:
        return None
    best = None
    for seg in path.split("/"):
        if seg.count(",") != 1:
            continue
        a, b = seg.split(",")
        try:
            lon, lat = float(a), float(b)
        except ValueError:
            continue
        if not (-180.0 <= lon <= 180.0 and -90.0 <= lat <= 90.0):
            continue
        best = (lat, lon)  # keep the last valid segment
    return best


class _RedirectCaught(Exception):
    def __init__(self, location):
        self.location = location


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Capture the first redirect target instead of following it."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise _RedirectCaught(newurl)


def _default_fetch_location(url, timeout):
    """One GET that returns the redirect Location (absolute) or None.

    Never follows the redirect (the final 2gis.kz page 403s bots) and
    never raises: any error is logged to stderr and returns None.
    """
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(url, timeout=timeout) as resp:
            # No redirect (e.g. 204/200): nothing to expand.
            return resp.headers.get("Location")
    except _RedirectCaught as e:
        return e.location
    except Exception as e:  # URLError, timeout, HTTPError, ...
        print(f"geo2gis: fetch failed for {url}: {e}", file=sys.stderr)
        return None


def resolve_place_coords(url, *, timeout=DEFAULT_TIMEOUT, _fetch_location=None):
    """Resolve a 2GIS link to (lat, lon), or None.

    1. If the URL already carries coordinates in its path, return them with
       zero network access (covers long /geo/ and /branches/ links).
    2. Otherwise, if it is a short go.2gis.com link, expand it via ONE
       redirect and parse the Location. At most one expansion (a Location
       that is itself short -> None, no recursion).
    3. Non-2GIS host with no inline coords -> None, network untouched.

    Never raises. None means "no coordinates" -- the caller stores the
    place without them.
    """
    if not url or "://" not in url:
        return None
    url = url.strip()

    if not _scheme_ok(url):
        return None

    direct = _extract_lonlat(url)
    if direct is not None:
        return direct

    if not _is_short(url) or not _host_allowed(url):
        return None

    fetch = _fetch_location or _default_fetch_location
    try:
        location = fetch(url, timeout)
    except Exception as e:
        print(f"geo2gis: resolve failed for {url}: {e}", file=sys.stderr)
        return None
    if not location:
        return None
    # One expansion only: do not chase a Location that is itself a short link.
    if _is_short(location):
        return None
    return _extract_lonlat(location)
