"""Catalog tools: product search, details and prices, with no LLM involved."""

from dataclasses import dataclass
from decimal import Decimal
from difflib import get_close_matches

from emporio_ai.data.repository import MAX_LIMIT, Repository
from emporio_ai.domain.normalization import normalize_text, search_tokens, tokenize, word_tokens
from emporio_ai.domain.pricing import availability_of, quote_price
from emporio_ai.domain.schemas import (
    Availability,
    CategoryOverview,
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

_MIN_TYPO_LENGTH = 5
_TYPO_CUTOFF = 0.8

_GENERIC_PRODUCT_WORDS = frozenset(word_tokens("instrumentos musicais produtos itens opções"))
_NON_DISTINCTIVE_CATEGORY_WORDS = frozenset(word_tokens("instrumentos de e madeiras metais cordas"))

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


def is_generic(text: str | None) -> bool:
    """True for blank text or words that name no kind of product, such as "instrumentos".

    Those used to match the wind categories by their name, which have no products, so "tem algum
    instrumento barato?" was told that nothing exists.
    """
    return set(tokenize(text or "")) <= _GENERIC_PRODUCT_WORDS


def find_category_mention(repo: Repository, text: str) -> str | None:
    """Return the first word of the text that names a kind of instrument the store carries."""
    category_words = {
        word
        for category in repo.list_categories()
        for word in word_tokens(category.name)
        if word not in _NON_DISTINCTIVE_CATEGORY_WORDS
    }
    return next((word for word in word_tokens(text) if word in category_words), None)


def catalog_overview(repo: Repository) -> tuple[CategoryOverview, ...]:
    """Summarize what can be bought now, by category, from the products themselves."""
    by_category: dict[str, list[Decimal]] = {}
    for product in repo.find_products(limit=MAX_LIMIT):
        offer = _to_offer(repo, product)
        if offer.availability is Availability.AVAILABLE:
            by_category.setdefault(product.category_name, []).append(offer.quote.effective_price)
    overview = [
        CategoryOverview(category=name, available_products=len(prices), lowest_price=min(prices))
        for name, prices in by_category.items()
    ]
    return tuple(sorted(overview, key=lambda entry: entry.lowest_price))


def products_named_in(repo: Repository, text: str, product_ids: list[int]) -> list[Product]:
    """Return the products of a list that the customer's words point to.

    "Quero a fender" points to both Fenders of a list, and the model tended to pick one. Words
    shared by the whole list, like "natural" in a list of natural guitars, tell nothing apart.
    """
    products = [product for i in product_ids if (product := repo.get_product(i)) is not None]
    names = {product.product_id: set(tokenize(product.name)) for product in products}
    if len(names) < 2:
        return []
    known = set().union(*names.values())
    shared = set.intersection(*names.values())
    words = {word for word in search_tokens(text) if not word.isdigit()} & known - shared
    if not words:
        return []
    return [product for product in products if words <= names[product.product_id]]


def correct_typos(repo: Repository, text: str | None) -> str | None:
    """Replace words that no product or category uses with the closest one that does.

    Only used after a search finds nothing, so a correct name is never changed. "Yamha" and
    "guitara" were told the product does not exist. Short words and codes are left alone, as they
    are too easily mistaken: "C40" is not a typo of "C70".
    """
    if not text:
        return text
    names = [product.name for product in repo.find_products(limit=MAX_LIMIT)]
    names += [category.name for category in repo.list_categories()]
    known = {word for name in names for word in tokenize(name)}

    def closest(word: str) -> str:
        if word in known or len(word) < _MIN_TYPO_LENGTH or not word.isalpha():
            return word
        return next(iter(get_close_matches(word, known, n=1, cutoff=_TYPO_CUTOFF)), word)

    return " ".join(closest(word) for word in tokenize(text))


def search_products(
    repo: Repository,
    term: str | None = None,
    category: str | None = None,
    max_price: Decimal | None = None,
    only_available: bool = False,
    on_promotion: bool = False,
    limit: int = MAX_RESULTS,
    most_expensive_first: bool = False,
    min_price: Decimal | None = None,
) -> ProductSearchResult:
    """Search the catalog, trying again with typos corrected if nothing was found."""

    def search(term: str | None, category: str | None) -> ProductSearchResult:
        return _search(
            repo,
            term,
            category,
            max_price,
            only_available,
            on_promotion,
            limit,
            most_expensive_first,
            min_price,
        )

    result = search(term, category)
    if result.products or result.unsold_item:
        return result
    corrected = correct_typos(repo, term), correct_typos(repo, category)
    if corrected == (term, category):
        return result
    second_try = search(*corrected)
    return second_try if second_try.products else result


def _search(
    repo: Repository,
    term: str | None,
    category: str | None,
    max_price: Decimal | None,
    only_available: bool,
    on_promotion: bool,
    limit: int,
    most_expensive_first: bool,
    min_price: Decimal | None,
) -> ProductSearchResult:
    """Search the catalog, returning the cheapest matches with their availability.

    Price limits apply to the effective price, which is the promotional price when there is an
    active promotion and the list price otherwise. PIX is left out because it depends on how the
    customer chooses to pay.

    Browsing without a term only shows products that can be bought. A term may name a specific
    product, so its matches are returned even if unavailable, marked as such. With only_available,
    they are left out if something else can be bought. Otherwise they stay, because "Tem baixo
    Ibanez?" deserves to hear it was discontinued rather than that it was not found.

    The store does not sell accessories, so asking for one reports that instead of searching.
    """
    unsold_item = find_unsold_item(f"{term or ''} {category or ''}")
    if unsold_item:
        return ProductSearchResult(unsold_item=unsold_item)

    term = None if is_generic(term) else term.strip()
    category = None if is_generic(category) else category
    category_ids = None
    if category:
        category_ids = _match_categories(repo, category, include_description=True)
        if not category_ids:
            # The model can pack details into the category, as in "violões de 7 cordas". That is
            # not a known category, but it does describe products.
            combined = f"{term or ''} {category}".strip()
            if not repo.find_products(combined, limit=1):
                return ProductSearchResult(unknown_category=True)
            term, category_ids = combined, None

    if term and category_ids is None:
        # "violões" is a category, not a product name: treat it as browsing that category.
        term_categories = _match_categories(repo, term, include_description=False)
        if term_categories:
            category_ids, term = term_categories, None

    matches = repo.find_products(term, category_ids, limit=MAX_LIMIT)
    if term and not matches:
        matches = repo.find_products(term, category_ids, limit=MAX_LIMIT, include_description=True)

    offers = [_to_offer(repo, product) for product in matches]
    available = [offer for offer in offers if offer.availability is Availability.AVAILABLE]
    if term is None:
        offers = available
    elif only_available:
        offers = available or offers
    if max_price is not None:
        offers = [offer for offer in offers if offer.quote.effective_price <= max_price]
    if min_price is not None:
        offers = [offer for offer in offers if offer.quote.effective_price >= min_price]
    if on_promotion:
        offers = [offer for offer in offers if offer.quote.promotion_percent is not None]
    offers.sort(key=_sort_key)
    if most_expensive_first:
        # Stable, and unavailable products stay last: they are not what is being offered.
        offers.sort(key=lambda o: o.quote.effective_price, reverse=True)
        offers.sort(key=lambda o: o.availability is not Availability.AVAILABLE)

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

    matches = repo.find_products(name, limit=MAX_LIMIT) or repo.find_products(
        correct_typos(repo, name), limit=MAX_LIMIT
    )
    exact = [p for p in matches if normalize_text(p.name) == normalize_text(name)]
    # "Martin D-28" matches "Martin D-28E" as text, but it only names the D-28.
    words = set(search_tokens(name))
    whole_words = [p for p in matches if words <= set(tokenize(p.name))]
    matches = exact or whole_words or matches
    if not matches:
        return _Lookup(LookupStatus.NOT_FOUND)
    if len(matches) == 1:
        return _Lookup(LookupStatus.FOUND, matches[0])
    return _Lookup(
        LookupStatus.AMBIGUOUS,
        candidates=tuple(
            ProductCandidate(product_id=p.product_id, name=p.name) for p in matches[:MAX_CANDIDATES]
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
