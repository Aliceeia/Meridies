"""Human review of community share requests. The ONLY way a memory becomes community.

    python connect/tools/review.py list                    # pending requests
    python connect/tools/review.py play <id>               # where the audio file is, and its transcript
    python connect/tools/review.py approve <id> --by <name>
    python connect/tools/review.py reject  <id> --by <name>

Only a pending_review request in a public space can be approved, and the author can still make it private
at any time afterwards. Run it on the machine that holds connect/data/memories/.
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
import connect.server as srv  # noqa: E402


def pending():
    return [m for m in srv.all_memories() if (m.get("share_request") or {}).get("status") == "pending_review"]


def decide(mid: str, verdict: str, by: str) -> dict:
    def fn(m):
        req = m.get("share_request") or {}
        if req.get("status") != "pending_review":
            return 409, f"no pending request (status: {req.get('status')})"
        if not srv.is_public_space(m["building_id"]):
            return 403, "not a public space"
        now = datetime.now(timezone.utc).isoformat(timespec="seconds")
        req.update(status=verdict, reviewed_at=now, review={**(req.get("review") or {}), "reviewer": f"human:{by}",
                                                             "status": verdict})
        if verdict == "approved":
            m["visibility"] = "community"
        m.setdefault("decisions", []).append({"choice": f"review-{verdict}", "reason": f"human:{by}", "at": now})
        return None
    m, code, err = srv.update(mid, fn)
    if err:
        raise SystemExit(f"{mid}: {err}")
    return m


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    p = sub.add_parser("play"); p.add_argument("id")
    for v in ("approve", "reject"):
        p = sub.add_parser(v); p.add_argument("id"); p.add_argument("--by", required=True, help="who reviewed it")
    a = ap.parse_args(argv)
    if a.cmd == "list":
        rows = pending()
        for m in rows:
            t = (m.get("transcript") or {}).get("text") or "(no transcript)"
            print(f"{m['id']}  {m['created_at']}  {m['building_id']}  {t[:70]}")
        print(f"{len(rows)} pending")
    elif a.cmd == "play":
        m = srv.load(a.id)
        print(srv.MEM / m["audio_file"])
        print((m.get("transcript") or {}).get("text") or "(no transcript)")
    else:
        m = decide(a.id, "approved" if a.cmd == "approve" else "rejected", a.by)
        print(f"{m['id']}: {m['share_request']['status']}, visibility {m['visibility']}")


if __name__ == "__main__":
    main()
