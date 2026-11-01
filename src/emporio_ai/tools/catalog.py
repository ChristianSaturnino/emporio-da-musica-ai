"""Catalog tools: product search, details and prices, with no LLM involved."""

from dataclasses import dataclass
from decimal import Decimal

from emporio_ai.data.repository import MAX_LIMIT, Repository
from emporio_ai.domain.normalization import normalize_text, tokenize
from emporio_ai.domain.pricing import availability_of, quote_price
from emporio_ai.domain.schemas import (
    Availability,
    LookupStatus,
    Product,
    ProductCandidate,
    ProductDetails,
    ProductDetailsResult,
    ProductOffer,
    ProductPriceResult,
    ProductSearchResult,
)
from emporio_ai.domain.scope import find_unsold_item

MAX_RESULTS = 5
MAX_ALTERNATIVES = 3
MAX_CANDIDATES = 5

# The descriptions of these products name a different brand from the product itself, so they are
# not passed on: a "Yamaha" drum kit would be presented as a "Pearl Export".
_UNRELIABLE_DESCRIPTIONS = frozenset({135, 137, 139, 140, 142, 144})


def _to_offer(repo: Repository, product: Product) -> ProductOffer:
    availability = availability_of(product.status, product.stock_quantity)
    return ProductOffer(
        product_id=product.product_id,
        name=product.name,
        category_name=product.category_name,
        availability=availability,
        quote=quote_price(
            product.price, repo.get_active_promotions(product.product_id), availability
        ),
    )


def _sort_key(offer: ProductOffer) -> tuple[bool, Decimal, int]:
    """Available products first, then the cheapest."""
    return (
        offer.availability is not Availability.AVAILABLE,
        offer.quote.effective_price,
        offer.product_id,
    )


def _match_categories(repo: Repository, text: str, include_description: bool) -> list[int]:
    """Return the categories whose name holds every word of the text.

    With include_description, descriptions are tried when no name matches, which is how
    "saxofone" reaches its category. More than one category can match, as with "sopro".
    """
    words = set(tokenize(text))
    if not words:
        return []
    categories = repo.list_categories()
    by_name = [c.category_id for c in categories if words <= set(tokenize(c.name))]
    if by_name or not include_description:
        return by_name
    return [c.category_id for c in categories if words <= set(tokenize(c.description or ""))]


def search_products(
    repo: Repository,
    term: str | None = None,
    category: str | None = None,
    max_price: Decimal | None = None,
    only_available: bool = False,
    on_promotion: bool = False,
    limit: int = MAX_RESULTS,
) -> ProductSearchResult:
    """Search the catalog, returning the cheapest matches with their availability.

    max_price applies to the effective price, which is the promotional price when there is an
    active promotion and the list price otherwise. PIX is left out because it depends on how the
    customer chooses to pay.

    Browsing without a term only shows products that can be bought. A term may name a specific
    product, so its matches are returned even if unavailable, marked as such, unless
    only_available is set.

    The store does not sell accessories, so asking for one reports that instead of searching.
    """
    unsold_item = find_unsold_item(f"{term or ''} {category or ''}")
    if unsold_item:
        return ProductSearchResult(unsold_item=unsold_item)

    category_ids = None
    if category and category.strip():
        category_ids = _match_categories(repo, category, include_description=True)
        if not category_ids:
            return ProductSearchResult(unknown_category=True)

    term = (term or "").strip() or None
    if term and category_ids is None:
        # "violões" is a category, not a product name: treat it as browsing that category.
        term_categories = _match_categories(repo, term, include_description=False)
        if term_categories:
            category_ids, term = term_categories, None

    matches = repo.find_products(term, category_ids, limit=MAX_LIMIT)
    if term and not matches:
        matches = repo.find_products(term, category_ids, limit=MAX_LIMIT, include_description=True)

    offers = [_to_offer(repo, product) for product in matches]
    if only_available or term is None:
        offers = [offer for offer in offers if offer.availability is Availability.AVAILABLE]
    if max_price is not None:
        offers = [offer for offer in offers if offer.quote.effective_price <= max_price]
    if on_promotion:
        offers = [offer for offer in offers if offer.quote.promotion_percent is not None]
    offers.sort(key=_sort_key)

    return ProductSearchResult(
        products=tuple(offers[: max(1, min(limit, MAX_RESULTS))]),
        total_matches=len(offers),
    )


@dataclass(frozen=True)
class _Lookup:
    status: LookupStatus
    product: Product | None = None
    candidates: tuple[ProductCandidate, ...] = ()


def _lookup_product(repo: Repository, product_id: int | None, name: str | None) -> _Lookup:
    """Resolve one product by ID or name, without guessing when a name is ambiguous."""
    if product_id is not None:
        product = repo.get_product(product_id)
        return _Lookup(LookupStatus.FOUND, product) if product else _Lookup(LookupStatus.NOT_FOUND)
    if not name or not name.strip():
        return _Lookup(LookupStatus.NOT_FOUND)

    matches = repo.find_products(name, limit=MAX_LIMIT)
    exact = [p for p in matches if normalize_text(p.name) == normalize_text(name)]
    matches = exact or matches
    if not matches:
        return _Lookup(LookupStatus.NOT_FOUND)
    if len(matches) == 1:
        return _Lookup(LookupStatus.FOUND, matches[0])
    return _Lookup(
        LookupStatus.AMBIGUOUS,
        candidates=tuple(
            ProductCandidate(product_id=p.product_id, name=p.name)
            for p in matches[:MAX_CANDIDATES]
        ),
    )


def _find_alternatives(repo: Repository, product: Product) -> tuple[ProductOffer, ...]:
    """Available products of the same category, closest in price (policy manual, section 7.3)."""
    peers = repo.find_products(category_ids=[product.category_id], limit=MAX_LIMIT)
    offers = [
        offer
        for offer in (_to_offer(repo, peer) for peer in peers if peer != product)
        if offer.availability is Availability.AVAILABLE
    ]
    offers.sort(key=lambda o: (abs(o.quote.effective_price - product.price), o.product_id))
    return tuple(offers[:MAX_ALTERNATIVES])


def get_product_details(
    repo: Repository, product_id: int | None = None, name: str | None = None
) -> ProductDetailsResult:
    """Return one product's details, plus alternatives when it cannot be bought."""
    lookup = _lookup_product(repo, product_id, name)
    if lookup.product is None:
        return ProductDetailsResult(status=lookup.status, candidates=lookup.candidates)

    offer = _to_offer(repo, lookup.product)
    is_available = offer.availability is Availability.AVAILABLE
    return ProductDetailsResult(
        status=LookupStatus.FOUND,
        details=ProductDetails(
            offer=offer,
            description=(
                None
                if lookup.product.product_id in _UNRELIABLE_DESCRIPTIONS
                else lookup.product.description
            ),
            stock_quantity=lookup.product.stock_quantity,
            specs=lookup.product.specs,
            alternatives=() if is_available else _find_alternatives(repo, lookup.product),
        ),
    )


def get_product_price(
    repo: Repository, product_id: int | None = None, name: str | None = None
) -> ProductPriceResult:
    """Return one product's list, promotional and PIX prices, as applicable."""
    lookup = _lookup_product(repo, product_id, name)
    if lookup.product is None:
        return ProductPriceResult(status=lookup.status, candidates=lookup.candidates)
    return ProductPriceResult(status=LookupStatus.FOUND, offer=_to_offer(repo, lookup.product))
