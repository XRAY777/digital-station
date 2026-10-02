"""Interactively create local demo accounts; never ship passwords."""

from __future__ import annotations

import getpass

from backend.app.auth.schemas import Credentials
from backend.app.auth.service import password_hash
from backend.app.auth.setup_admin import main as setup_admin
from backend.app.models import Role, User, UserRole
from database.connection import make_engine, make_session_factory
from sqlalchemy import select


def main() -> None:
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory() as db:
            has_user = db.scalar(select(User.id).limit(1)) is not None
        if not has_user:
            setup_admin()
        with factory() as db:
            dispatcher_role = db.scalar(select(Role).where(Role.code == "DISPATCHER"))
            if dispatcher_role is None:
                raise SystemExit("Run migrations and demo seed before account setup.")
            has_dispatcher = (
                db.scalar(
                    select(UserRole.user_id).where(UserRole.role_id == dispatcher_role.id).limit(1)
                )
                is not None
            )
        if has_dispatcher:
            return
        username = input("Dispatcher username: ").strip().lower()
        password = getpass.getpass("New dispatcher password (12+ characters): ")
        confirm = getpass.getpass("Repeat password: ")
        try:
            Credentials(username=username, password=password)
        except ValueError as exc:
            raise SystemExit("Invalid dispatcher username or password.") from exc
        if password != confirm:
            raise SystemExit("Passwords do not match.")
        with factory.begin() as db:
            if db.scalar(select(User.id).where(User.username == username)) is not None:
                raise SystemExit("Username already exists.")
            user = User(username=username, password_hash=password_hash.hash(password))
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=dispatcher_role.id))
        print(f"Dispatcher {username} created.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
