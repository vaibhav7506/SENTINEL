"""Build/check a deterministic credential-free agent source bundle for enrollment."""

import argparse
import gzip
import hashlib
import io
import json
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / "backend/app/saas"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    files = [
        ROOT / "agent/pyproject.toml",
        ROOT / "agent/uv.lock",
        *sorted((ROOT / "agent/sentinel_agent").glob("*.py")),
    ]
    payload = io.BytesIO()
    hashes = {}
    with gzip.GzipFile(fileobj=payload, mode="wb", filename="", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for path in files:
                if path.is_symlink() or not path.is_file():
                    raise ValueError("Only regular source files are allowed")
                name = path.relative_to(ROOT / "agent").as_posix()
                # Git may expand LF to CRLF on Windows checkout. Package one
                # canonical byte representation on every build host.
                body = path.read_bytes().replace(b"\r\n", b"\n")
                hashes[name] = hashlib.sha256(body).hexdigest()
                info = tarfile.TarInfo(name)
                info.size = len(body)
                info.mode = 0o644
                info.mtime = 0
                archive.addfile(info, io.BytesIO(body))
    contents = payload.getvalue()
    manifest = {
        "version": "0.2.0",
        "sha256": hashlib.sha256(contents).hexdigest(),
        "sources": hashes,
    }
    if args.check:
        assert (DIRECTORY / "agent_bundle.tar.gz").read_bytes() == contents, "Agent bundle is stale"
        assert json.loads((DIRECTORY / "agent_bundle.json").read_text()) == manifest, (
            "Agent manifest is stale"
        )
    else:
        (DIRECTORY / "agent_bundle.tar.gz").write_bytes(contents)
        (DIRECTORY / "agent_bundle.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
    print("PASS deterministic, credential-free agent bundle")


if __name__ == "__main__":
    main()
