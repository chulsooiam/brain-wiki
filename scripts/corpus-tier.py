#!/usr/bin/env python3
"""Register and inspect corpus tiers, including deprioritized ones.

A top-level folder under `.sources/` IS a curation tier: the folder name is
stamped into every chunk and `corpus-retrieve.py` adds a small per-tier bonus
at ranking time, so more authoritative material wins ties. Folders named
"…Tier N…" map to N automatically; anything else needs an entry in
`.vault-meta/corpus-tiers.json`.

WHY THIS EXISTS. That file is gitignored on purpose — it is per-vault config
that must not ship — so every tier registered by hand is state a fresh clone
loses silently, and the tier simply reverts to a zero bonus with nothing
announcing it. Two independent pipelines had each grown their own inline JSON
merge to work around that. This is the one place that owns it.

THE DEPRIORITIZED-TIER PATTERN, which is the case worth naming. Some material
must stay searchable without ever winning: content that has been retired but
not deleted, and machine- or vendor-generated paraphrase sitting alongside the
source it was derived from. Deleting it loses real answers ("why was this
retired?"); indexing it flat lets a superseded document outrank its own
replacement. Both have happened in this toolkit's own history:

  - A question bank archived 2,616 retired questions — MORE than its 1,832
    live ones. Indexed flat, a retired question came back for wording a live
    question also matched.
  - A source document deleted from a vault kept its corpus chunks (deleting a
    page does not prune them) and returned as the TOP TWO hits for the topic
    it had been removed over, outranking the document that replaced it.

A negative bonus fixes both: findable on a specific query, never the top
answer over the real source. The gap that matters is against tier 1, and
`list` prints it so the claim is checked rather than assumed.

Usage:
    corpus-tier.py list [--json]
    corpus-tier.py set "<folder>" [--tier LABEL] [--bonus N] [--dry-run]
    corpus-tier.py remove "<folder>" [--dry-run]

After changing a mapping, chunks must be re-stamped:
    corpus-index.py --rebuild && corpus-dedup.py --apply && corpus-bm25.py build
"""
import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

VAULT_ROOT = Path(__file__).resolve().parent.parent
SRC = VAULT_ROOT / ".sources"
CONFIG = VAULT_ROOT / ".vault-meta" / "corpus-tiers.json"

# A bonus is added to a similarity score that lives in roughly 0..1, so
# anything approaching 0.1 stops nudging ties and starts overriding relevance
# outright. Not a hard limit — an operator may know better — but it is warned
# about, because a too-large bonus fails in the direction that looks like the
# retrieval working.
BONUS_SANITY = 0.1


