"""Policy retrieval and store information, from the real manual and without any LLM."""

import re

import pytest
from pypdf import PdfReader

from emporio_ai.data.policy_index import _SECTION_KEYWORDS, build_policy_index, extract_sections
from emporio_ai.domain.errors import DatasetError
from emporio_ai.domain.schemas import StoreTopic
from emporio_ai.domain.scope import find_unsold_item
from emporio_ai.tools.policies import CONTACT_NOTE, MAX_SECTIONS, POLICY_SOURCE, search_policies
from emporio_ai.tools.store import get_store_information


@pytest.fixture(scope="module")
def index(data_dir):
    return build_policy_index(data_dir / "politicas_da_loja.pdf")


def section_ids(result):
    return [section.section_id for section in result.sections]


def section_text(index, section_id):
    return index.get(section_id).content


# Extraction


def test_every_section_with_content_is_extracted(index):
    assert [section.section_id for section in index.sections] == [
        "1", "1.1", "1.2", "2", "3", "3.1", "4", "4.1", "4.2", "4.3", "4.4", "5.1", "5.2", "5.3",
        "6.1", "6.2", "7", "7.1", "7.2", "7.3", "8.1", "8.2", "8.3", "9", "10",
    ]  # fmt: skip


@pytest.mark.parametrize(
    ("section_id", "title", "page"),
    [
        ("1.2", "Dados da Empresa", 2),
        ("2", "Horário de Funcionamento", 2),
        ("3.1", "Regras de Parcelamento", 3),
        ("4.1", "Direito de Arrependimento (Compras Online)", 4),
        ("5.3", "Código de Rastreamento", 5),
        ("6.2", "Regras de Promoções", 6),
        ("8.1", "Garantia Legal", 7),
        ("10", "Disposições Finais", 8),
    ],
)
def test_sections_keep_title_and_page(index, section_id, title, page):
    section = index.get(section_id)
    assert (section.title, section.page) == (title, page)


def test_condition_stays_with_its_deadline(index):
    content = section_text(index, "4.1")
    assert "7 (sete) dias corridos após o recebimento" in content
    assert "embalagem original" in content
    assert "até 10 dias úteis" in content
    assert "garantia" not in content.lower()


def test_section_spanning_two_pages_is_complete_and_has_no_page_furniture(index):
    content = section_text(index, "5.2")
    assert "Jadlog" in content
    assert "cotação individual" in content
    assert "Página" not in content
    assert "Manual de Políticas" not in content

    privacy = section_text(index, "9")
    assert "Cumprimento de obrigações legais" in privacy
    assert "Manual de Políticas" not in privacy


def test_numbers_split_by_the_pdf_are_rebuilt(index):
    assert "(67) 3341-4444" in section_text(index, "1.2")


def test_bullets_become_separate_lines(index):
    lines = section_text(index, "4.1").splitlines()
    assert len(lines) == 4
    assert all(line.startswith(("O ", "- ")) for line in lines)


def test_keywords_only_refer_to_existing_sections(index):
    assert set(_SECTION_KEYWORDS) <= {section.section_id for section in index.sections}


def test_missing_manual_is_reported(tmp_path):
    with pytest.raises(DatasetError, match="not found"):
        extract_sections(tmp_path / "missing.pdf")


def test_invalid_manual_is_reported(tmp_path):
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"this is not a pdf")
    with pytest.raises(DatasetError):
        extract_sections(path)


# Retrieval: the question finds the right section


@pytest.mark.parametrize(
    ("question", "expected_first"),
    [
        ("Qual o endereço da loja?", "1.2"),
        ("Onde fica a loja?", "1.2"),
        ("Abre domingo?", "2"),
        ("Qual o horário de sábado?", "2"),
        ("Vocês abrem no feriado?", "2"),
        ("Aceita boleto?", "3"),
        ("Posso parcelar em 12x?", "3.1"),
        ("Qual a parcela mínima?", "3.1"),
        ("Me arrependi da minha compra, posso devolver meu pedido?", "4.1"),
        ("Recebi há 5 dias; posso devolver?", "4.1"),
        ("Quero trocar a cor do meu violão", "4.3"),
        ("Tem frete grátis em Campo Grande?", "5.1"),
        ("Quanto custa entregar em outra cidade?", "5.2"),
        ("Qual o prazo do SEDEX?", "5.2"),
        ("Como rastrear meu pedido?", "5.3"),
        ("Quando é a Black Friday?", "6.1"),
        ("O desconto do PIX acumula com a promoção?", "6.2"),
        ("O que a garantia não cobre?", "8.3"),
        ("Quero excluir meus dados", "9"),
        ("Quero fazer uma reclamação", "7.3"),
    ],
)
def test_question_finds_the_right_section(index, question, expected_first):
    assert section_ids(search_policies(index, question))[0] == expected_first


