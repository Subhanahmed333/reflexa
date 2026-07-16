from __future__ import annotations

from pathlib import Path

_package_dir = Path(__file__).resolve().parent / "Reflexa"
__path__ = [str(_package_dir)]  # type: ignore[assignment]

from Reflexa.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

