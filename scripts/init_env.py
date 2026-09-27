"""Generate ignored local credentials without printing them or overwriting an existing .env."""

import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
content = (root / ".env.example").read_text(encoding="utf-8")
for key in ("POSTGRES_PASSWORD", "GRAFANA_ADMIN_PASSWORD"):
    content = content.replace(f"{key}=\n", f"{key}={secrets.token_urlsafe(32)}\n")
try:
    with (root / ".env").open("x", encoding="utf-8") as output:
        output.write(content)
except FileExistsError:
    raise SystemExit(".env already exists; left unchanged.") from None
print("Created .env with random local credentials. Keep this file private.")
