"""Local administration for the optional shared-project account store."""
import argparse
import getpass
from pathlib import Path
from backend.engine.shared_accounts import AccountStore


def main(argv=None):
    parser=argparse.ArgumentParser(description='Configure the first shared-server administrator locally')
    parser.add_argument('--shared-auth-dir',required=True,type=Path)
    parser.add_argument('--username',required=True)
    args=parser.parse_args(argv)
    password=getpass.getpass('New administrator password (12+ characters): ')
    repeated=getpass.getpass('Repeat password: ')
    if password!=repeated:parser.error('Passwords differ')
    try:user=AccountStore(args.shared_auth_dir/'accounts.sqlite').bootstrap(args.username,password)
    except (ValueError,OSError) as exc:parser.error(str(exc))
    print(f"Administrator {user['username']} configured. Start the server with the same --shared-auth-dir.")


if __name__=='__main__':main()
