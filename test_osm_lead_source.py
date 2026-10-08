"""The lead finder was returning an empty pipeline for every Indian city.

Three separate defects, each of which alone was enough to produce zero leads,
and all three reported success rather than an error.

1. `admin_level=8` was hardcoded in the area selector. That level is a US
   convention. In OpenStreetMap's own data Pune, Indore and Nagpur are
   `admin_level=5`, so the area selector matched nothing and the query returned
   an empty set. Surat is level 8, which is why this looked intermittent.

2. Categories with more than one tagset were built as a run of standalone
   `(nwr[..](area.a););` statements. Overpass accepts the syntax and discards
   them, so every multi-tagset category returned zero. Dentist appeared to work
   only because it has exactly one tagset.

3. Overpass mirrors rate-limit independently. With two endpoints, Jaipur
   returned 429 from both and Lucknow 504 from both, while the first endpoint
   served Pune seconds later.

These tests exercise query generation only. The Overpass API is a public shared
resource that rate-limits, and a test that depends on it is a test that fails
for reasons unrelated to the code.
"""
import sys

sys.path.insert(0, ".")

from admin.tools import osm_lead_source as osm  # noqa: E402


def test_area_selector_does_not_hardcode_admin_level():
    """The bug. admin_level 8 is a US convention; Indian cities are level 5."""
    q = osm._geo_area_filter("Pune", "Maharashtra")
    assert "admin_level" not in q
    assert 'area["name"="Pune"]' in q
    assert 'boundary"="administrative"' in q


def test_a_us_city_still_resolves():
    """Dropping admin_level must not have broken the original US use case."""
    q = osm._geo_area_filter("Austin", "Texas")
    assert 'area["name"="Austin"]' in q
    assert "admin_level" not in q


def test_multi_tagset_categories_build_one_union_block():
    """The second bug. Separate parenthesised statements are discarded."""
    q = osm._overpass_query("physiotherapist", "Indore", "", 15)
    # Exactly one standalone union block, opened once and closed once.
    assert q.count("(\n") == 1, q
    assert q.count("\n);\n") == 1, q
    # Every tagset must live inside that block.
    assert q.index("healthcare\"=\"physiotherapist") < q.index("\n);\n")


def test_single_tagset_still_works():
    q = osm._overpass_query("dentist", "Pune", "", 15)
    assert 'nwr["amenity"="dentist"](area.a);' in q
    assert q.count("(\n") == 1


def test_output_statement_is_last():
    """`out` must follow the union block, or nothing is emitted."""
    q = osm._overpass_query("dermatologist", "Lucknow", "", 15)
    assert q.rstrip().endswith(";")
    assert q.index("out center tags") > q.index("\n);\n")


def test_the_tags_agency_actually_sells_into_are_mapped():
    """FOUNDER_CONTEXT.md sells dentists, physios, skin, ortho, yoga, gym.
    Every one of these returned zero leads until the tag was spelled the way
    OpenStreetMap actually spells it, which was checked against live data for
    Indore: healthcare=physiotherapist 6, speciality=dermatology 13,
    speciality=orthopaedics 14."""
    for cat in ("dentist", "physiotherapist", "dermatologist", "orthopaedic",
                "yoga studio", "gym", "skin clinic", "ayurveda"):
        assert cat in osm._CATEGORY_TAGS, f"{cat} missing from category map"
        tagsets = osm._CATEGORY_TAGS[cat]
        assert tagsets, f"{cat} has an empty tagset"
        for ts in tagsets:
            assert ts.startswith('["') and ts.endswith("]"), ts


def test_no_dead_overpass_endpoint_is_configured():
    """overpass.osm.jp presents a certificate that does not match its hostname,
    so it can only ever fail the whole lookup on a TLS error."""
    for ep in osm._OVERPASS_ENDPOINTS:
        assert "osm.jp" not in ep
    # More than two, because the mirrors rate-limit independently.
    assert len(osm._OVERPASS_ENDPOINTS) >= 3


def test_query_escapes_a_quote_in_the_city_name():
    """A stray quote would break the query and silently return nothing."""
    q = osm._geo_area_filter('Pune"s', "")
    assert '"Punes"' in q
