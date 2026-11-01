"""A fake model and shorthand for scripting what it "understood"."""

import json

from emporio_ai.domain.schemas import ActionRequest, IntentType, ParsedTurn


class FakeLLM:
    """Plays back scripted turns and records every prompt it receives."""

    def __init__(self, *turns, reply="Resposta redigida."):
        self.turns = list(turns)
        self.reply = reply
        self.parse_calls = []
        self.compose_calls = []

    def parse_turn(self, messages):
        self.parse_calls.append(messages)
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn

    def compose_reply(self, messages):
        self.compose_calls.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply

    @property
    def everything_sent(self):
        return json.dumps(self.parse_calls + self.compose_calls, ensure_ascii=False)

    @property
    def facts(self):
        return self.compose_calls[-1][-1]["content"]


def turn(*actions):
    return ParsedTurn(actions=list(actions), needs_clarification=False)


def act(intent, **slots):
    return ActionRequest(intent=intent, **slots)


def search(**slots):
    return act(IntentType.PRODUCT_SEARCH, **slots)


def price(**slots):
    return act(IntentType.PRODUCT_PRICE, **slots)


def details(**slots):
    return act(IntentType.PRODUCT_DETAILS, **slots)


def order(**slots):
    return act(IntentType.ORDER_STATUS, **slots)


def policy(topic):
    return act(IntentType.POLICY_QUESTION, policy_topic=topic)


def store(topic):
    return act(IntentType.STORE_INFORMATION, store_topic=topic)


OUT_OF_SCOPE = act(IntentType.OUT_OF_SCOPE)
