from __future__ import annotations

from abc import ABC, abstractmethod

from src.core.events import Event
from src.core.projections import StageProjection


class Agent(ABC):
    name: str

    @abstractmethod
    def act(
        self,
        run_id: str,
        turn: int,
        projection: StageProjection,
        recent_events: tuple[Event, ...],
    ) -> Event:
        raise NotImplementedError

