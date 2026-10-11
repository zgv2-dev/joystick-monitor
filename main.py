import sys

if "--cli" in sys.argv:
    sys.argv.remove("--cli")
    from src.monitor import main
else:
    from src.gui import main

if __name__ == "__main__":
    main()
