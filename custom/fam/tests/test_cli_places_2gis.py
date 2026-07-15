"""CLI wiring: places add/update auto-resolve 2GIS links to coords."""
from fam import cli, places


def test_add_resolves_short_link(db, monkeypatch):
    called = {}
    def fake(url, **kw):
        called["url"] = url
        return (43.205156, 76.899298)
    monkeypatch.setattr(cli.geo2gis, "resolve_place_coords", fake)

    rc = cli.main(["places", "add", "Invictus",
                   "--address", "https://go.2gis.com/01L46"])
    assert rc == 0
    assert called["url"] == "https://go.2gis.com/01L46"
    p = places.resolve(db, "Invictus")
    assert p["lat"] == 43.205156 and p["lon"] == 76.899298


def test_add_explicit_coords_skip_resolver(db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("resolver must not be called when coords given")
    monkeypatch.setattr(cli.geo2gis, "resolve_place_coords", boom)

    rc = cli.main(["places", "add", "Дом",
                   "--address", "https://go.2gis.com/xxxx",
                   "--lat", "43.2", "--lon", "76.9"])
    assert rc == 0
    p = places.resolve(db, "Дом")
    assert p["lat"] == 43.2 and p["lon"] == 76.9


def test_add_resolver_none_saves_without_coords(db, monkeypatch):
    monkeypatch.setattr(cli.geo2gis, "resolve_place_coords", lambda url, **k: None)
    rc = cli.main(["places", "add", "Кафе",
                   "--address", "https://go.2gis.com/deadlink"])
    assert rc == 0  # not an error -- place saved without coords
    p = places.resolve(db, "Кафе")
    assert p["lat"] is None and p["lon"] is None
    assert p["address"] == "https://go.2gis.com/deadlink"


def test_add_non_2gis_address_no_resolver(db, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("resolver must not run for a non-2GIS address")
    monkeypatch.setattr(cli.geo2gis, "resolve_place_coords", boom)
    rc = cli.main(["places", "add", "Работа", "--address", "ул. Абая 10"])
    assert rc == 0
    assert places.resolve(db, "Работа")["lat"] is None


def test_update_resolves_short_link(db, monkeypatch):
    places.add(db, "Invictus", address="старый адрес"); db.commit()
    monkeypatch.setattr(cli.geo2gis, "resolve_place_coords",
                        lambda url, **k: (43.205156, 76.899298))
    rc = cli.main(["places", "update", "Invictus",
                   "--address", "https://go.2gis.com/01L46"])
    assert rc == 0
    p = places.resolve(db, "Invictus")
    assert p["lat"] == 43.205156 and p["lon"] == 76.899298


def test_update_explicit_coords_skip_resolver(db, monkeypatch):
    places.add(db, "Invictus"); db.commit()
    def boom(*a, **k):
        raise AssertionError("resolver must not be called when coords given")
    monkeypatch.setattr(cli.geo2gis, "resolve_place_coords", boom)
    rc = cli.main(["places", "update", "Invictus",
                   "--address", "https://go.2gis.com/x", "--lat", "43.1", "--lon", "76.8"])
    assert rc == 0
    p = places.resolve(db, "Invictus")
    assert p["lat"] == 43.1 and p["lon"] == 76.8
