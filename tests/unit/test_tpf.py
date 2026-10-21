"""Kepler target pixel files: the archive listing, the FITS reader and the fetch through the blob cache."""
import gzip

import numpy as np
import pytest
from sqlalchemy import select

from skysift.lc.tpf import TPF_BASE, fetch_tpfs, list_tpf_names, parse_tpf, tpf_listing_url
from skysift.net import ServiceUnavailable
from skysift.store.blob import LocalBlob
from skysift.store.db import ArtifactRow, QueryRow, Store
from tests.unit.synthetic_tpf import NEIGHBOUR_KIC, FakeMast, archive_season, tpf_name, tpf_quarters

KIC = 6146666
NAME = tpf_name(KIC, 3)
LISTING = (f'<html><body><ul><li><a href="/missions/kepler/target_pixel_files/0061/"> Parent Directory</a></li>'
           f'<li><a href="{NAME}"> {NAME}</a></li>'
           f'<li><a href="kplr006146666-2009131105131_spd-targ.fits.gz"> short cadence</a></li>'
           f'<li><a href="kplr006146666_tpf_lc_Q111111111111111111.tar"> tar</a></li></ul></body></html>').encode()


@pytest.fixture
def store(tmp_path):
    s = Store(f"sqlite:///{tmp_path.as_posix()}/t.db"); s.init_schema(); return s


@pytest.fixture
def blob(tmp_path):
    return LocalBlob(tmp_path / "blobs")


@pytest.fixture
def synthetic_tpf_bytes():
    return tpf_quarters("target", n_quarters=3, kic=NEIGHBOUR_KIC)[2]          # quarter 3


def outage_fetch(url, *, service, **_kw):
    raise ServiceUnavailable(f"{service} {url} -> HTTP 503")


def test_list_tpf_names_parses_the_mast_listing():
    html = ('<a href="kplr006146666-2009131105131_lpd-targ.fits.gz">x</a> '
            '<a href="kplr006146666-2009131105131_spd-targ.fits.gz">')
    assert list_tpf_names(html, 6146666) == ["kplr006146666-2009131105131_lpd-targ.fits.gz"]
    assert tpf_listing_url(6146666).endswith("/0061/006146666/")
    assert tpf_listing_url(6146666) == TPF_BASE.format(kic4="0061", kic9="006146666")
    # absolute links, duplicates and another star's files: one sorted list of this star's long-cadence files
    two = "kplr006146666-2010078095331_lpd-targ.fits.gz"
    html = (f'<a href="/missions/kepler/target_pixel_files/0061/006146666/{two}">{two}</a> <a href="{NAME}">'
            f'<a href="{NAME}"> <a href="kplr006146667-2009131105131_lpd-targ.fits.gz">')
    assert list_tpf_names(html, 6146666) == sorted([NAME, two])
    assert list_tpf_names(LISTING.decode(), 6146666) == [NAME]


def test_parse_tpf_reads_cube_quarter_and_wcs(synthetic_tpf_bytes):
    tpf = parse_tpf(synthetic_tpf_bytes, "k.fits")
    assert tpf.cube.shape[1:] == (9, 9) and tpf.quarter == 3
    col, row = tpf.pixel_of(*synthetic_tpf_bytes.target_radec)
    assert abs(col - 4.0) < 0.05 and abs(row - 4.0) < 0.05
    assert tpf.cube.shape[0] == tpf.time.size == tpf.quality.size and tpf.cube.dtype == np.float64
    assert tpf.name == "k.fits" and len(tpf.sha256) == 64 and tpf.kepler_id == NEIGHBOUR_KIC
    assert (tpf.ra_obj, tpf.dec_obj) == pytest.approx(synthetic_tpf_bytes.neighbour_radec)
    assert tpf.pixel_of(*synthetic_tpf_bytes.neighbour_radec) == pytest.approx(synthetic_tpf_bytes.pos_neighbour,
                                                                              abs=1e-6)
    assert tpf.aperture.shape == (9, 9) and int((tpf.aperture & 2).astype(bool).sum()) == 9
    assert tpf.season == archive_season(3) == 1                 # the header SEASON, not quarter % 4
    no_season = tpf_quarters("target", n_quarters=3, season=None)[2]
    assert parse_tpf(no_season).season is None


def test_parse_tpf_reads_plain_fits_and_a_failed_wcs_gives_nan(synthetic_tpf_bytes):
    tpf = parse_tpf(gzip.decompress(synthetic_tpf_bytes))
    assert tpf.quarter == 3 and tpf.cube.shape[1:] == (9, 9)
    tpf.wcs = None                                          # a header without a usable WCS
    assert all(np.isnan(tpf.pixel_of(*synthetic_tpf_bytes.target_radec)))


