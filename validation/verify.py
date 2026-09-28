import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

meta = json.loads((Path(__file__).parent / "case.json").read_text())
work = Path(sys.argv[1]).resolve()
evidence = Path(sys.argv[2]).resolve()
evidence.mkdir(parents=True, exist_ok=True)
python = sys.executable
is_rm = meta["repo"].endswith("robustness_metrics")
source = work / meta["source"]
fixed = source.read_bytes()
summary = {}
runner = Path(__file__).parent / "pytest_with_absl.py"
prefix = [python, str(runner.resolve())] if is_rm else [python, "-m", "pytest"]


def run(label, command, expected=0, cwd=work, timeout=480):
    with (evidence / (label + ".log")).open("w") as log:
        proc = subprocess.run(command, cwd=cwd, env=os.environ.copy(),
                              stdout=log, stderr=subprocess.STDOUT, timeout=timeout)
    text = (evidence / (label + ".log")).read_text(errors="replace")
    print(label, "exit", proc.returncode, text[-450:], flush=True)
    assert proc.returncode == expected, (label, proc.returncode, text[-8000:])
    return text


def test(label, files, expected=0, coverage=False, cwd=work):
    xml = evidence / (label + ".xml")
    command = prefix + files + ["-q", "--tb=short", "--junitxml=" + str(xml)]
    if coverage:
        package = "robustness_metrics" if is_rm else "mt_metrics_eval"
        command += ["--cov=" + package, "--cov-branch",
                    "--cov-report=json:" + str(evidence / "coverage.json")]
    run(label, command, expected, cwd)
    tree = ET.parse(xml).getroot()
    counts = [sum(int(s.attrib.get(k, 0)) for s in tree.iter("testsuite"))
              for k in ("tests", "failures", "errors", "skipped")]
    failed = sorted({t.attrib.get("classname", "") + "::" + t.attrib["name"]
                     for t in tree.iter("testcase")
                     if t.find("failure") is not None or t.find("error") is not None})
    summary[label] = {"counts": counts, "failed_nodes": failed}
    print(label, counts, failed, flush=True)
    return counts, failed


assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=work, text=True).strip() == meta["head"]
print("TESTED SOURCE", meta["head"], flush=True)
run("dependencies", [python, "-m", "pip", "freeze"])
run("dependency-check", [python, "-m", "pip", "check"])
focused = [meta["test"]]
full = ["robustness_metrics/metrics/uncertainty_test.py", meta["test"]] if is_rm else ["mt_metrics_eval"]
expected_new = 23 if is_rm else 19
expected_original_failures = 17 if is_rm else 12
original = subprocess.check_output(["git", "show", meta["base"] + ":" + meta["source"]], cwd=work)
try:
    counts, failed = test("fixed-focused", focused)
    assert counts == [expected_new, 0, 0, 0]
    counts, failed = test("selected-suite", meta["selection"], coverage=True)
    assert counts == meta["selection_counts"]
    fixed_counts, fixed_failures = test("fixed-broader", full, expected=1)
    source.write_bytes(original)
    counts, failed = test("original-focused", focused, expected=1)
    assert counts == [expected_new, expected_original_failures, 0, 0]
    baseline_files = [full[0]] if is_rm else full + ["--ignore=" + meta["test"]]
    baseline_counts, baseline_failures = test("baseline-broader", baseline_files, expected=1)
    assert fixed_failures == baseline_failures
    assert fixed_counts[1:] == baseline_counts[1:]
    assert fixed_counts[0] == baseline_counts[0] + expected_new
finally:
    source.write_bytes(fixed)
assert source.read_bytes() == fixed
counts, failed = test("restored-focused", focused)
assert counts == [expected_new, 0, 0, 0]
run("test-lint", [python, "-m", "ruff", "check", "--select", "F,E501", "--line-length", "80", meta["test"]])
run("source-lint", [python, "-m", "ruff", "check", "--select", "E9,F63,F7,F82", meta["source"]])
run("source-restoration", ["git", "diff", "--exit-code"])
run("patch-check", ["git", "diff", "--check", meta["base"]])
run("package-build", [python, "-m", "build", "--outdir", str(evidence / "dist")])
wheel, = (evidence / "dist").glob("*.whl")
run("install-wheel", [python, "-m", "pip", "install", "--force-reinstall", "--no-deps", str(wheel)])
with tempfile.TemporaryDirectory() as directory:
    outside = Path(directory)
    copied = outside / Path(meta["test"]).name
    shutil.copyfile(work / meta["test"], copied)
    module = "robustness_metrics" if is_rm else "mt_metrics_eval"
    code = f"import {module}; from pathlib import Path; p=Path({module}.__file__).resolve(); print(p); assert not p.is_relative_to(Path({str(work)!r}))"
    run("installed-import", [python, "-c", code], cwd=outside)
    counts, failed = test("installed-focused", [str(copied)], cwd=outside)
    assert counts == [expected_new, 0, 0, 0]
(evidence / "summary.json").write_text(json.dumps(summary, indent=2))
print("VALIDATION COMPLETE", meta["head"], flush=True)
