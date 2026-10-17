"""Document lifecycle state machine."""

from __future__ import annotations

from enum import StrEnum


class DocumentState(StrEnum):
    CARGADO = "CARGADO"
    EN_COLA = "EN_COLA"
    PROCESADO = "PROCESADO"
    EN_REVISION = "EN_REVISION"
    VALIDADO = "VALIDADO"
    APROBADO = "APROBADO"
    ERROR = "ERROR"
    DUPLICADO_PENDIENTE = "DUPLICADO_PENDIENTE"


S = DocumentState

TRANSITIONS: dict[DocumentState, frozenset[DocumentState]] = {
    S.CARGADO: frozenset({S.EN_COLA, S.ERROR}),
    S.DUPLICADO_PENDIENTE: frozenset({S.CARGADO, S.ERROR}),
    # EN_COLA -> EN_REVISION covers template alignment out of tolerance (HU06)
    S.EN_COLA: frozenset({S.PROCESADO, S.EN_REVISION, S.ERROR}),
    S.PROCESADO: frozenset({S.EN_REVISION, S.ERROR}),
    S.EN_REVISION: frozenset({S.VALIDADO, S.EN_COLA, S.ERROR}),
    # An observed record returns to the operator's tray (HU11)
    S.VALIDADO: frozenset({S.APROBADO, S.EN_REVISION}),
    S.APROBADO: frozenset(),
    # Manual reprocess after an error
    S.ERROR: frozenset({S.EN_COLA}),
}


class InvalidTransition(Exception):
    def __init__(self, current: DocumentState, target: DocumentState):
        super().__init__(f"Transición inválida: {current} → {target}")
        self.current = current
        self.target = target


def can_transition(current: DocumentState, target: DocumentState) -> bool:
    return target in TRANSITIONS[current]


def transition(current: DocumentState, target: DocumentState) -> DocumentState:
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    return target