def load_defaults():
    """Default tier bonuses, read from corpus-retrieve.py rather than copied.

    Duplicating them here is how the two drift, and a drifted default is
    invisible: ranking just changes.
    """
    path = VAULT_ROOT / "scripts" / "corpus-retrieve.py"
    if not path.is_file():
        return {}
    spec = importlib.util.spec_from_file_location("_cr", str(path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["_cr"] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception as exc:                      # pragma: no cover
        print(f"warning: could not read default bonuses ({exc})",
              file=sys.stderr)
        return {}
    return dict(getattr(mod, "TIER_BONUS", {}) or {})


def read_config():
    if not CONFIG.exists():
        return {}
    try:
        data = json.loads(CONFIG.read_text(encoding="utf-8"))
    except ValueError as exc:
        sys.exit(f"error: {CONFIG.name} is malformed ({exc}) — fix or delete "
                 "it; refusing to overwrite a file that may hold "
                 "hand-written config")
    return data if isinstance(data, dict) else {}


def write_config(cfg, dry_run):
    if dry_run:
        print(json.dumps(cfg, indent=2, ensure_ascii=False))
        return
    CONFIG.parent.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                      encoding="utf-8", newline="\n")
    print(f"wrote {CONFIG.relative_to(VAULT_ROOT)}")


def auto_label(folder):
    """What tier_for() in corpus-index.py would infer with no mapping."""
    m = re.search(r"tier\s*(\d+)", folder, re.I)
    if m:
        return m.group(1)
    return re.sub(r"[^a-z0-9]+", "-", folder.lower()).strip("-")


def source_folders():
    if not SRC.is_dir():
        return []
    return sorted(p.name for p in SRC.iterdir()
                  if p.is_dir() and not p.name.startswith("."))


def effective(cfg, defaults):
    """(folder, label, bonus, mapped, on_disk) for every known tier."""
    mapping = {str(k): str(v) for k, v in (cfg.get("map") or {}).items()}
    bonus = {str(k): float(v) for k, v in (cfg.get("bonus") or {}).items()}
    merged = dict(defaults)
    merged.update(bonus)
    on_disk = source_folders()
    rows = []
    for folder in sorted(set(on_disk) | set(mapping)):
        label = mapping.get(folder, auto_label(folder))
        rows.append((folder, label, merged.get(label, 0.0),
                     folder in mapping, folder in on_disk))
    return rows, merged


def cmd_list(args):
    cfg, defaults = read_config(), load_defaults()
    rows, merged = effective(cfg, defaults)
    if args.json:
        print(json.dumps([{"folder": f, "tier": t, "bonus": b,
                           "mapped": m, "on_disk": d}
                          for f, t, b, m, d in rows], indent=2))
        return 0
    top = max([b for _, _, b, _, _ in rows] or [0.0])
    print(f"{'folder':38} {'tier':14} {'bonus':>7}  {'vs top':>7}  notes")
    for folder, label, bonus, mapped, on_disk in rows:
        notes = []
        if not mapped:
            notes.append("inferred")
        if not on_disk:
            notes.append("NOT ON DISK")
        if bonus < 0:
            notes.append("deprioritized")
        print(f"{folder[:38]:38} {label[:14]:14} {bonus:>+7.3f}  "
              f"{bonus - top:>+7.3f}  {', '.join(notes)}")
    print(f"\ndefault bonuses (from corpus-retrieve.py): {defaults}")
    print("'vs top' is the handicap against the highest-ranked tier: a "
          "deprioritized tier\nneeds a gap wide enough that it cannot win on "
          "wording its source also matches.")
    return 0


def register(folder, tier=None, bonus=None, dry_run=False, note=None):
    """Idempotently register one tier. Returns the list of changes made.

    This is the importable entry point, so a pipeline that CREATES a
    `.sources/` folder can register its tier in the same run instead of
    hand-merging this JSON — which is what two pipelines here were doing, and
    is how a merge that silently drops sibling entries gets written twice.
    Empty return means the config already said this.
    """
    folder = (folder or "").strip().strip("/\\")
    if not folder:
        raise ValueError("folder must not be empty")
    label = tier or auto_label(folder)
    cfg = read_config()
    cfg.setdefault("map", {})
    cfg.setdefault("bonus", {})

    changed = []
    if cfg["map"].get(folder) != label:
        cfg["map"][folder] = label
        changed.append(f"map[{folder}] -> {label}")
    if bonus is not None:
        if abs(bonus) > BONUS_SANITY:
            print(f"warning: |{bonus}| exceeds {BONUS_SANITY}; a bonus that "
                  "large overrides relevance instead of breaking ties",
                  file=sys.stderr)
        if cfg["bonus"].get(label) != bonus:
            cfg["bonus"][label] = bonus
            changed.append(f"bonus[{label}] -> {bonus:+.3f}")
    if note is not None and cfg.get("_note") != note:
        cfg["_note"] = note
        changed.append("_note updated")

    if changed and not dry_run:
        CONFIG.parent.mkdir(parents=True, exist_ok=True)
        CONFIG.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                          encoding="utf-8", newline="\n")
    return changed


def cmd_set(args):
    folder = args.folder.strip().strip("/\\")
    if not folder:
        sys.exit("error: folder must not be empty")
    if folder not in source_folders():
        print(f"note: '{folder}' is not a directory under .sources/ yet — "
              "registering anyway (a pipeline may create it later)")
    try:
        changed = register(folder, args.tier, args.bonus, args.dry_run)
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    if not changed:
        print("no change — already registered that way")
        return 0
    for c in changed:
        print(f"  {c}")
    if args.dry_run:
        print(json.dumps(read_config(), indent=2, ensure_ascii=False))
    else:
        print(f"wrote {CONFIG.relative_to(VAULT_ROOT)}")
        print("re-stamp chunks: corpus-index.py --rebuild && "
              "corpus-dedup.py --apply && corpus-bm25.py build")
    return 0


def cmd_remove(args):
    folder = args.folder.strip().strip("/\\")
    cfg = read_config()
    mapping = cfg.get("map") or {}
    if folder not in mapping:
        print(f"'{folder}' is not mapped; nothing to remove "
              f"(it would fall back to the inferred tier "
              f"'{auto_label(folder)}')")
        return 0
    label = mapping.pop(folder)
    # The bonus is keyed by LABEL, which other folders may share. Removing it
    # blindly would silently change their ranking too.
    others = [f for f, t in mapping.items() if t == label]
    if others:
        print(f"note: keeping bonus['{label}'] — still used by: "
              f"{', '.join(others)}")
    elif label in (cfg.get("bonus") or {}):
        del cfg["bonus"][label]
        print(f"  removed bonus[{label}]")
    print(f"  removed map[{folder}]")
    write_config(cfg, args.dry_run)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("list", help="show every tier and its effective bonus")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("set", help="register a folder as a tier")
    p.add_argument("folder")
    p.add_argument("--tier", help="tier label (default: inferred from name)")
    p.add_argument("--bonus", type=float,
                   help="additive rank bonus; NEGATIVE to deprioritize")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("remove", help="drop a folder's mapping")
    p.add_argument("folder")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=cmd_remove)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
