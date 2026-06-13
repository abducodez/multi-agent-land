from src.core.conductor import Conductor
from src.core.events import Event
from src.core.ledger import Ledger
from src.core.projections import rebuild_stage
from src.scenarios.thousand_token_wood import build_scenario


def test_ledger_dedupes_by_event_id() -> None:
    ledger = Ledger()
    event = Event(run_id="run", turn=0, kind="world.observed", actor="test", payload={"text": "hello"})

    ledger.append(event)
    ledger.append(event)

    assert len(ledger.events) == 1


def test_projection_rebuilds_from_events() -> None:
    event = Event(run_id="run", turn=0, kind="user.injected", actor="visitor", payload={"text": "a brass moon"})

    projection = rebuild_stage((event,))

    assert projection.user_artifacts == ["a brass moon"]


def test_conductor_runs_vertical_slice() -> None:
    conductor = Conductor(build_scenario())

    conductor.reset("a test clearing")
    conductor.step()

    assert len(conductor.ledger.events) >= 3
    assert conductor.projection.current_scene != "The curtain has not risen."
