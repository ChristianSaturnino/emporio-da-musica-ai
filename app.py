"""Streamlit chat for the Empório da Música assistant. All the logic lives in the service."""

import uuid

import streamlit as st

from emporio_ai.agent.service import ConversationService, create_service
from emporio_ai.domain.errors import ConfigurationError, DatabaseNotFoundError, DatasetError

EXAMPLE_QUESTIONS = (
    "Quais violões têm até R$ 1.000 e estão disponíveis?",
    "Quanto custa o Takamine GD20? E esse tem desconto?",
    "Abre domingo? Qual o endereço?",
    "Me arrependi da compra, posso devolver?",
    "Onde está meu pedido 8?",
)


def escape_markdown(text: str) -> str:
    """Stop Streamlit from reading the text between two "R$" amounts as a math formula."""
    return text.replace("$", "\\$")


@st.cache_resource(show_spinner="Preparando o assistente...")
def load_service() -> ConversationService:
    return create_service()


def render_sidebar() -> None:
    with st.sidebar:
        st.warning(
            "**Demonstração.** Os dados são fictícios e são um retrato fixo, não o sistema da "
            "loja em tempo real. Preços, estoque e pedidos podem estar defasados."
        )
        st.subheader("Experimente perguntar")
        for question in EXAMPLE_QUESTIONS:
            st.markdown(f"- {escape_markdown(question)}")
        st.caption(
            "Para consultar um pedido, informe o número e o e-mail do cliente, que estão em "
            "`data/orders.csv` e `data/customers.csv`. Essa verificação é simulada e não é uma "
            "autenticação real."
        )
        st.button("Limpar conversa", key="clear_conversation", on_click=clear_conversation)


def clear_conversation() -> None:
    service = st.session_state.get("service")
    if service is not None:
        service.reset(st.session_state.session_id)
    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []


def show_setup_error(title: str, command: str) -> None:
    st.error(title)
    st.code(command, language="bash")
    st.stop()


def main() -> None:
    st.set_page_config(page_title="Empório da Música", page_icon="🎸")
    st.title("🎸 Empório da Música")
    st.caption("Sua música começa aqui.")

    st.session_state.setdefault("session_id", str(uuid.uuid4()))
    st.session_state.setdefault("messages", [])
    render_sidebar()

    try:
        service = load_service()
    except ConfigurationError:
        show_setup_error(
            "A chave da OpenAI não está configurada. Crie o arquivo `.env` a partir do exemplo, "
            "preencha `OPENAI_API_KEY` e recarregue a página.",
            "cp .env.example .env",
        )
    except DatabaseNotFoundError:
        show_setup_error(
            "O banco de dados local ainda não foi criado. Rode o comando abaixo e recarregue a "
            "página.",
            "python scripts/seed_db.py",
        )
    except DatasetError:
        show_setup_error(
            "Não consegui ler os dados da loja. Confira a pasta `data/` e rode o comando abaixo.",
            "python scripts/inspect_data.py",
        )
    st.session_state.service = service

    for message in st.session_state.messages:
        with st.chat_message(message["role"]):
            st.markdown(escape_markdown(message["content"]))

    if prompt := st.chat_input("Escreva sua mensagem"):
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(escape_markdown(prompt))
        with st.chat_message("assistant"):
            with st.spinner("Consultando..."):
                reply = service.chat(st.session_state.session_id, prompt)
            st.markdown(escape_markdown(reply))
        st.session_state.messages.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    main()
