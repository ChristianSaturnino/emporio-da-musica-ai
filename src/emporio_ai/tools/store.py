"""Store information tool: fixed manual sections for each topic, with no search involved."""

from emporio_ai.data.policy_index import PolicyIndex
from emporio_ai.domain.schemas import StoreInformation, StoreTopic
from emporio_ai.tools.policies import CONTACT_NOTE, POLICY_SOURCE

_TOPIC_SECTIONS = {
    StoreTopic.ADDRESS: ("1.2",),
    StoreTopic.HOURS: ("2",),
    StoreTopic.CONTACT: ("1.2", "7"),
    StoreTopic.ABOUT: ("1", "1.1"),
}
_TOPIC_NOTES = {StoreTopic.CONTACT: (CONTACT_NOTE,)}


def get_store_information(index: PolicyIndex, topic: StoreTopic) -> StoreInformation:
    sections = tuple(index.get(section_id) for section_id in _TOPIC_SECTIONS[topic])
    return StoreInformation(
        topic=topic,
        source=POLICY_SOURCE,
        sections=tuple(section for section in sections if section is not None),
        notes=_TOPIC_NOTES.get(topic, ()),
    )
