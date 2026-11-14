"""Run the official quickstart cells with independent section error reporting."""
import argparse
import contextlib
import html
import io
import json
import os
from pathlib import Path
import re
import time

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
os.environ.setdefault("MPLBACKEND", "Agg")
os.environ.setdefault("MPLCONFIGDIR", str(ROOT / ".mpl-cache"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sections", default="1,2,3,4,5,6,7,8", help="Comma-separated section numbers; setup always runs")
    args = parser.parse_args()
    selected = {int(n) for n in args.sections.split(",")} | {1}
    if selected - set(range(1, 9)):
        parser.error("sections must be between 1 and 8")
    secrets = []
    if (ROOT / ".env").exists():
        for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if sep and not key.lstrip().startswith("#"):
                value = value.strip().strip("\"'")
                if value:
                    secrets.append(value)
    secrets += [v for k, v in os.environ.items() if any(s in k for s in ("TOKEN", "PASSWORD", "API_KEY")) and v]

    def sanitize(text):
        for value in sorted(secrets, key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        return re.sub(r"(https?://[^\s?'\"]+)\?[^\s'\"]+", r"\1?[REDACTED]", text)

    source = (ROOT / "matterport_quickstart.py").read_text(encoding="utf-8")
    namespace = {"__name__": "__main__"}
    results, section, failed_sections = [], 0, set()
    out = ROOT / "mp_out"
    out.mkdir(exist_ok=True)
    for cell in re.split(r"(?m)^# %%", source)[1:]:
        if cell.startswith(" [markdown]"):
            heading = re.search(r"# ## (\d+)\.", cell)
            if heading:
                section = int(heading[1])
            continue
        if section not in selected or (section in failed_sections and section == 6):
            continue
        if 1 in failed_sections and section in {3, 4, 5, 8}:
            if not any(r["section"] == section for r in results):
                results.append(dict(section=section, status="blocked", seconds=0, log="", error="Matterport authentication failed; refresh the key/token in .env."))
            continue
        print(f"Running section {section}, cell {len(results) + 1}...", flush=True)
        started, stream, error = time.monotonic(), io.StringIO(), None
        with contextlib.redirect_stdout(stream), contextlib.redirect_stderr(stream):
            try:
                exec(compile(cell.lstrip("\r\n"), "matterport_quickstart.py", "exec"), namespace)
            except (Exception, SystemExit) as exc:
                error = sanitize(f"{type(exc).__name__}: {exc}")
        log = sanitize(stream.getvalue())
        result = dict(section=section, status="failed" if error else "passed", seconds=round(time.monotonic()-started, 2), log=log, error=error)
        results.append(result)
        print(log, end="", flush=True)
        if error:
            print(error, flush=True)
            failed_sections.add(section)
        (out / "run_report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
        if section == 1 and not error and 1 in failed_sections:
            def indexed_latlon(sid=None):
                import pandas as pd
                frame = pd.read_csv(ROOT / "models_index.csv")
                row = frame.loc[frame.sid == (sid or namespace["MODEL"])].iloc[0]
                return float(row.lat), float(row.lon)
            namespace["latlon"] = indexed_latlon
            print("Using the shipped index for coordinates; authenticated Model API sections remain blocked.", flush=True)
    (out / "run_report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    images = sorted(p for p in out.iterdir() if p.suffix.lower() in {".png", ".jpg"})
    report = '<!doctype html><meta charset="utf-8"><title>Matterport test results</title><style>body{font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px;background:#f3f5f7}img{max-width:100%}pre{white-space:pre-wrap;background:white;padding:20px}section{margin:35px 0}</style><h1>Matterport quickstart test results</h1>'
    for r in results:
        report += f'<h2>Section {r["section"]}: {r["status"]} ({r["seconds"]}s)</h2><pre>{html.escape(r["log"] + (r["error"] or ""))}</pre>'
    for p in images:
        report += f'<section><h2>{html.escape(p.name)}</h2><img loading="lazy" src="{html.escape(p.name)}"></section>'
    (out / "index.html").write_text(report, encoding="utf-8")
    print(f"Report: {out / 'index.html'}", flush=True)
    return 1 if failed_sections else 0


if __name__ == "__main__":
    raise SystemExit(main())
