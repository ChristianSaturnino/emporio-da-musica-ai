"""Optional command-line chat, for a quick check without opening a browser."""

import logging
import sys
from collections.abc import Callable

from emporio_ai.agent.service import ConversationService, create_service
from emporio_ai.domain.errors import ConfigurationError, DatabaseNotFoundError, DatasetError
from emporio_ai.observability import events

SESSION_ID = "cli"
EXIT_COMMANDS = {"sair", "exit", "quit"}


def run_chat(
    service: ConversationService,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    write("Empório da Música (demonstração). Escreva 'sair' para encerrar.")
    while True:
        try:
            message = read("\nVocê: ")
        except (EOFError, KeyboardInterrupt):
            return
        if message.strip().casefold() in EXIT_COMMANDS:
            return
        write(f"Assistente: {service.chat(SESSION_ID, message)}")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    # Without a handler, Python would print warning events in the middle of the chat.
    events.logger.addHandler(logging.NullHandler())
    try:
        service = create_service()
    except (ConfigurationError, DatabaseNotFoundError, DatasetError) as exc:
        print(exc)
        return 1
    run_chat(service)
    return 0


if __name__ == "__main__":
    sys.exit(main())
