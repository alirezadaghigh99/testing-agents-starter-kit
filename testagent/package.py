"""Build the submission zip that you upload to eClass.

Usage:
    python -m testagent.package --run my-final --team team-07

The zip contains your student/ folder, REPORT.md, and the evaluation of your final configuration on
all development problems. It is only built if everything required is there.
"""

import argparse
import json
import re
import sys
import zipfile
from pathlib import Path

from testagent import REPO_ROOT
from testagent.problems import load_problems
from testagent.run import RUNS_DIR, STUDENT_DIR
from testagent.submission import check

REPORT = REPO_ROOT / "REPORT.md"
PLACEHOLDER = re.compile(r"<[a-z][^<>\n]*>", re.IGNORECASE)


def problems_to_check(run_dir: Path) -> list[str]:
    errors = []
    final = run_dir / "final" / "evaluation.json"
    if not final.exists():
        return [f"missing {final}: run `python -m testagent.final --run {run_dir.name}` and then "
                f"`python -m testagent.evaluate --run {run_dir.name} --mode final`"]
    evaluated = {p["problem_id"] for p in json.loads(final.read_text())["problems"]}
    expected = {p.id for p in load_problems(REPO_ROOT / "data" / "dev")}
    missing = sorted(expected - evaluated)
    if missing:
        errors.append(f"the final evaluation covers {len(evaluated)} of {len(expected)} development problems; "
                      f"missing: {', '.join(missing[:5])}{' ...' if len(missing) > 5 else ''}")
    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", required=True, help="the run that holds your final configuration")
    parser.add_argument("--team", required=True, help="your team name, used in the file name")
    args = parser.parse_args(argv)

    run_dir = RUNS_DIR / args.run
    errors = [f"student/: {e}" for e in check(STUDENT_DIR)]
    if not REPORT.exists():
        errors.append("missing REPORT.md: copy REPORT_TEMPLATE.md to REPORT.md and fill it in")
    elif PLACEHOLDER.search(REPORT.read_text()):
        errors.append("REPORT.md still has <placeholders> to fill in")
    errors += problems_to_check(run_dir)
    if errors:
        for error in errors:
            print(f"ERROR {error}")
        print("Submission not built.")
        return 1

    target = REPO_ROOT / f"submission-{args.team}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(STUDENT_DIR.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                archive.write(path, Path("student") / path.relative_to(STUDENT_DIR))
        archive.write(REPORT, "REPORT.md")
        for mode in ("blackbox", "whitebox", "combined", "final"):
            evaluation = run_dir / mode / "evaluation.json"
            if evaluation.exists():
                archive.write(evaluation, f"results/{mode}_evaluation.json")
            usage = run_dir / mode / "usage.json"
            if usage.exists():
                archive.write(usage, f"results/{mode}_usage.json")
    print(f"Built {target.name}. Upload this file to eClass.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