@pytest.mark.parametrize(
    ("question", "expected_first"),
    [
        ("Comprei pela internet e não gostei, e agora?", "4.1"),
        ("Posso trocar um ukulele que comprei em liquidação?", "4.4"),
        ("Onde acompanho a entrega?", "5.3"),
        ("Quero reclamar do atendimento", "7.3"),
    ],
)
def test_questions_that_retrieval_once_missed(index, question, expected_first):
    assert section_ids(search_policies(index, question))[0] == expected_first


@pytest.mark.parametrize("question", ["Vocês entregam em Dourados?", "Enviam pelos Correios?"])
def test_verb_forms_match_the_section_about_them(index, question):
    assert "5.2" in section_ids(search_policies(index, question))


def test_negation_does_not_match_titles_that_contain_it(index):
    # "Itens Não Elegíveis" and "O que Não Cobre" have nothing to do with a missing tracking code.
    assert section_ids(search_policies(index, "O código de rastreio não chegou")) == ["5.3"]


def test_warranty_question_with_a_purchase_word_keeps_the_warranty(index):
    question = "Comprei há 3 meses e quebrou, tem garantia?"
    assert "8.1" in section_ids(search_policies(index, question))


def test_defect_after_40_days_brings_both_exchange_and_warranty(index):
    question = "Deu defeito depois de 40 dias, acabou minha garantia?"
    assert {"4.2", "8.1"} <= set(section_ids(search_policies(index, question)))


def test_pix_question_brings_payment_and_promotion_rules(index):
    assert {"3", "6.2"} <= set(section_ids(search_policies(index, "Tem desconto no PIX?")))


def test_retrieval_ignores_case_and_accents(index):
    assert section_ids(search_policies(index, "HORARIO DE FUNCIONAMENTO")) == section_ids(
        search_policies(index, "horário de funcionamento")
    )


def test_result_is_limited_and_has_a_source(index):
    result = search_policies(index, "troca devolução garantia defeito frete pix")
    assert 0 < len(result.sections) <= MAX_SECTIONS
    assert result.source == POLICY_SOURCE


# Retrieval: nothing is made up when the manual does not cover the question


@pytest.mark.parametrize(
    "question",
    [
        "Qual a capital da França?",
        "Vocês dão aulas de violão?",
        "Fazem conserto de guitarra?",
        "Vocês alugam instrumentos?",
        "Vocês compram instrumento usado?",
        "Me conta uma piada",
        "Qual é a senha do wifi?",
        "",
        "   ",
        "???",
        "o que é isso",
    ],
)
def test_uncovered_question_returns_nothing(index, question):
    result = search_policies(index, question)
    assert result.sections == ()
    assert result.notes == ()


def test_single_word_in_the_text_is_not_evidence(index):
    # "aulas" only appears once, in the "Volta às Aulas" campaign.
    assert "aulas" in section_text(index, "6.1").lower()
    assert search_policies(index, "aulas").sections == ()


# Rules the assistant must not get wrong are in the retrieved text


def test_pix_is_not_cumulative(index):
    result = search_policies(index, "O desconto do PIX acumula com a promoção?")
    assert "não se aplica sobre preços já promocionais" in result.sections[0].content


def test_hours(index):
    content = get_store_information(index, StoreTopic.HOURS).sections[0].content
    assert "Sábado 09:00 às 13:00" in content
    assert "Domingo e Feriados Fechado" in content


def test_freight_out_of_town_has_deadlines_but_no_price(index):
    result = search_policies(index, "Quanto custa entregar em outra cidade?")
    content = result.sections[0].content
    assert "5 a 12 dias úteis" in content
    assert "calculado automaticamente" in content
    assert not re.search(r"R\$\s*\d", content)
    assert any("Não informe valor de frete" in note for note in result.notes)


