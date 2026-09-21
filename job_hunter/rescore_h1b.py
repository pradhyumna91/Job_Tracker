"""Re-run sponsorship analysis over every job already in the database.

Existing rows carry verdicts from the old substring matcher, which flagged
roughly a third of employers as verified sponsors on accidental matches. This
recomputes sponsorship_status and the H-1B evidence columns in place.

    python rescore_h1b.py --dry-run     # report what would change
    python rescore_h1b.py               # apply (writes a .bak first)
"""

import argparse
import json
import shutil
from collections import Counter
from datetime import datetime

from config import DB_PATH
from db import get_connection, init_db
from filters import check_sponsorship
from h1b_data import get_index


def rescore(dry_run: bool = False) -> None:
    init_db()
    index = get_index()
    print(f"H-1B index — {index.describe()}\n")

    conn = get_connection()
    rows = [dict(r) for r in conn.execute("SELECT * FROM jobs")]
    print(f"Scoring {len(rows):,} jobs...\n")

    transitions = Counter()
    updates = []

    for row in rows:
        before = row.get("sponsorship_status") or "unknown"

        tags = row.get("tags")
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except ValueError:
                tags = []
        row["tags"] = tags or []

        job = check_sponsorship(row)
        after = job["sponsorship_status"]
        transitions[(before, after)] += 1

        updates.append((
            after,
            1 if job["is_h1b_sponsor"] else 0,
            int(job.get("h1b_approvals") or 0),
            job.get("h1b_fiscal_year"),
            job.get("h1b_match"),
            job.get("h1b_confidence"),
            job.get("sponsorship_reason"),
            row["id"],
        ))

    print("Status changes (before -> after):")
    for (before, after), count in sorted(transitions.items(), key=lambda kv: -kv[1]):
        arrow = "   " if before == after else "  *"
        print(f"{arrow} {before:9} -> {after:9}  {count:5,}")

    changed = sum(c for (b, a), c in transitions.items() if b != a)
    print(f"\n{changed:,} of {len(rows):,} jobs change status.")

    by_match = Counter(u[4] for u in updates)
    print("\nHow employers matched:")
    for match, count in by_match.most_common():
        print(f"    {match or 'none':22} {count:5,}")

    if dry_run:
        print("\nDry run — nothing written.")
        conn.close()
        return

    backup = DB_PATH.with_name(
        f"{DB_PATH.name}.bak-before-h1b-rescore-{datetime.now():%Y%m%d-%H%M%S}"
    )
    shutil.copy2(DB_PATH, backup)
    print(f"\nBacked up database to {backup.name}")

    conn.executemany(
        """UPDATE jobs SET sponsorship_status = ?, is_h1b_sponsor = ?,
                  h1b_approvals = ?, h1b_fiscal_year = ?, h1b_match = ?,
                  h1b_confidence = ?, sponsorship_reason = ?
           WHERE id = ?""",
        updates,
    )
    conn.commit()
    conn.close()
    print(f"Updated {len(updates):,} rows.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="report changes without writing")
    rescore(**vars(parser.parse_args()))
