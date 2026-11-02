"""Split the policy manual into sections and retrieve them by lexical score."""

import re
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

from emporio_ai.domain.errors import DatasetError
from emporio_ai.domain.normalization import word_tokens
from emporio_ai.domain.schemas import PolicySection

_PAGE_HEADER = "Empório da Música Manual de Políticas e Procedimentos"
_PAGE_FOOTER = re.compile(r"Página \d+\s*$")

# Words customers use that the section text does not necessarily contain. Words are compared by
# their first letters (see _root), so one form of each word is enough.
_SECTION_KEYWORDS = {
    "1": ("vende", "acessório", "catálogo", "marca", "história"),
    "1.1": ("missão", "propósito"),
    "1.2": (
        "endereço",
        "rua",
        "localização",
        "onde",
        "fica",
        "telefone",
        "contato",
        "whatsapp",
        "email",
        "cnpj",
    ),
    "2": (
        "horário",
        "hora",
        "abre",
        "aberto",
        "fecha",
        "funciona",
        "expediente",
        "domingo",
        "sábado",
        "segunda",
        "sexta",
        "feriado",
        "natal",
    ),
    "3": (
        "pagamento",
        "pagar",
        "pago",
        "forma",
        "aceita",
        "pix",
        "cartão",
        "débito",
        "crédito",
        "boleto",
    ),
    "3.1": (
        "parcela",
        "vezes",
        "juros",
        "12x",
        "combinar",
        "combinação",
        "pix",
        "cartão",
        "crédito",
        "pagamento",
        "pago",
        "parte",
        "metade",
        "dividir",
    ),
    "4": ("troca", "devolução"),
    "4.1": (
        "arrependimento",
        "desistir",
        "devolver",
        "reembolso",
        "estorno",
        "online",
        "internet",
    ),
    "4.2": ("defeito", "quebrou", "troca"),
    "4.3": ("troca", "preferência", "cor", "modelo", "tamanho"),
    "4.4": ("troca", "elegível", "personalizado", "boquilha", "liquidação"),
    "5.1": (
        "frete",
        "entrega",
        "campo",
        "grande",
        "metropolitana",
        "grátis",
        "motoboy",
        "custa",
    ),
    "5.2": (
        "frete",
        "entrega",
        "enviar",
        "mandar",
        "outra",
        "cidade",
        "estado",
        "correios",
        "pac",
        "sedex",
        "jadlog",
        "custa",
        "avaria",
    ),
    "5.3": ("rastreamento", "código", "acompanhar", "entrega"),
    "6.1": ("promoção", "campanha", "black", "friday", "aniversário", "queima"),
    "6.2": ("promoção", "desconto", "pix", "cumulativo", "acumula", "somar"),
    "7": ("whatsapp", "atendente", "humano", "telefone", "contato"),
    "7.3": ("reclamação", "reclamar", "esgotado", "descontinuado", "expirada", "vencida"),
    "8.1": ("garantia", "defeito", "legal"),
    "8.2": ("garantia", "fabricante"),
    "8.3": ("garantia", "cobre", "desgaste"),
    "9": ("privacidade", "dados", "lgpd", "excluir", "apagar"),
}

_STOP_WORDS = frozenset(
    word_tokens(
        """
        a agora ai ao aos aqui as com como da das de do dos e ela ele em esse essa este esta eu faco
        fazer gostaria ha isso ja la loja mais me meu meus minha minhas muito musica na nao nas no
        nos o os ou para pela pelo por pra preciso produto qual quais quando quanto que queria quero
        saber se sem ser seu sim sua sobre tem ter um uma vc voce voces instrumento emporio cliente
        posso pode podem dia dias depois atendimento compra compram comprar comprei compro conta
        """
    )
)

_ROOT_LENGTH = 5

# A word in the title or keywords says far more about a section than one found in its text.
_STRONG_MATCH = 3
_WEAK_MATCH = 1
# One strong match or two weak ones. A single common word in the text is not evidence.
_MIN_SCORE = 2


