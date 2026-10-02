from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from alembic import command
from alembic.config import Config
from backend.app.models import (
    Block,
    Edge,
    Node,
    Occupancy,
    Signal,
    Switch,
    SwitchTransition,
    Track,
    Train,
    User,
)
from backend.app.services.topology import demo_yard, load_graph
from database.connection import make_engine, make_session_factory
from database.seed.demo import seed_demo
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError


def test_migration_and_demo_seed_are_repeatable() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "station.db"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
        command.upgrade(config, "head")
        command.upgrade(config, "head")
        engine = make_engine(f"sqlite:///{db_path.as_posix()}")
        factory = make_session_factory(engine)
        with factory() as session:
            station_id = seed_demo(session)
            seed_demo(session)
            assert len(inspect(engine).get_table_names()) >= 35
            assert (
                session.scalar(
                    select(func.count()).select_from(Train).where(Train.station_id == station_id)
                )
                == 7
            )
            assert session.scalar(select(func.count()).select_from(Track)) == 10
            assert session.scalar(select(func.count()).select_from(Node)) == 24
            assert session.scalar(select(func.count()).select_from(Edge)) == 64
            assert session.scalar(select(func.count()).select_from(Switch)) == 2
            assert session.scalar(select(func.count()).select_from(Signal)) == 20
            assert session.scalar(select(func.count()).select_from(SwitchTransition)) == 40
            yard = demo_yard(session)
            assert yard is not None
            graph = load_graph(session, yard)
            assert graph.validate() == []
            assert len(graph.paths("WEST-APPROACH", "EAST-APPROACH")) == 10
            assert len(graph.paths("EAST-APPROACH", "WEST-APPROACH")) == 10
            assert session.scalar(select(func.count()).select_from(User)) == 0
            occupancy = session.scalar(select(Occupancy))
            assert occupancy is not None
            assert session.get(Block, occupancy.block_id) is not None
        engine.dispose()


def test_only_one_current_train_can_occupy_a_block() -> None:
    with TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "station.db"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")
        command.upgrade(config, "head")
        engine = make_engine(f"sqlite:///{db_path.as_posix()}")
        factory = make_session_factory(engine)
        with factory() as session:
            seed_demo(session)
            occupancy = session.scalar(select(Occupancy))
            assert occupancy is not None
            duplicate = Occupancy(
                run_id=occupancy.run_id,
                train_id=occupancy.train_id,
                block_id=occupancy.block_id,
                entered_at_ms=1,
            )
            session.add(duplicate)
            with pytest.raises(IntegrityError):
                session.flush()
            session.rollback()
        engine.dispose()
