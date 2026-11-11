import sys


def main(argv):
    if argv and argv[0] == "soak":
        from tools.keiko.soak import main as soak_main

        return soak_main(argv[1:])

    if argv and argv[0] == "birthdays":
        from tools.keiko.birthdays.repair import main as repair_main

        rest = argv[1:]
        if rest and rest[0] == "repair":
            rest = rest[1:]
        return repair_main(rest)

    from tools.keiko.logs.cli import main as logs_main

    return logs_main(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
