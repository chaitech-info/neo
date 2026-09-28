"""İstifadəçi yaradır (ilk admin də daxil olmaqla). Şifrə gizli soruşulur —
heç vaxt əmr arqumenti kimi ötürülmür (shell tarixçəsinə düşməsin).

    python -m scripts.create_user --email ad@sirket.az --name "Ad Soyad" --role admin
"""
from __future__ import annotations

import argparse
import getpass
import sys

from sqlalchemy.orm import Session

from app.auth.users import create_user
from app.db.engine import engine
from app.db.migrate import upgrade_to_head
from app.db.models import ROLE_CODES


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Admin panel istifadəçisi yarat")
    parser.add_argument("--email", required=True)
    parser.add_argument("--name", required=True, help="Ad Soyad")
    parser.add_argument("--role", required=True, choices=ROLE_CODES)
    args = parser.parse_args(argv)

    password = getpass.getpass("Şifrə (ən azı 10 simvol): ")
    if password != getpass.getpass("Şifrəni təkrarla: "):
        print("Şifrələr uyğun gəlmir.", file=sys.stderr)
        return 1

    upgrade_to_head(engine)
    with Session(engine) as session:
        try:
            user = create_user(session, email=args.email, full_name=args.name,
                               role_code=args.role, password=password)
        except ValueError as exc:
            print(f"Xəta: {exc}", file=sys.stderr)
            return 1
        print(f"Yaradıldı: {user.email} ({user.role_code})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
