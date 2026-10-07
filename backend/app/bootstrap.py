"""Run with python -m app.bootstrap USERNAME inside the migrated app container."""
import getpass
import sys
from sqlalchemy import select
from . import auth, models
from .db import SessionLocal


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python -m app.bootstrap USERNAME")
    with SessionLocal() as db:
        auth.lock(db)
        if db.scalar(select(models.User.id).where(models.User.is_admin == True)):
            raise SystemExit("An administrator already exists. Use the admin panel for account management.")
        password = getpass.getpass("Initial admin password (12+ characters): ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords do not match")
        user = models.User(username=sys.argv[1].strip().casefold(), password_hash=auth.password_hash(password),
            is_admin=True, disabled=False, must_change_password=True)
        db.add(user)
        db.flush()
        auth.audit(db, user, "bootstrapped", "user", user.id, after=auth.user_json(user))
        db.commit()
        print("Administrator created. Change the initial password at first sign-in.")


if __name__ == "__main__":
    main()
