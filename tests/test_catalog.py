"""Catalog tools over the real challenge data, without any LLM."""

from decimal import Decimal

import pytest

from emporio_ai.domain.normalization import build_search_text, tokenize
from emporio_ai.domain.schemas import Availability, LookupStatus
from emporio_ai.tools.catalog import (
    MAX_ALTERNATIVES,
    MAX_RESULTS,
    get_product_details,
    get_product_price,
    search_products,
)


def ids(result):
    return [offer.product_id for offer in result.products]


# Normalization


@pytest.mark.parametrize(
    ("word", "expected"),
    [
        ("Violões", "violao"),
        ("violão", "violao"),
        ("Guitarras", "guitarra"),
        ("Metais", "metal"),
        ("Bass", "bass"),
        ("Les", "les"),
        ("gd20", "gd20"),
    ],
)
def test_tokenize_reduces_plurals(word, expected):
    assert tokenize(word) == [expected]


def test_tokenize_splits_on_separators():
    assert tokenize("Sopro (Madeiras) KA-C") == ["sopro", "madeira", "ka", "c"]


def test_search_text_keeps_codes_without_hyphen():
    assert build_search_text("Giannini GF-3D", "Violões") == "giannini gf 3d violao gf3d"


# Search by category and price


@pytest.mark.parametrize("kwargs", [{"category": "violões"}, {"term": "violões"}])
def test_acoustic_guitars_up_to_1000(repo, kwargs):
    result = search_products(repo, max_price=Decimal("1000"), **kwargs)
    assert result.total_matches == 12
    assert len(result.products) == MAX_RESULTS
    assert result.products[0].name == "Tagima Memphis AC-39 Nylon Natural"
    assert all(offer.availability is Availability.AVAILABLE for offer in result.products)
    assert all(offer.quote.effective_price <= 1000 for offer in result.products)


def test_browsing_excludes_out_of_stock_and_discontinued(repo):
    result = search_products(repo, category="violão", limit=MAX_RESULTS)
    assert result.total_matches == 33  # 35 acoustic guitars minus products 96 and 113.
    assert not {96, 113} & set(ids(result))


def test_browsing_excludes_coming_soon(repo):
    assert search_products(repo, category="ukulele").total_matches == 14


def test_browsing_excludes_discontinued_with_stock(repo):
    assert search_products(repo, category="baixos").total_matches == 3


def test_results_are_sorted_by_effective_price(repo):
    prices = [o.quote.effective_price for o in search_products(repo, category="ukulele").products]
    assert prices == sorted(prices)


def test_max_price_is_inclusive(repo):
    assert 112 in ids(search_products(repo, term="Tagima TW-7", max_price=Decimal("999")))
    assert search_products(repo, term="Tagima TW-7", max_price=Decimal("998.99")).products == ()


def test_max_price_uses_the_promotional_price(repo):
    # Ohana CK-20 lists at R$ 549.00 and is on promotion for R$ 439.20.
    assert 121 in ids(search_products(repo, term="Ohana CK-20", max_price=Decimal("440")))
    assert search_products(repo, term="Ohana CK-20", max_price=Decimal("439")).products == ()


def test_max_price_does_not_use_the_pix_price(repo):
    # Giannini GNF-3 lists at R$ 1,049.00, which is R$ 996.55 with PIX.
    assert search_products(repo, term="Giannini GNF-3", max_price=Decimal("1000")).products == ()


def test_only_products_on_promotion(repo):
    result = search_products(repo, on_promotion=True)
    assert ids(result) == [127, 121, 90, 94]
    assert result.total_matches == 4


def test_multiple_filters_combine(repo):
    result = search_products(repo, category="ukulele", max_price=Decimal("500"), on_promotion=True)
    assert ids(result) == [127, 121]


def test_limit_is_capped(repo):
    assert len(search_products(repo, category="violão", limit=2).products) == 2
    assert len(search_products(repo, category="violão", limit=999).products) == MAX_RESULTS