# Notes on what the manual leaves unclear


@pytest.mark.parametrize(
    ("question", "expected_in_note"),
    [
        ("Recebi há 5 dias; posso devolver?", "não afirme"),
        ("Deu defeito depois de 40 dias, acabou minha garantia?", "não afirme que um direito"),
        ("Qual a parcela mínima?", "Siga a 3.1"),
        ("Tem frete grátis em Campo Grande?", "valor exato de R$ 500,00"),
        ("Quero trocar a cor do meu violão", "Não presuma"),
    ],
)
def test_ambiguous_rules_come_with_a_note(index, question, expected_in_note):
    notes = search_policies(index, question).notes
    assert any(expected_in_note in note for note in notes)


def test_note_appears_only_once(index):
    notes = search_policies(index, "parcela forma de pagamento cartão").notes
    assert len(notes) == len(set(notes))


def test_notes_describe_what_the_manual_really_says(index):
    contact = section_text(index, "1.2")
    assert "(67) 3341-4444" in contact
    assert "contato@empóriodamusica.com.br" in contact
    assert "(67) 3321-4500" in section_text(index, "7")
    assert "Parcela mínima de R$ 100,00" in section_text(index, "3")
    assert "até 3x" in section_text(index, "3.1")
    assert "30 (trinta) dias corridos após a compra" in section_text(index, "4.2")
    assert "90 (noventa) dias" in section_text(index, "8.1")
    freight = section_text(index, "5.1")
    assert "acima de R$ 500,00" in freight
    assert "abaixo de R$ 500,00" in freight
    assert "lojas parceiras" in section_text(index, "7.1")


def test_source_matches_the_manual_version(data_dir):
    last_page = "".join(
        PdfReader(data_dir / "politicas_da_loja.pdf").pages[-1].extract_text().splitlines()
    )
    assert "Junho de 2025 — Versão 2.1" in last_page
    assert "versão 2.1, junho de 2025" in POLICY_SOURCE


# Store information


def test_address(index):
    result = get_store_information(index, StoreTopic.ADDRESS)
    assert "Rua 14 de Maio, 3200" in result.sections[0].content
    assert result.notes == ()
    assert result.source == POLICY_SOURCE


def test_contact_flags_the_divergence_between_phone_numbers(index):
    result = get_store_information(index, StoreTopic.CONTACT)
    assert [section.section_id for section in result.sections] == ["1.2", "7"]
    assert result.notes == (CONTACT_NOTE,)


def test_about_includes_the_company_data(index):
    sections = get_store_information(index, StoreTopic.ABOUT).sections
    assert "12.345.678/0001-90" in " ".join(section.content for section in sections)


def test_about_says_the_store_only_sells_instruments(index):
    content = get_store_information(index, StoreTopic.ABOUT).sections[0].content
    assert "não comercializamos acessórios" in content


@pytest.mark.parametrize("topic", list(StoreTopic))
def test_every_topic_has_content(index, topic):
    assert get_store_information(index, topic).sections


# Items the store does not sell


@pytest.mark.parametrize(
    ("text", "item"),
    [
        ("Você vende pedal?", "pedal"),
        ("pedais de guitarra", "pedal"),
        ("Tem corda de nylon?", "corda"),
        ("jogo de cordas", "corda"),
        ("palhetas", "palheta"),
        ("Cabo P10", "cabo"),
        ("case para violão", "case"),
        ("capa de ukulele", "capa"),
        ("amplificador", "amplificador"),
        ("AMPLIFICADORES", "amplificadore"),
        ("acessórios", "acessorio"),
    ],
)
def test_accessories_are_detected(text, item):
    assert find_unsold_item(text) == item


@pytest.mark.parametrize(
    "text",
    [
        "violão",
        "Quais violões até R$ 1.000?",
        "violão de 7 cordas",
        "Rozini RV-174 7 Cordas Nylon Natural",
        "cordas orquestrais",
        "instrumentos de corda",
        "violão com cordas de aço",
        "",
    ],
)
def test_instruments_are_not_mistaken_for_accessories(text):
    assert find_unsold_item(text) is None


def test_unsold_policy_is_in_the_manual(index):
    content = section_text(index, "1")
    for word in ("cordas", "palhetas", "cabos", "cases", "pedais", "amplificadores"):
        assert word in content