def _outline_entries(reader: PdfReader, items: list) -> list[tuple[str, int]]:
    entries = []
    for item in items:
        if isinstance(item, list):
            entries.extend(_outline_entries(reader, item))
        else:
            title = " ".join(item.title.split())
            entries.append((title, reader.get_destination_page_number(item) + 1))
    return entries


def _page_text(page) -> str:
    """Rebuild a page's text, without the repeated header and footer.

    This PDF yields one word per line with explicit space lines, so lines are joined as they are.
    """
    text = "".join(page.extract_text().splitlines())
    text = " ".join(text.split())
    return _PAGE_FOOTER.sub("", text.removeprefix(_PAGE_HEADER)).strip()


def extract_sections(pdf_path: Path) -> list[PolicySection]:
    """Split the manual along its own outline, so a condition is never cut from its deadline."""
    if not pdf_path.exists():
        raise DatasetError(f"{pdf_path.name}: file not found")
    try:
        reader = PdfReader(pdf_path)
        entries = _outline_entries(reader, reader.outline)
        text = " ".join(_page_text(page) for page in reader.pages)
    except PdfReadError as exc:
        raise DatasetError(f"{pdf_path.name}: could not be read") from exc
    if not entries:
        raise DatasetError(f"{pdf_path.name}: has no outline to split sections by")

    starts = []
    position = 0
    for title, _ in entries:
        start = text.find(title, position)
        if start == -1:
            raise DatasetError(f"{pdf_path.name}: section '{title}' not found in the text")
        starts.append(start)
        position = start + len(title)

    sections = []
    for index, (title, page) in enumerate(entries):
        end = starts[index + 1] if index + 1 < len(entries) else len(text)
        content = text[starts[index] + len(title) : end].replace(" • ", "\n- ").strip()
        content = content.removeprefix("• ")
        # Parents such as "8. Garantia" hold only their subsections.
        if content:
            number, _, name = title.partition(" ")
            section_id = number.rstrip(".")
            sections.append(
                PolicySection(
                    section_id=section_id,
                    title=name,
                    page=page,
                    content=content,
                    keywords=_SECTION_KEYWORDS.get(section_id, ()),
                )
            )
    return sections


def _roots(text: str, skip: frozenset[str] = frozenset()) -> set[str]:
    """Reduce words to their first letters, so "entregam" and "entrega" compare as equal.

    Portuguese verbs have too many forms to list. This is cruder than a real stemmer, but it needs
    no dependency, and every match can still be explained by looking at the words involved.
    """
    return {word[:_ROOT_LENGTH] for word in word_tokens(text) if word not in skip and len(word) > 1}


class PolicyIndex:
    def __init__(self, sections: list[PolicySection]) -> None:
        self._sections = sections
        self._indexed = [
            (
                section,
                _roots(" ".join((section.title, *section.keywords))),
                _roots(section.content),
            )
            for section in sections
        ]

    @property
    def sections(self) -> list[PolicySection]:
        return list(self._sections)

    def get(self, section_id: str) -> PolicySection | None:
        return next((s for s in self._sections if s.section_id == section_id), None)

    def search(self, query: str, limit: int) -> list[PolicySection]:
        """Return the best sections, leaving out those far below the top score."""
        words = _roots(query, skip=_STOP_WORDS)
        scored = []
        for position, (section, strong, weak) in enumerate(self._indexed):
            score = sum(
                _STRONG_MATCH if word in strong else _WEAK_MATCH if word in weak else 0
                for word in words
            )
            if score >= _MIN_SCORE:
                scored.append((score, position, section))
        if not scored:
            return []
        scored.sort(key=lambda item: (-item[0], item[1]))
        top_score = scored[0][0]
        relevant = [section for score, _, section in scored if score * 2 >= top_score]
        return relevant[: max(1, limit)]


def build_policy_index(pdf_path: Path) -> PolicyIndex:
    return PolicyIndex(extract_sections(pdf_path))
