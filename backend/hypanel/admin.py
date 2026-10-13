"""Local, interactive account recovery. Requires container/host administration."""
import argparse
import getpass
from . import store
from .security import hash_password, audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["reset-password"])
    parser.add_argument("username")
    args = parser.parse_args()
    store.init()
    user = store.query("SELECT * FROM users WHERE username=? AND role='admin'", (args.username,), one=True)
    if not user:
        parser.error("Administrator nicht gefunden")
    password = getpass.getpass("Neues Passwort: ")
    if password != getpass.getpass("Passwort wiederholen: "):
        parser.error("Passwörter stimmen nicht überein")
    store.execute("UPDATE users SET password=?,disabled=0 WHERE id=?", (hash_password(password), user["id"]))
    store.execute("DELETE FROM sessions WHERE user_id=?", (user["id"],))
    audit("host-admin", "auth.password_reset", detail=args.username)
    print("Passwort geändert; bestehende Sessions widerrufen.")


if __name__ == "__main__":
    main()
