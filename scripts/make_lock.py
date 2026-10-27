"""Regenerate requirements.lock (hash-pinned, Windows x64 / CPython 3.13 operator machine).

    pip install --dry-run --ignore-installed --report report.json --only-binary=:all: \
        --platform win_amd64 --python-version 3.13 --target <empty-dir> \
        "numpy>=1.26,<3" "pandas>=2.1,<4" "scipy>=1.11,<2" "pyyaml>=6.0,<7" \
        "pytest>=8" "hypothesis>=6.100" "pyarrow>=14" colorama
    python scripts/make_lock.py report.json requirements.lock

colorama is listed explicitly because --platform does not apply pytest's win32 marker.
Every file hash PyPI publishes for each pinned version is included, so the lock also installs
on Linux/macOS with the same versions. CI installs it with --require-hashes.
"""
import json, sys, urllib.request
report, out = sys.argv[1], sys.argv[2]
pins = sorted((i["metadata"]["name"].lower(), i["metadata"]["version"]) for i in json.load(open(report))["install"])
lines = ["# Hash-pinned dependency lock for the OPERATOR MACHINE: Windows x64, CPython 3.13.",
         "# Install:  python -m pip install --require-hashes -r requirements.lock",
         "#           python -m pip install --no-deps -e .",
         "# Regenerate: see scripts/make_lock.py. Includes dev/test extras (pytest, hypothesis, pyarrow).", ""]
for name, ver in pins:
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/{ver}/json", timeout=60) as r:
        files = json.load(r)["urls"]
    hashes = sorted({f["digests"]["sha256"] for f in files})
    if not hashes:
        sys.exit(f"no files for {name}=={ver}")
    lines.append(f"{name}=={ver} \\")
    lines += [f"    --hash=sha256:{h}" + (" \\" if k < len(hashes) - 1 else "") for k, h in enumerate(hashes)]
open(out, "w").write("\n".join(lines) + "\n")
print(len(pins), "packages,", sum(l.count("sha256") for l in lines), "hashes")
