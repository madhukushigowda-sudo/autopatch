"""Explicit state machine."""
from enum import Enum


class State(Enum):
    IDLE = "idle"
    RUNNING = "running"
    DONE = "done"


TRANSITIONS = {
    State.IDLE: {State.RUNNING},
    State.RUNNING: {State.DONE, State.IDLE},
    State.DONE: set(),
}


def move(current, target):
    if target not in TRANSITIONS[current]:
        raise ValueError(f"cannot go from {current.value} to {target.value}")
    return target