def test_several_categories_can_match(repo):
    # "Sopro" names two categories, and the snapshot has no wind instruments at all.
    result = search_products(repo, category="sopro")
    assert result.products == ()
    assert result.total_matches == 0
    assert not result.unknown_category


def test_category_found_through_its_description_has_no_products(repo):
    result = search_products(repo, category="saxofone")
    assert result.products == ()
    assert not result.unknown_category


def test_unknown_category_is_flagged(repo):
    result = search_products(repo, category="theremin")
    assert result.unknown_category
    assert result.products == ()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"term": "cordas"},
        {"category": "cordas"},
        {"term": "pedal de distorção"},
        {"category": "amplificadores"},
        {"term": "palheta para violão"},
    ],
)
def test_accessories_are_reported_instead_of_searched(repo, kwargs):
    result = search_products(repo, **kwargs)
    assert result.unsold_item
    assert result.products == ()
    assert not result.unknown_category


def test_instruments_described_by_their_strings_are_not_accessories(repo):
    result = search_products(repo, term="violão 7 cordas")
    assert result.unsold_item is None
    assert 112 in ids(result)
    assert search_products(repo, category="cordas orquestrais").unsold_item is None


# Search by term


def test_search_with_accents_and_plurals(repo):
    expected = ids(search_products(repo, term="Ukulele Barítono"))
    assert expected == ids(search_products(repo, term="ukuleles baritono"))
    assert set(expected) == {128, 129, 130}


def test_named_search_keeps_unavailable_products_marked(repo):
    result = search_products(repo, term="Giannini GF-3D")
    assert ids(result) == [96]
    assert result.products[0].availability is Availability.OUT_OF_STOCK


def test_only_available_removes_unavailable_named_products(repo):
    assert search_products(repo, term="Giannini GF-3D", only_available=True).products == ()


def test_unavailable_products_come_after_available_ones(repo):
    result = search_products(repo, term="ibanez")
    assert ids(result) == [132, 136]
    assert result.products[1].availability is Availability.DISCONTINUED


def test_term_falls_back_to_description(repo):
    result = search_products(repo, term="violão clássico")
    assert 81 in ids(result)
    assert result.total_matches > 0


def test_term_and_category_combine(repo):
    assert ids(search_products(repo, term="takamine", category="ukulele")) == []
    assert search_products(repo, term="takamine", category="violão").total_matches == 4


def test_search_without_results(repo):
    result = search_products(repo, term="theremin quântico")
    assert result.products == ()
    assert result.total_matches == 0
    assert not result.unknown_category


@pytest.mark.parametrize("term", ["'; DROP TABLE products; --", "_", "\\"])
def test_special_characters_find_nothing(repo, term):
    assert search_products(repo, term=term).products == ()


def test_percent_sign_is_a_literal_and_not_a_wildcard(repo):
    # Only product 140 has a "%" in its text ("100% Birch"); a wildcard would match all of them.
    assert ids(search_products(repo, term="%")) == [140]


@pytest.mark.parametrize("term", ["", "   ", None])
def test_blank_term_browses_the_catalog(repo, term):
    assert search_products(repo, term=term).total_matches == 62 - 1  # Active, minus product 96.


# Details


def test_details_of_available_product_have_no_alternatives(repo):
    result = get_product_details(repo, product_id=95)
    assert result.status is LookupStatus.FOUND
    assert result.details.offer.name == "Takamine GD20 Dreadnought Natural"
    assert result.details.stock_quantity == 5
    assert result.details.specs
    assert result.details.alternatives == ()


@pytest.mark.parametrize("product_id", [135, 137, 139, 140, 142, 144])
def test_description_naming_another_brand_is_not_passed_on(repo, product_id):
    assert repo.get_product(product_id).description
    assert get_product_details(repo, product_id=product_id).details.description is None


def test_giannini_gf3d_is_active_but_unavailable(repo):
    result = get_product_details(repo, name="Giannini GF-3D")
    assert result.status is LookupStatus.FOUND
    assert result.details.offer.product_id == 96
    assert result.details.offer.availability is Availability.OUT_OF_STOCK
    assert result.details.stock_quantity == 0


