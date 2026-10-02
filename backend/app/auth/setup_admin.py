"""One-time interactive bootstrap for the first administrator."""

from __future__ import annotations

import getpass

from database.connection import make_engine, make_session_factory
from sqlalchemy import select

from backend.app.auth.schemas import Credentials
from backend.app.auth.service import password_hash
from backend.app.models import Role, User, UserRole


def main() -> None:
    username = input("Admin username: ").strip().lower()
    try:
        Credentials(username=username, password="TemporaryValidationPassword123!")
    except ValueError as exc:
        raise SystemExit(
            "Username must be 3-80 characters: letters, digits, dot, dash or underscore."
        ) from exc
    password = getpass.getpass("New admin password (12+ characters): ")
    confirm = getpass.getpass("Repeat password: ")
    if len(password) < 12 or len(password) > 256 or password != confirm:
        raise SystemExit("Passwords must match and be 12-256 characters.")
    engine = make_engine()
    factory = make_session_factory(engine)
    try:
        with factory.begin() as db:
            if db.scalar(select(User.id).limit(1)) is not None:
                raise SystemExit("An account already exists; first-admin setup is closed.")
            role = db.scalar(select(Role).where(Role.code == "ADMIN"))
            if role is None:
                raise SystemExit(
                    "RBAC catalog missing; run the demo seed after database migration."
                )
            user = User(username=username, password_hash=password_hash.hash(password))
            db.add(user)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=role.id))
        print(f"Administrator {username} created.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
