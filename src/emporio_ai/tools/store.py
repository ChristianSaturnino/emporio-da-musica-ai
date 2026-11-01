"""Store information tool: fixed manual sections for each topic, with no search involved."""

from datetime import UTC, date, datetime, timedelta, timezone

from emporio_ai.data.policy_index import PolicyIndex
from emporio_ai.domain.schemas import StoreInformation, StoreTopic
from emporio_ai.tools.policies import CONTACT_NOTE, POLICY_SOURCE

# Campo Grande/MS is on UTC-4 all year. A fixed offset needs no time zone database on Windows.
_STORE_TIMEZONE = timezone(timedelta(hours=-4))
_WEEKDAYS = (
    "segunda-feira",
    "terça-feira",
    "quarta-feira",
    "quinta-feira",
    "sexta-feira",
    "sábado",
    "domingo",
)
_HOLIDAY_NOTE = (
    "O sistema não sabe quais dias são feriado. Ao falar de hoje ou de amanhã como um dia de loja "
    "aberta, avise que a loja fecha em feriados."
)

_TOPIC_SECTIONS = {
    StoreTopic.ADDRESS: ("1.2",),
    StoreTopic.HOURS: ("2",),
    StoreTopic.CONTACT: ("1.2", "7"),
    StoreTopic.ABOUT: ("1", "1.1", "1.2"),
}
_TOPIC_NOTES = {StoreTopic.CONTACT: (CONTACT_NOTE,), StoreTopic.HOURS: (_HOLIDAY_NOTE,)}


def store_date(moment: datetime) -> date:
    return moment.astimezone(_STORE_TIMEZONE).date()


def today_in_store() -> date:
    return store_date(datetime.now(UTC))


def calendar_facts(today: date) -> dict[str, str]:
    """Today and tomorrow, worked out here so the model never has to guess or count a weekday."""

    def describe(day: date) -> str:
        return f"{_WEEKDAYS[day.weekday()]}, {day:%d/%m/%Y}"

    return {"today": describe(today), "tomorrow": describe(today + timedelta(days=1))}


def get_store_information(index: PolicyIndex, topic: StoreTopic) -> StoreInformation:
    sections = tuple(index.get(section_id) for section_id in _TOPIC_SECTIONS[topic])
    return StoreInformation(
        topic=topic,
        source=POLICY_SOURCE,
        sections=tuple(section for section in sections if section is not None),
        notes=_TOPIC_NOTES.get(topic, ()),
    )
