"""
Creates the first Admin user. Run once, right after `python migrate.py`,
before starting the app for the first time.

Usage (recommended -- you'll be asked to type a password on screen):
    python scripts/bootstrap_admin.py <username> "<display name>"

If typing at that prompt doesn't seem to register anything (this happens on
some Windows terminals), you can instead pass the password directly:
    python scripts/bootstrap_admin.py <username> "<display name>" --password "your-password-here"
(Only do this in a terminal window you're about to close, since some
terminals keep a history of typed commands.)

Refuses to run if any user already exists, so it can't be used to create a
second admin account by accident -- use the Admin panel's Users screen for
that once you're logged in.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import auth  # noqa: E402
import db  # noqa: E402
import repositories.users as users_repo  # noqa: E402
import repositories.roles as roles_repo  # noqa: E402


def prompt_password():
    # Plain, visible input rather than hidden (getpass-style) entry. Hidden
    # password entry relies on the terminal supporting echo control, and on
    # some Windows terminals that silently fails -- the prompt appears but
    # keystrokes never register. Plain input() has no such dependency, so it
    # works everywhere; the trade-off is the password is visible as you type,
    # which is a reasonable one for a one-time local setup step.
    print()
    print("Type a password for this admin account (it WILL be visible as you type -- ")
    print("that's expected) and press Enter. At least 8 characters.")
    sys.stdout.flush()
    password = input("Password: ")
    confirm = input("Confirm password: ")
    return password, confirm


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--password")]
    password_flag = None
    for a in sys.argv[1:]:
        if a.startswith("--password="):
            password_flag = a.split("=", 1)[1]
    if "--password" in sys.argv:
        idx = sys.argv.index("--password")
        if idx + 1 < len(sys.argv):
            password_flag = sys.argv[idx + 1]
            args = [a for a in args if a != password_flag]

    if len(args) != 2:
        print('Usage: python scripts/bootstrap_admin.py <username> "<display name>" [--password "..."]')
        sys.exit(1)

    username, display_name = args[0], args[1]

    with db.connect() as conn:
        existing = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"]
        if existing > 0:
            print(f"Refusing to run: {existing} user(s) already exist. "
                  f"Use the Admin panel to add more users once you're logged in.")
            sys.exit(1)

        admin_role = roles_repo.get_role_by_name(conn, "Admin")
        if admin_role is None:
            print("Admin role not found -- run `python migrate.py` first.")
            sys.exit(1)

    if password_flag is not None:
        password = password_flag
        confirm = password_flag
    else:
        password, confirm = prompt_password()

    if len(password) < 8:
        print("Password must be at least 8 characters. Run the script again.")
        sys.exit(1)
    if password != confirm:
        print("Passwords did not match. Run the script again.")
        sys.exit(1)

    pw_hash, salt, iterations = auth.hash_password(password)
    with db.connect() as conn:
        user_id = users_repo.create_user(conn, username, display_name, pw_hash, salt, iterations, admin_role["id"])

    print()
    print(f"Created admin user '{username}' (id={user_id}). Start the app with `python app.py` and log in.")


if __name__ == "__main__":
    main()
