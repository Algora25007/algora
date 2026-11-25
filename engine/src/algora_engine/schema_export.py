"""Write the strategy JSON schema: ``python -m algora_engine.schema_export [path]``."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .dsl import json_schema


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    out = Path(args[0]) if args else Path(__file__).resolve().parents[2] / "schema" / "strategy.v1.json"
    out.write_text(json.dumps(json_schema(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
