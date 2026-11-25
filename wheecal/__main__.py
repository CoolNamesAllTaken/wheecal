"""`python -m wheecal` (and the packaged app): no arguments starts the GUI, any argument the command line."""
import sys


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith('-psn_')]      # macOS Finder launch argument
    if args:
        from .cli import main as cli_main
        return cli_main(args)
    from .gui import main as gui_main
    return gui_main()


if __name__ == '__main__':
    sys.exit(main())
