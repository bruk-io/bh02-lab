import argparse
import json


def counts(text: str) -> tuple[int, int]:
    """Lines and words in `text`."""
    return len(text.splitlines()), len(text.split())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count lines and words in a file.")
    parser.add_argument("path")
    parser.add_argument("--json", action="store_true", help="print one JSON object")
    args = parser.parse_args(argv)
    with open(args.path, encoding="utf-8") as file:
        lines, words = counts(file.read())
    if args.json:
        print(json.dumps({"path": args.path, "lines": lines, "words": words}))
    else:
        print(f"{lines} {words} {args.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