@pytest.mark.parametrize(
    ("product_id", "availability"),
    [
        (96, Availability.OUT_OF_STOCK),
        (113, Availability.DISCONTINUED),
        (130, Availability.COMING_SOON),
    ],
)
def test_unavailable_product_gets_alternatives(repo, product_id, availability):
    details = get_product_details(repo, product_id=product_id).details
    assert details.offer.availability is availability
    assert 0 < len(details.alternatives) <= MAX_ALTERNATIVES
    assert product_id not in [offer.product_id for offer in details.alternatives]
    assert all(offer.availability is Availability.AVAILABLE for offer in details.alternatives)
    assert {o.category_name for o in details.alternatives} == {details.offer.category_name}


def test_alternatives_are_closest_in_price(repo):
    details = get_product_details(repo, product_id=96).details
    assert [offer.product_id for offer in details.alternatives] == [107, 82, 86]


def test_discontinued_with_stock_is_not_available(repo):
    details = get_product_details(repo, product_id=136).details
    assert details.offer.availability is Availability.DISCONTINUED
    assert details.stock_quantity == 13


def test_lookup_by_name_is_ambiguous_without_guessing(repo):
    result = get_product_details(repo, name="Takamine")
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.details is None
    assert {candidate.product_id for candidate in result.candidates} == {95, 100, 105, 115}


def test_exact_name_resolves_a_name_contained_in_others(repo):
    # "Martin D-28" matches both the D-28 and the D-28E; only the full name settles it.
    result = get_product_details(repo, name="Martin D-28")
    assert result.status is LookupStatus.AMBIGUOUS
    exact = get_product_details(repo, name="martin d-28 dreadnought natural")
    assert exact.status is LookupStatus.FOUND
    assert exact.details.offer.product_id == 93


@pytest.mark.parametrize(
    "kwargs",
    [{"product_id": 1}, {"name": "theremin"}, {"name": "   "}, {"name": None}, {}],
)
def test_lookup_not_found(repo, kwargs):
    result = get_product_details(repo, **kwargs)
    assert result.status is LookupStatus.NOT_FOUND
    assert result.details is None
    assert result.candidates == ()


# Prices


def test_price_without_promotion(repo):
    result = get_product_price(repo, name="Takamine GD20")
    assert result.status is LookupStatus.FOUND
    quote = result.offer.quote
    assert quote.list_price == Decimal("2199.00")
    assert quote.pix_price == Decimal("2089.05")
    assert quote.promotion_percent is None


@pytest.mark.parametrize(
    ("product_id", "percent", "promotional_price"),
    [(127, 10, "323.10"), (121, 20, "439.20"), (90, 18, "1967.18"), (94, 8, "5519.08")],
)
def test_price_with_active_promotion_excludes_pix(repo, product_id, percent, promotional_price):
    quote = get_product_price(repo, product_id=product_id).offer.quote
    assert quote.promotion_percent == percent
    assert quote.promotional_price == Decimal(promotional_price)
    assert quote.effective_price == Decimal(promotional_price)
    assert quote.pix_price is None


def test_inactive_promotion_is_ignored(repo):
    # Product 95 has only an inactive 12% promotion.
    quote = get_product_price(repo, product_id=95).offer.quote
    assert quote.promotion_percent is None
    assert quote.effective_price == quote.list_price


def test_price_of_out_of_stock_product_has_no_discounts(repo):
    offer = get_product_price(repo, product_id=96).offer
    assert offer.availability is Availability.OUT_OF_STOCK
    assert offer.quote.list_price == Decimal("799.90")
    assert offer.quote.pix_price is None


def test_price_lookup_is_ambiguous_without_guessing(repo):
    result = get_product_price(repo, name="Takamine")
    assert result.status is LookupStatus.AMBIGUOUS
    assert result.offer is None
    assert len(result.candidates) == 4


def test_price_lookup_not_found(repo):
    result = get_product_price(repo, product_id=1)
    assert result.status is LookupStatus.NOT_FOUND
    assert result.offer is None
