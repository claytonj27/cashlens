"""Write the synthetic fixtures to disk.

.venv/bin/python -m app.synthetic.generate            # writes ../../fixtures
.venv/bin/python -m app.synthetic.generate --check    # fails if files differ from a fresh build
"""

import json
import sys
from pathlib import Path

from app.synthetic.generator import build_all

DEFAULT_ROOT = Path(__file__).resolve().parents[4] / "fixtures"


def render(payload: dict) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def main(argv: list[str]) -> int:
    check = "--check" in argv
    root = Path(argv[argv.index("--out") + 1]) if "--out" in argv else DEFAULT_ROOT
    provider_dir, evaluation_dir = root / "provider", root / "evaluation"
    differences = 0
    for slug, (provider, evaluation) in build_all().items():
        for directory, payload in ((provider_dir, provider), (evaluation_dir, evaluation)):
            path = directory / f"{slug}.json"
            content = render(payload)
            if check:
                if not path.exists() or path.read_text(encoding="utf-8") != content:
                    print(f"differs: {path}")
                    differences += 1
                continue
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            print(f"wrote {path.relative_to(root.parent)} ({len(content)} bytes)")
    if check:
        print(
            "fixtures match a fresh build" if not differences else f"{differences} file(s) differ"
        )
        return 1 if differences else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