def test_fetch_tpfs_logs_stores_and_reuses(store, blob, synthetic_tpf_bytes):
    fetch = FakeMast({"listing": LISTING, NAME: synthetic_tpf_bytes})
    tpfs = fetch_tpfs(6146666, fetch=fetch, blob=blob, store=store, target_id="t")
    assert len(tpfs) == 1 and fetch.calls == 2 and store.counts()["artifacts"] == 1
    assert fetch.urls == [tpf_listing_url(KIC), tpf_listing_url(KIC) + NAME]
    with store.session() as s:
        art = s.scalars(select(ArtifactRow)).one()
        assert art.kind == "tpf" and art.target_id == "t" and art.url == tpf_listing_url(KIC) + NAME
        assert not art.pinned and art.sha256 == tpfs[0].sha256
        assert {q.target_id for q in s.scalars(select(QueryRow))} == {"t"}
    fetch_tpfs(6146666, fetch=fetch, blob=blob, store=store, target_id="t"); assert fetch.calls == 3   # listing only
    store.pin_target_artifacts("t")                        # the target's evidence pins its pixel files too
    with store.session() as s:
        assert s.get(ArtifactRow, tpfs[0].sha256).pinned


def test_fetch_tpfs_refetches_a_file_the_cache_lost(store, blob, synthetic_tpf_bytes):
    fetch = FakeMast({"listing": LISTING, NAME: synthetic_tpf_bytes})
    tpfs = fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t")
    with store.session() as s:
        blob.delete(s.get(ArtifactRow, tpfs[0].sha256).local_path)
    again = fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t")
    assert fetch.calls == 4 and again[0].sha256 == tpfs[0].sha256


def test_fetch_tpfs_without_a_blob_fetches_every_file(store, synthetic_tpf_bytes):
    fetch = FakeMast({"listing": LISTING, NAME: synthetic_tpf_bytes})
    assert len(fetch_tpfs(KIC, fetch=fetch, blob=None, store=store, target_id="t")) == 1
    assert store.counts()["artifacts"] == 0


def test_fetch_tpfs_raises_service_unavailable_when_mast_is_down(store, blob):
    with pytest.raises(ServiceUnavailable):
        fetch_tpfs(1, fetch=outage_fetch, blob=blob, store=store, target_id="t")


def test_fetch_tpfs_raises_when_no_listed_file_is_reachable(store, blob):
    fetch = FakeMast({"listing": LISTING}, fail={NAME})     # the listing answers, every file 503s
    with pytest.raises(ServiceUnavailable, match="no pixel file"):
        fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t")


def test_fetch_tpfs_of_a_star_without_pixel_files_is_empty(store, blob):
    fetch = FakeMast({"listing": b"<html><body><ul></ul></body></html>"})
    assert fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t") == [] and fetch.calls == 1


def test_a_missing_pixel_directory_is_no_pixel_files_not_an_outage(store, blob):
    fetch = FakeMast({})                                   # the listing URL answers HTTP 404
    got = fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t")
    assert got == [] and got.missing == [] and fetch.calls == 1


def test_fetch_tpfs_reports_the_files_it_could_not_reach(store, blob, synthetic_tpf_bytes):
    other = tpf_name(KIC, 4)
    listing = LISTING.replace(f'<li><a href="{NAME}">'.encode(),
                              f'<li><a href="{other}"></a><a href="{NAME}">'.encode())
    fetch = FakeMast({"listing": listing, NAME: synthetic_tpf_bytes, other: b"unused"}, fail={other})
    got = fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t")
    assert [t.quarter for t in got] == [3] and got.missing == [{"name": other, "cause": "unreachable"}]


def test_fetch_tpfs_skips_an_unreadable_file_and_keeps_the_rest(store, blob, synthetic_tpf_bytes):
    other = tpf_name(KIC, 4)
    listing = LISTING.replace(f'<li><a href="{NAME}">'.encode(),
                              f'<li><a href="{other}"></a><a href="{NAME}">'.encode())
    fetch = FakeMast({"listing": listing, NAME: synthetic_tpf_bytes, other: b"<html>not a fits file</html>"})
    tpfs = fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t")
    assert [t.quarter for t in tpfs] == [3] and tpfs.missing == [{"name": other, "cause": "unreadable"}]
    assert store.counts()["artifacts"] == 1                 # the unreadable file is not kept


def test_a_listed_file_that_404s_is_a_stale_index_not_an_outage(store, blob, synthetic_tpf_bytes):
    other = tpf_name(KIC, 4)
    listing = LISTING.replace(f'<li><a href="{NAME}">'.encode(),
                              f'<li><a href="{other}"></a><a href="{NAME}">'.encode())
    fetch = FakeMast({"listing": listing, NAME: synthetic_tpf_bytes})          # the listed Q4 file answers 404
    got = fetch_tpfs(KIC, fetch=fetch, blob=blob, store=store, target_id="t")
    assert [t.quarter for t in got] == [3] and got.missing == [{"name": other, "cause": "unreadable"}]
    only = FakeMast({"listing": LISTING})                  # the only listed file 404s: nothing to read, no outage
    got = fetch_tpfs(KIC, fetch=only, blob=None, store=store, target_id="t")
    assert got == [] and got.missing == [{"name": NAME, "cause": "unreadable"}]
