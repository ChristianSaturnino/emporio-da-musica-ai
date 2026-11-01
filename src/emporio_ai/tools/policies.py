"""Policy tool: retrieve manual sections, flagging where the manual is unclear or inconsistent."""

from emporio_ai.data.policy_index import PolicyIndex
from emporio_ai.domain.schemas import PolicySearchResult

POLICY_SOURCE = "Manual de Políticas e Procedimentos, versão 2.1, junho de 2025"
MAX_SECTIONS = 3

CONTACT_NOTE = (
    "Há dois números registrados para telefone e WhatsApp: (67) 3341-4444 e (67) 3321-4500. "
    "Informe os dois e diga que, se um não atender, o cliente pode tentar o outro. O e-mail é "
    "contato@emporiodamusica.com.br, mesmo que outra grafia apareça nos dados."
)
_INSTALLMENT_NOTE = (
    "A tabela da seção 3 cita parcela mínima de R$ 100,00, mas a seção 3.1 define faixas. Siga a "
    "3.1, que é mais específica. O limite de R$ 50,00 em até 3x não está claro: não confirme "
    "parcelas nesse caso e oriente falar com a equipe."
)
_DEFECT_NOTE = (
    "A seção 4.2 trata da troca em até 30 dias após a compra e a seção 8.1 da garantia legal de "
    "90 dias a partir do recebimento. São regras diferentes: apresente as duas, não afirme que um "
    "direito acabou nem que o cliente ainda tem o direito, pois a data de recebimento não é "
    "conhecida. Não diga \"não se preocupe\" nem \"você ainda tem\". Explique o que cada regra diz "
    "e oriente falar com a equipe para avaliar o caso."
)
# Keyed by section ID. These notes are written here, not taken from the PDF.
_SECTION_NOTES = {
    "1.2": CONTACT_NOTE,
    "7": CONTACT_NOTE,
    "3": _INSTALLMENT_NOTE,
    "3.1": _INSTALLMENT_NOTE,
    "4.1": (
        "Explique sempre a regra: vale para compras online, até 7 dias corridos após o "
        "recebimento, com o produto sem uso e na embalagem original. Se faltar o canal da compra "
        "ou a data de recebimento, pergunte o que falta. Se o cliente já informou e parece estar "
        "no prazo, diga que pela regra parece estar dentro do prazo e oriente falar com a equipe "
        "para concluir. Se a compra foi na loja física, diga que essa regra é para compras online "
        "e oriente falar com a equipe sobre o caso. Mas não afirme que a devolução está garantida."
    ),
    "4.2": _DEFECT_NOTE,
    "8.1": _DEFECT_NOTE,
    "4.3": (
        "O manual não diz a partir de quando contam os 7 dias da troca por preferência. Não "
        "presuma: oriente confirmar com a equipe."
    ),
    "5.1": (
        "O manual fala em pedidos acima e abaixo de R$ 500,00 e não cobre o valor exato de "
        "R$ 500,00. Nesse valor, não prometa frete grátis: oriente confirmar com a equipe."
    ),
    "5.2": (
        "O valor depende de CEP, peso e dimensões, que não estão disponíveis aqui. Não informe "
        "valor de frete nem prometa data de entrega. O manual não lista cidades atendidas: nunca "
        "diga que a loja não entrega em uma cidade, explique as modalidades de envio."
    ),
    "7.1": "O manual sugere lojas parceiras, mas não lista nenhuma. Não cite lojas.",
}


def search_policies(
    index: PolicyIndex, query: str, limit: int = MAX_SECTIONS
) -> PolicySearchResult:
    """Find the manual sections that answer a question. An empty result means it is not covered."""
    sections = index.search(query, limit=min(limit, MAX_SECTIONS))
    notes = dict.fromkeys(
        _SECTION_NOTES[section.section_id]
        for section in sections
        if section.section_id in _SECTION_NOTES
    )
    return PolicySearchResult(source=POLICY_SOURCE, sections=tuple(sections), notes=tuple(notes))
