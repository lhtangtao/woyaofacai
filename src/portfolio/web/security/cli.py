"""CLI tool for local-only account management on Mac (AC-36)."""

import argparse
import sys

from portfolio.web.security.credentials import (
    create_user,
    get_user_by_username,
    init_security_db,
    list_users,
    set_user_password,
    toggle_user_enabled,
)
from portfolio.web.security.sessions import revoke_all_user_sessions


def main():
    parser = argparse.ArgumentParser(description="Money Portfolio Local Security CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # list
    subparsers.add_parser("list-users", help="List all managed users")

    # set-password
    p_set = subparsers.add_parser("set-password", help="Set user password")
    p_set.add_argument("--username", required=True, help="Username")
    p_set.add_argument("--password", required=True, help="New password")

    # create-user
    p_create = subparsers.add_parser("create-user", help="Create new user")
    p_create.add_argument("--username", required=True, help="Username")
    p_create.add_argument("--password", required=True, help="Password")
    p_create.add_argument("--role", default="user", choices=["admin", "user"], help="User role")
    p_create.add_argument("--person-id", type=int, default=None, help="Assigned person_id for asset isolation")
    p_create.add_argument("--person-name", default=None, help="Person display name")

    # toggle-user
    p_toggle = subparsers.add_parser("toggle-user", help="Enable or disable user")
    p_toggle.add_argument("--user-id", type=int, required=True, help="User ID")
    p_toggle.add_argument("--enable", action="store_true", help="Enable user")
    p_toggle.add_argument("--disable", action="store_true", help="Disable user")

    args = parser.parse_args()
    init_security_db()

    if args.command == "list-users":
        users = list_users()
        print("==================================================================")
        print(f"{'ID':4s} {'Username':15s} {'Role':8s} {'PersonID':10s} {'Enabled':8s}")
        print("------------------------------------------------------------------")
        for u in users:
            print(f"{u['id']:<4d} {u['username']:<15s} {u['role']:<8s} {str(u['person_id']):<10s} {'YES' if u['enabled'] else 'NO':<8s}")
        print("==================================================================")

    elif args.command == "set-password":
        u = get_user_by_username(args.username)
        if not u:
            print(f"Error: User '{args.username}' not found.")
            sys.exit(1)
        set_user_password(args.username, args.password)
        revoke_all_user_sessions(u["id"])
        print(f"✅ Successfully updated password for '{args.username}' and revoked active sessions.")

    elif args.command == "create-user":
        u = create_user(
            username=args.username,
            password=args.password,
            role=args.role,
            person_id=args.person_id,
            person_name=args.person_name,
        )
        print(f"✅ User '{args.username}' created successfully with ID {u['id']}.")

    elif args.command == "toggle-user":
        if not args.enable and not args.disable:
            print("Error: Specify --enable or --disable")
            sys.exit(1)
        enabled = True if args.enable else False
        toggle_user_enabled(args.user_id, enabled)
        if not enabled:
            revoke_all_user_sessions(args.user_id)
        print(f"✅ User ID {args.user_id} set enabled={enabled}.")


if __name__ == "__main__":
    main()
