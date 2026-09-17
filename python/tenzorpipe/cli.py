"""``tenzor`` console script installed with the Python package."""
import sys


def main() -> None:
    from . import _engine

    sys.exit(_engine.run_cli(sys.argv[1:]))


if __name__ == "__main__":
    main()
