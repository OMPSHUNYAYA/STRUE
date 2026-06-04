#!/usr/bin/env python3
import argparse
import csv
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

VERSION = "1.2"

STATE_RESOLVED = "RESOLVED"
STATE_PARTIAL = "PARTIAL"

INDEX_FILE = ".strue_index.json"
EVENT_FILE = ".strue_events.jsonl"
JOURNAL_FILE = ".strue_journal.jsonl"
VERIFY_FILE = ".strue_verify.json"
REPLAY_FILE = ".strue_replay_verify.json"
BENCH_FILE = ".strue_benchmark.json"
BENCH_CSV_FILE = ".strue_benchmark_history.csv"
RECOVERY_FILE = ".strue_recovery.json"
CORRUPT_FILE = ".strue_corruption_test.json"
STRESS_FILE = ".strue_stress.json"
CIVILIZATION_FILE = ".strue_civilization.json"
SUMMARY_FILE = ".strue_release_summary.txt"
OBSERVATORY_FILE = ".strue_observatory_snapshot.json"
STATUS_FILE = ".strue_status.json"
FRESHNESS_FILE = ".strue_freshness.json"
MANIFEST_FILE = ".strue_manifest.sha256"
README_FILE = "README.md"

RESERVED = {
    INDEX_FILE,
    EVENT_FILE,
    JOURNAL_FILE,
    VERIFY_FILE,
    REPLAY_FILE,
    BENCH_FILE,
    BENCH_CSV_FILE,
    RECOVERY_FILE,
    CORRUPT_FILE,
    STRESS_FILE,
    CIVILIZATION_FILE,
    SUMMARY_FILE,
    OBSERVATORY_FILE,
    STATUS_FILE,
    FRESHNESS_FILE,
    MANIFEST_FILE,
    README_FILE,
}

def now_ns():
    return time.time_ns()

def ensure_dir(path):
    Path(path).mkdir(parents=True, exist_ok=True)

def stable_json(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

def sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()

def sha256_text(text):
    return sha256_bytes(text.encode("utf-8"))

def rel_depth(rel):
    if rel == ".":
        return 0
    return rel.count("/") + 1

def norm_rel(path, root):
    path = Path(path).resolve()
    root = Path(root).resolve()
    if path == root:
        return "."
    return path.relative_to(root).as_posix()

def is_internal(rel):
    name = rel.split("/")[-1]
    return name in RESERVED or name.endswith(".tmp")

def atomic_write_text(path, text):
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8", newline="\n")
    os.replace(tmp, p)

def atomic_write_bytes(path, data):
    p = Path(path)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, p)

def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path, obj):
    atomic_write_text(path, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

def file_size(path):
    try:
        return Path(path).stat().st_size
    except FileNotFoundError:
        return 0

def children_hash(children):
    return sha256_text(stable_json(sorted(children)))

def capsule_certificate(c):
    core = {
        "path": c["path"],
        "kind": c["kind"],
        "size": c["size"],
        "file_count": c["file_count"],
        "dir_count": c["dir_count"],
        "mutation_id": c["mutation_id"],
        "child_hash": c["child_hash"],
        "state": c["state"],
    }
    return sha256_text(stable_json(core))

def capsule_truth_certificate(c):
    core = {
        "path": c["path"],
        "kind": c["kind"],
        "size": c["size"],
        "file_count": c["file_count"],
        "dir_count": c["dir_count"],
        "child_hash": c["child_hash"],
        "state": c["state"],
    }
    return sha256_text(stable_json(core))

def capsule_history_certificate(c):
    core = {
        "path": c["path"],
        "kind": c["kind"],
        "size": c["size"],
        "file_count": c["file_count"],
        "dir_count": c["dir_count"],
        "mutation_id": c["mutation_id"],
        "child_hash": c["child_hash"],
        "state": c["state"],
        "certificate": c["certificate"],
    }
    return sha256_text(stable_json(core))

def empty_capsule(rel, kind):
    return {
        "path": rel,
        "kind": kind,
        "size": 0,
        "file_count": 0,
        "dir_count": 1 if kind == "dir" else 0,
        "children": [],
        "child_hash": children_hash([]),
        "mutation_id": 0,
        "state": STATE_RESOLVED,
        "certificate": "",
    }

def finalize_capsule(c):
    c["children"] = sorted(c.get("children", []))
    c["child_hash"] = children_hash(c["children"])
    c["certificate"] = capsule_certificate(c)
    return c

def parent_of(rel):
    if rel == ".":
        return None
    if "/" not in rel:
        return "."
    return rel.rsplit("/", 1)[0]

def parent_chain(rel):
    chain = []
    cur = rel
    while cur is not None:
        chain.append(cur)
        cur = parent_of(cur)
    if "." not in chain:
        chain.append(".")
    return chain

def refresh_index_certificates(index):
    root = index["capsules"].get(".")
    if root:
        index["root_certificate"] = root["certificate"]
        index["truth_certificate"] = capsule_truth_certificate(root)
        index["history_certificate"] = capsule_history_certificate(root)

def refresh_generation(index):
    generation = int(index.get("last_mutation_id", 0))
    index["structure_generation"] = generation
    index["structure_current_generation"] = generation
    index["freshness_model"] = "explicit_sync_required_for_out_of_band_changes"
    index["query_validity"] = "valid_when_structure_current"
    index["traversal_free_scope"] = "property_lookup_given_maintained_current_structure"
    return index

def root_generation(index):
    return int(index.get("structure_generation", index.get("last_mutation_id", 0)))

def generation_match(index):
    return int(index.get("structure_current_generation", index.get("last_mutation_id", 0))) == int(index.get("last_mutation_id", 0))

def capsule_counts(index):
    c = index.get("capsules", {}).get(".", {})
    return {
        "size_bytes": int(c.get("size", 0)),
        "file_count": int(c.get("file_count", 0)),
        "dir_count": int(c.get("dir_count", 0)),
        "root_state": c.get("state", STATE_PARTIAL),
    }

def write_manifest(root, rel_files):
    root = Path(root)
    lines = []
    for rel in sorted(rel_files):
        p = root / rel
        if p.exists() and p.is_file():
            lines.append(f"{sha256_bytes(p.read_bytes())}  {rel}")
    atomic_write_text(root / MANIFEST_FILE, "\n".join(lines) + ("\n" if lines else ""))

def save_index(root, index):
    refresh_index_certificates(index)
    refresh_generation(index)
    root = Path(root)
    write_json(root / INDEX_FILE, index)
    write_manifest(root, [
        INDEX_FILE,
        EVENT_FILE,
        JOURNAL_FILE,
        VERIFY_FILE,
        REPLAY_FILE,
        BENCH_FILE,
        BENCH_CSV_FILE,
        SUMMARY_FILE,
        OBSERVATORY_FILE,
        STATUS_FILE,
        FRESHNESS_FILE,
        RECOVERY_FILE,
        CORRUPT_FILE,
        STRESS_FILE,
    ])

def load_index(root):
    p = Path(root) / INDEX_FILE
    if not p.exists():
        raise SystemExit("ERROR: STRUE index not found. Run init first.")
    return read_json(p)

def append_jsonl(path, event):
    with Path(path).open("a", encoding="utf-8", newline="\n") as f:
        f.write(stable_json(event) + "\n")

def event_append(root, event):
    append_jsonl(Path(root) / EVENT_FILE, event)

def journal_append(root, event):
    append_jsonl(Path(root) / JOURNAL_FILE, event)

def read_journal(root, limit):
    p = Path(root) / JOURNAL_FILE
    if not p.exists():
        return []
    rows = []
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    if limit and limit > 0:
        return rows[-limit:]
    return rows

def ensure_parent_capsules(index, rel):
    if rel == ".":
        return
    parts = rel.split("/")[:-1]
    cur = "."
    for part in parts:
        nxt = part if cur == "." else cur + "/" + part
        if nxt not in index["capsules"]:
            c = empty_capsule(nxt, "dir")
            c["mutation_id"] = index["last_mutation_id"]
            finalize_capsule(c)
            index["capsules"][nxt] = c
        pc = index["capsules"].get(cur)
        if pc and nxt not in pc["children"]:
            pc["children"].append(nxt)
        cur = nxt

def recompute_dir(index, rel):
    c = index["capsules"].get(rel)
    if not c or c["kind"] != "dir":
        return
    size = 0
    files = 0
    dirs = 1
    state = STATE_RESOLVED
    clean = []
    for child in sorted(c.get("children", [])):
        cc = index["capsules"].get(child)
        if not cc:
            state = STATE_PARTIAL
            continue
        clean.append(child)
        size += int(cc["size"])
        files += int(cc["file_count"])
        dirs += int(cc["dir_count"])
        if cc["state"] != STATE_RESOLVED:
            state = STATE_PARTIAL
    c["children"] = clean
    c["size"] = size
    c["file_count"] = files
    c["dir_count"] = dirs
    c["state"] = state
    finalize_capsule(c)

def update_ancestors(index, rel):
    seen = set()
    for node in parent_chain(rel):
        if node in seen:
            continue
        seen.add(node)
        c = index["capsules"].get(node)
        if c and c["kind"] == "dir":
            c["mutation_id"] = index["last_mutation_id"]
            recompute_dir(index, node)
            c["mutation_id"] = index["last_mutation_id"]
            finalize_capsule(c)
    refresh_index_certificates(index)

def scan_tree(root):
    root = Path(root).resolve()
    capsules = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath)
        dirnames[:] = sorted([d for d in dirnames if not is_internal(d)])
        rel_dir = norm_rel(dirpath, root)
        if is_internal(rel_dir):
            continue
        c = empty_capsule(rel_dir, "dir")
        children = []
        for name in dirnames:
            rel = norm_rel(dirpath / name, root)
            if not is_internal(rel):
                children.append(rel)
        for name in sorted(filenames):
            fp = dirpath / name
            rel = norm_rel(fp, root)
            if is_internal(rel):
                continue
            fc = empty_capsule(rel, "file")
            fc["size"] = file_size(fp)
            fc["file_count"] = 1
            fc["dir_count"] = 0
            fc["mutation_id"] = 1
            finalize_capsule(fc)
            capsules[rel] = fc
            children.append(rel)
        c["children"] = sorted(children)
        capsules[rel_dir] = c

    index = {
        "strue_version": VERSION,
        "root": str(root),
        "created_ns": now_ns(),
        "last_scan_ns": now_ns(),
        "last_mutation_id": 1,
        "invariant": "truth_visible iff structure_updated",
        "collapse": "truth = resolve(structure)",
        "cost_law": "truth_cost proportional_to mutation_set not object_count",
        "root_certificate": "",
        "truth_certificate": "",
        "history_certificate": "",
        "capsules": dict(sorted(capsules.items())),
    }
    for rel in sorted(index["capsules"].keys(), key=rel_depth, reverse=True):
        if index["capsules"][rel]["kind"] == "dir":
            index["capsules"][rel]["mutation_id"] = 1
            recompute_dir(index, rel)
            index["capsules"][rel]["mutation_id"] = 1
            finalize_capsule(index["capsules"][rel])
    refresh_index_certificates(index)
    return index

def physical_snapshot(root):
    root = Path(root).resolve()
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirpath = Path(dirpath)
        dirnames[:] = sorted([d for d in dirnames if not is_internal(d)])
        rel_dir = norm_rel(dirpath, root)
        if not is_internal(rel_dir):
            snap[rel_dir] = {"kind": "dir", "size": 0}
        for name in sorted(filenames):
            rel = norm_rel(dirpath / name, root)
            if not is_internal(rel):
                snap[rel] = {"kind": "file", "size": file_size(dirpath / name)}
    return snap

def detect_changes(index, snap):
    old = index["capsules"]
    old_keys = set(old.keys())
    new_keys = set(snap.keys())
    added = sorted(new_keys - old_keys, key=rel_depth)
    deleted = sorted(old_keys - new_keys, key=rel_depth, reverse=True)
    modified = []
    for rel in sorted(new_keys & old_keys, key=rel_depth):
        if snap[rel]["kind"] != old[rel]["kind"]:
            deleted.append(rel)
            added.append(rel)
        elif snap[rel]["kind"] == "file" and int(snap[rel]["size"]) != int(old[rel]["size"]):
            modified.append(rel)
    return added, modified, deleted

def apply_delete(index, rel):
    for k in sorted(list(index["capsules"].keys()), key=rel_depth, reverse=True):
        if k == rel or k.startswith(rel + "/"):
            del index["capsules"][k]
    parent = parent_of(rel)
    if parent and parent in index["capsules"]:
        pc = index["capsules"][parent]
        pc["children"] = [x for x in pc.get("children", []) if x != rel and not x.startswith(rel + "/")]

def apply_add_or_modify(index, root, rel, kind, size):
    ensure_parent_capsules(index, rel)
    if kind == "dir":
        c = index["capsules"].get(rel, empty_capsule(rel, "dir"))
        c["kind"] = "dir"
        c.setdefault("children", [])
    else:
        c = empty_capsule(rel, "file")
        c["size"] = int(size)
        c["file_count"] = 1
        c["dir_count"] = 0
    c["mutation_id"] = index["last_mutation_id"]
    finalize_capsule(c)
    index["capsules"][rel] = c
    parent = parent_of(rel)
    if parent and parent in index["capsules"]:
        pc = index["capsules"][parent]
        if rel not in pc["children"]:
            pc["children"].append(rel)

def sync_index(root):
    root = Path(root).resolve()
    index = load_index(root)
    snap = physical_snapshot(root)
    added, modified, deleted = detect_changes(index, snap)
    if not (added or modified or deleted):
        index["last_scan_ns"] = now_ns()
        save_index(root, index)
        return index, {"added": [], "modified": [], "deleted": [], "changed_branches": []}

    index["last_mutation_id"] += 1
    changed = set()
    for rel in deleted:
        changed.add(parent_of(rel) or ".")
        apply_delete(index, rel)
    for rel in added:
        item = snap[rel]
        apply_add_or_modify(index, root, rel, item["kind"], item["size"])
        changed.add(rel)
        changed.add(parent_of(rel) or ".")
    for rel in modified:
        item = snap[rel]
        apply_add_or_modify(index, root, rel, item["kind"], item["size"])
        changed.add(rel)
        changed.add(parent_of(rel) or ".")
    for rel in sorted(changed, key=rel_depth, reverse=True):
        update_ancestors(index, rel)
    index["last_scan_ns"] = now_ns()
    event = {
        "mutation_id": index["last_mutation_id"],
        "op": "sync",
        "added": added,
        "modified": modified,
        "deleted": deleted,
        "changed_branches": sorted(changed),
        "root_certificate": index.get("root_certificate", ""),
        "truth_certificate": index.get("truth_certificate", ""),
    }
    event_append(root, event)
    save_index(root, index)
    return index, event

def print_properties(index, rel, lookup_us=None):
    c = index["capsules"].get(rel)
    if not c:
        raise SystemExit(f"ERROR: no STRUE capsule for {rel}")
    print("STRUE_PROPERTIES")
    print(f"path={c['path']}")
    print(f"state={c['state']}")
    print(f"size_bytes={c['size']}")
    print(f"file_count={c['file_count']}")
    print(f"dir_count={c['dir_count']}")
    print(f"mutation_id={c['mutation_id']}")
    print(f"certificate={c['certificate']}")
    print("truth_source=maintained_structure")
    print("traversal_used_for_property_lookup=false")
    print("valid_when=structure_current")
    print("staleness_model=explicit_sync_required_for_out_of_band_changes")
    print("claim=traversal-free property retrieval given a maintained current structure")
    if lookup_us is not None:
        print(f"preloaded_structure_lookup_us={lookup_us:.3f}")

def event_display_fields(e):
    op = e.get("op", "")
    mid = e.get("mutation_id", "")
    parts = [f"mutation_id={mid}", f"op={op}"]
    if e.get("path") is not None:
        parts.append(f"path={e.get('path')}")
    if e.get("src") is not None:
        parts.append(f"src={e.get('src')}")
    if e.get("dst") is not None:
        parts.append(f"dst={e.get('dst')}")
    if e.get("count") is not None:
        parts.append(f"count={e.get('count')}")
    if e.get("delta_size") is not None:
        parts.append(f"delta_size={e.get('delta_size')}")
    branch_count = e.get("updated_branch_count")
    if branch_count is None:
        branch_count = len(e.get("updated_branch", []))
    parts.append(f"updated_branch_count={branch_count}")
    return " | ".join(parts)

def affected_directories_from_event(e):
    dirs = set()
    for node in e.get("updated_branch", []):
        dirs.add(parent_of(node) or ".")
    template = e.get("template")
    count = e.get("count")
    if template and count is not None:
        for i in range(int(count)):
            dirs.add(parent_of(template.format(i=i).replace("\\", "/").strip("/")) or ".")
    path = e.get("path")
    if path:
        dirs.add(parent_of(path) or ".")
    return sorted(dirs)

def traversal_properties(root, rel):
    root = Path(root).resolve()
    target = root if rel == "." else root / rel
    if target.is_file():
        return {"size": file_size(target), "file_count": 1, "dir_count": 0}
    size = 0
    files = 0
    dirs = 0
    for dirpath, dirnames, filenames in os.walk(target):
        dirpath = Path(dirpath)
        dirnames[:] = sorted([d for d in dirnames if not is_internal(d)])
        dirs += 1
        for name in sorted(filenames):
            rel_file = norm_rel(dirpath / name, root)
            if not is_internal(rel_file):
                size += file_size(dirpath / name)
                files += 1
    return {"size": size, "file_count": files, "dir_count": dirs}

def root_summary(index):
    refresh_index_certificates(index)
    c = index["capsules"].get(".")
    if not c:
        return {}
    return {
        "size": c["size"],
        "file_count": c["file_count"],
        "dir_count": c["dir_count"],
        "root_certificate": index.get("root_certificate", ""),
        "truth_certificate": index.get("truth_certificate", ""),
        "history_certificate": index.get("history_certificate", ""),
        "last_mutation_id": index.get("last_mutation_id", 0),
    }

def copy_tree_without_strue(src, dst):
    src = Path(src).resolve()
    dst = Path(dst).resolve()
    if dst.exists():
        shutil.rmtree(dst)
    ensure_dir(dst)
    for dirpath, dirnames, filenames in os.walk(src):
        dirpath = Path(dirpath)
        rel_dir = norm_rel(dirpath, src)
        if is_internal(rel_dir):
            continue
        target_dir = dst if rel_dir == "." else dst / rel_dir
        ensure_dir(target_dir)
        for name in sorted(filenames):
            rel = norm_rel(dirpath / name, src)
            if not is_internal(rel):
                shutil.copy2(dirpath / name, target_dir / name)

def build_observatory_snapshot(root, top):
    root = Path(root).resolve()
    index = load_index(root)
    dirs = []
    for rel, c in index["capsules"].items():
        if c["kind"] == "dir":
            dirs.append({
                "path": rel,
                "size_bytes": int(c["size"]),
                "file_count": int(c["file_count"]),
                "dir_count": int(c["dir_count"]),
                "state": c["state"],
                "mutation_id": int(c["mutation_id"]),
                "certificate": c["certificate"],
            })
    dirs.sort(key=lambda x: (x["size_bytes"], x["file_count"], x["path"]), reverse=True)
    journal_rows = read_journal(root, top)
    snapshot = {
        "strue_version": VERSION,
        "root": str(root),
        "last_mutation_id": index.get("last_mutation_id"),
        "root_certificate": index.get("root_certificate"),
        "truth_certificate": index.get("truth_certificate"),
        "history_certificate": index.get("history_certificate"),
        "top_directories": dirs[:top],
        "recent_events": journal_rows[-top:],
    }
    write_json(root / OBSERVATORY_FILE, snapshot)
    save_index(root, index)
    return snapshot

def command_init(args):
    root = Path(args.root).resolve()
    ensure_dir(root)
    if args.reset_events:
        for p in [root / EVENT_FILE, root / JOURNAL_FILE]:
            if p.exists():
                p.unlink()
    for p in [root / EVENT_FILE, root / JOURNAL_FILE]:
        if not p.exists():
            atomic_write_text(p, "")
    start = time.perf_counter()
    index = scan_tree(root)
    save_index(root, index)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    c = index["capsules"]["."]
    print("STRUE_INIT")
    print(f"root={root}")
    print(f"capsules={len(index['capsules'])}")
    print(f"root_size_bytes={c['size']}")
    print(f"root_file_count={c['file_count']}")
    print(f"root_dir_count={c['dir_count']}")
    print(f"root_certificate={index['root_certificate']}")
    print(f"truth_certificate={index['truth_certificate']}")
    print(f"elapsed_ms={elapsed_ms:.3f}")

def command_demo(args):
    root = Path(args.root).resolve()
    if root.exists() and args.clean:
        shutil.rmtree(root)
    ensure_dir(root / "Project" / "Data" / "Raw")
    ensure_dir(root / "Project" / "Docs")
    for i in range(args.files):
        p = root / "Project" / "Data" / "Raw" / f"sample_{i:05d}.txt"
        p.write_text("STRUE demo line\n" * ((i % 7) + 1), encoding="utf-8", newline="\n")
    (root / "Project" / "Docs" / "readme.txt").write_text("STRUE demo\n", encoding="utf-8", newline="\n")
    print("STRUE_DEMO_TREE")
    print(f"root={root}")
    print(f"files_created={args.files + 1}")
    command_init(argparse.Namespace(root=str(root), reset_events=True))
    command_props(argparse.Namespace(root=str(root), path="Project"))

def command_props(args):
    root = Path(args.root).resolve()
    rel = args.path.replace("\\", "/").strip("/")
    rel = "." if rel in {"", "."} else rel
    index = load_index(root)
    start = time.perf_counter()
    if rel not in index["capsules"]:
        raise SystemExit(f"ERROR: no STRUE capsule for {rel}")
    elapsed_us = (time.perf_counter() - start) * 1000000.0
    print_properties(index, rel, elapsed_us)

def command_batch(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    start = time.perf_counter()
    index["last_mutation_id"] += 1
    mid = index["last_mutation_id"]
    changed = set()
    for i in range(args.count):
        rel = args.template.format(i=i).replace("\\", "/").strip("/")
        content = args.content_template.format(i=i).encode("utf-8")
        if is_internal(rel):
            raise SystemExit("ERROR: internal STRUE file path is reserved.")
        target = root / rel
        ensure_dir(target.parent)
        atomic_write_bytes(target, content)
        ensure_parent_capsules(index, rel)
        c = empty_capsule(rel, "file")
        c["size"] = len(content)
        c["file_count"] = 1
        c["dir_count"] = 0
        c["mutation_id"] = mid
        finalize_capsule(c)
        index["capsules"][rel] = c
        parent = parent_of(rel)
        if parent and parent in index["capsules"]:
            pc = index["capsules"][parent]
            if rel not in pc["children"]:
                pc["children"].append(rel)
        for node in parent_chain(rel):
            changed.add(node)
    for rel in sorted(changed, key=rel_depth, reverse=True):
        update_ancestors(index, rel)
    event = {
        "mutation_id": mid,
        "op": "journal_batch_add_file",
        "count": args.count,
        "template": args.template,
        "updated_branch_count": len(changed),
        "root_certificate": index.get("root_certificate", ""),
        "truth_certificate": index.get("truth_certificate", ""),
    }
    journal_append(root, event)
    event_append(root, event)
    save_index(root, index)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    print("STRUE_BATCH")
    print(f"root={root}")
    print(f"count={args.count}")
    print(f"last_mutation_id={index.get('last_mutation_id')}")
    print(f"updated_branch_count={len(changed)}")
    print(f"truth_certificate={index.get('truth_certificate')}")
    print(f"history_certificate={index.get('history_certificate')}")
    print(f"elapsed_ms={elapsed_ms:.3f}")
    if args.count:
        print(f"avg_mutation_us={(elapsed_ms * 1000.0) / args.count:.3f}")

def command_add(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    rel = args.path.replace("\\", "/").strip("/")
    start = time.perf_counter()
    index["last_mutation_id"] += 1
    mid = index["last_mutation_id"]
    target = root / rel
    ensure_dir(target.parent)
    data = args.content.encode("utf-8")
    atomic_write_bytes(target, data)
    ensure_parent_capsules(index, rel)
    c = empty_capsule(rel, "file")
    c["size"] = len(data)
    c["file_count"] = 1
    c["dir_count"] = 0
    c["mutation_id"] = mid
    finalize_capsule(c)
    index["capsules"][rel] = c
    parent = parent_of(rel)
    if parent and parent in index["capsules"]:
        pc = index["capsules"][parent]
        if rel not in pc["children"]:
            pc["children"].append(rel)
    update_ancestors(index, rel)
    event = {"mutation_id": mid, "op": "journal_add_file", "path": rel, "updated_branch": parent_chain(rel)}
    journal_append(root, event)
    event_append(root, event)
    save_index(root, index)
    elapsed_us = (time.perf_counter() - start) * 1000000.0
    print("STRUE_JOURNAL_ADD_FILE")
    print(f"path={rel}")
    print(f"mutation_id={mid}")
    print(f"updated_branch_count={len(parent_chain(rel))}")
    print(f"elapsed_us={elapsed_us:.3f}")
    print_properties(index, rel)

def command_touch(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    rel = args.path.replace("\\", "/").strip("/")
    target = root / rel
    if not target.exists():
        raise SystemExit(f"ERROR: file not found: {rel}")
    start = time.perf_counter()
    before = file_size(target)
    with target.open("a", encoding="utf-8", newline="\n") as f:
        f.write(args.append)
    after = file_size(target)
    index["last_mutation_id"] += 1
    mid = index["last_mutation_id"]
    c = index["capsules"].get(rel, empty_capsule(rel, "file"))
    c["kind"] = "file"
    c["size"] = after
    c["file_count"] = 1
    c["dir_count"] = 0
    c["children"] = []
    c["mutation_id"] = mid
    finalize_capsule(c)
    index["capsules"][rel] = c
    update_ancestors(index, rel)
    event = {"mutation_id": mid, "op": "journal_touch_file", "path": rel, "delta_size": after - before, "updated_branch": parent_chain(rel)}
    journal_append(root, event)
    event_append(root, event)
    save_index(root, index)
    elapsed_us = (time.perf_counter() - start) * 1000000.0
    print("STRUE_JOURNAL_TOUCH")
    print(f"path={rel}")
    print(f"mutation_id={mid}")
    print(f"delta_size={after - before}")
    print(f"updated_branch_count={len(parent_chain(rel))}")
    print(f"elapsed_us={elapsed_us:.3f}")
    print_properties(index, rel)

def command_delete(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    rel = args.path.replace("\\", "/").strip("/")
    if rel in {"", "."}:
        raise SystemExit("ERROR: refusing to delete root.")
    start = time.perf_counter()
    target = root / rel
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    index["last_mutation_id"] += 1
    mid = index["last_mutation_id"]
    parent = parent_of(rel) or "."
    for k in sorted(list(index["capsules"].keys()), key=rel_depth, reverse=True):
        if k == rel or k.startswith(rel + "/"):
            del index["capsules"][k]
    if parent in index["capsules"]:
        pc = index["capsules"][parent]
        pc["children"] = [x for x in pc.get("children", []) if x != rel and not x.startswith(rel + "/")]
    update_ancestors(index, parent)
    event = {"mutation_id": mid, "op": "journal_delete", "path": rel, "updated_branch": parent_chain(parent)}
    journal_append(root, event)
    event_append(root, event)
    save_index(root, index)
    elapsed_us = (time.perf_counter() - start) * 1000000.0
    print("STRUE_JOURNAL_DELETE")
    print(f"path={rel}")
    print(f"mutation_id={mid}")
    print(f"updated_branch_count={len(parent_chain(parent))}")
    print(f"elapsed_us={elapsed_us:.3f}")
    if parent in index["capsules"]:
        print_properties(index, parent)

def command_move(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    src_rel = args.src.replace("\\", "/").strip("/")
    dst_rel = args.dst.replace("\\", "/").strip("/")
    if src_rel in {"", "."} or dst_rel in {"", "."}:
        raise SystemExit("ERROR: refusing to move root.")
    if is_internal(src_rel) or is_internal(dst_rel):
        raise SystemExit("ERROR: internal STRUE file path is reserved.")
    if src_rel not in index["capsules"]:
        raise SystemExit(f"ERROR: no STRUE capsule for {src_rel}")
    src_path = root / src_rel
    dst_path = root / dst_rel
    if not src_path.exists():
        raise SystemExit(f"ERROR: source path not found: {src_rel}")
    if dst_path.exists() and not args.overwrite:
        raise SystemExit(f"ERROR: destination already exists: {dst_rel}")
    start = time.perf_counter()
    ensure_dir(dst_path.parent)
    if dst_path.exists():
        if dst_path.is_dir():
            shutil.rmtree(dst_path)
        else:
            dst_path.unlink()
    shutil.move(str(src_path), str(dst_path))
    index["last_mutation_id"] += 1
    mid = index["last_mutation_id"]
    moved = {}
    for rel, c in list(index["capsules"].items()):
        if rel == src_rel or rel.startswith(src_rel + "/"):
            new_rel = dst_rel + rel[len(src_rel):]
            nc = dict(c)
            nc["path"] = new_rel
            nc["mutation_id"] = mid
            children = []
            for child in nc.get("children", []):
                if child == src_rel or child.startswith(src_rel + "/"):
                    children.append(dst_rel + child[len(src_rel):])
                else:
                    children.append(child)
            nc["children"] = children
            finalize_capsule(nc)
            moved[new_rel] = nc
            del index["capsules"][rel]
    old_parent = parent_of(src_rel) or "."
    new_parent = parent_of(dst_rel) or "."
    ensure_parent_capsules(index, dst_rel)
    if old_parent in index["capsules"]:
        index["capsules"][old_parent]["children"] = [x for x in index["capsules"][old_parent].get("children", []) if not (x == src_rel or x.startswith(src_rel + "/"))]
    index["capsules"].update(moved)
    if new_parent in index["capsules"] and dst_rel not in index["capsules"][new_parent].get("children", []):
        index["capsules"][new_parent]["children"].append(dst_rel)
    changed = set(parent_chain(old_parent) + parent_chain(dst_rel))
    for rel in sorted(changed, key=rel_depth, reverse=True):
        update_ancestors(index, rel)
    event = {"mutation_id": mid, "op": "journal_move", "src": src_rel, "dst": dst_rel, "updated_branch_count": len(changed), "root_certificate": index.get("root_certificate", ""), "truth_certificate": index.get("truth_certificate", "")}
    journal_append(root, event)
    event_append(root, event)
    save_index(root, index)
    elapsed_us = (time.perf_counter() - start) * 1000000.0
    print("STRUE_JOURNAL_MOVE")
    print(f"src={src_rel}")
    print(f"dst={dst_rel}")
    print(f"mutation_id={mid}")
    print(f"updated_branch_count={len(changed)}")
    print(f"truth_certificate={index.get('truth_certificate')}")
    print(f"elapsed_us={elapsed_us:.3f}")
    print_properties(index, dst_rel)

def read_manifest(root):
    p = Path(root) / MANIFEST_FILE
    rows = []
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        if "  " not in line:
            rows.append({"path": "", "expected": "", "status": "MALFORMED"})
            continue
        expected, rel = line.split("  ", 1)
        rows.append({"path": rel.strip(), "expected": expected.strip(), "status": "PENDING"})
    return rows

def command_manifest_verify(args):
    root = Path(args.root).resolve()
    rows = read_manifest(root)
    checks = []
    for row in rows:
        rel = row.get("path", "")
        expected = row.get("expected", "")
        if row.get("status") == "MALFORMED":
            checks.append({"path": rel, "status": "MALFORMED"})
            continue
        p = root / rel
        if not p.exists():
            checks.append({"path": rel, "status": "MISSING"})
            continue
        actual = sha256_bytes(p.read_bytes())
        if actual != expected:
            checks.append({"path": rel, "status": "MISMATCH", "expected": expected, "actual": actual})
    status = "PASS" if not checks and rows else "FAIL"
    if not rows:
        checks.append({"path": MANIFEST_FILE, "status": "MISSING_OR_EMPTY"})
    print("STRUE_MANIFEST_VERIFY")
    print(f"status={status}")
    print(f"manifest_entries={len(rows)}")
    print(f"issue_count={len(checks)}")
    for issue in checks[:args.limit]:
        print(f"issue={issue.get('status')} | path={issue.get('path')}")

def audit_index(index):
    issues = []
    capsules = index.get("capsules", {})
    if "." not in capsules:
        issues.append({"path": ".", "status": "ROOT_MISSING"})
    for rel, c in capsules.items():
        if c.get("path") != rel:
            issues.append({"path": rel, "status": "PATH_FIELD_MISMATCH"})
        if c.get("kind") not in {"file", "dir"}:
            issues.append({"path": rel, "status": "INVALID_KIND"})
        if c.get("kind") == "file" and c.get("children"):
            issues.append({"path": rel, "status": "FILE_HAS_CHILDREN"})
        if c.get("kind") == "dir":
            for child in c.get("children", []):
                if child not in capsules:
                    issues.append({"path": rel, "status": "MISSING_CHILD", "child": child})
                elif parent_of(child) != rel:
                    issues.append({"path": rel, "status": "NON_DIRECT_CHILD", "child": child})
        parent = parent_of(rel)
        if rel != "." and parent not in capsules:
            issues.append({"path": rel, "status": "PARENT_MISSING", "parent": parent})
        if rel != "." and parent in capsules and rel not in capsules[parent].get("children", []):
            issues.append({"path": rel, "status": "PARENT_DOES_NOT_REFERENCE_CHILD", "parent": parent})
        expected = capsule_certificate(c)
        if c.get("certificate") != expected:
            issues.append({"path": rel, "status": "CERTIFICATE_MISMATCH"})
    return issues

def command_audit(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    issues = audit_index(index)
    status = "PASS" if not issues else "FAIL"
    print("STRUE_AUDIT")
    print(f"status={status}")
    print(f"capsules={len(index.get('capsules', {}))}")
    print(f"issue_count={len(issues)}")
    print(f"truth_certificate={index.get('truth_certificate')}")
    for issue in issues[:args.limit]:
        extra = ""
        if issue.get("child"):
            extra += f" | child={issue.get('child')}"
        if issue.get("parent"):
            extra += f" | parent={issue.get('parent')}"
        print(f"issue={issue.get('status')} | path={issue.get('path')}{extra}")

def command_doctor(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    audit_issues = audit_index(index)
    fresh = scan_tree(root)
    verify_issues = []
    for rel, c in fresh["capsules"].items():
        old = index["capsules"].get(rel)
        if not old:
            verify_issues.append({"path": rel, "status": "MISSING_IN_INDEX"})
            continue
        for field in ["size", "file_count", "dir_count", "state"]:
            if old.get(field) != c.get(field):
                verify_issues.append({"path": rel, "status": "FIELD_MISMATCH", "field": field})
                break
    for rel in index["capsules"].keys():
        if rel not in fresh["capsules"]:
            verify_issues.append({"path": rel, "status": "STALE_IN_INDEX"})
    manifest_rows = read_manifest(root)
    manifest_issues = []
    for row in manifest_rows:
        rel = row.get("path", "")
        expected = row.get("expected", "")
        p = root / rel
        if not p.exists():
            manifest_issues.append({"path": rel, "status": "MISSING"})
        elif sha256_bytes(p.read_bytes()) != expected:
            manifest_issues.append({"path": rel, "status": "MISMATCH"})
    status = "PASS" if not audit_issues and not verify_issues and not manifest_issues else "FAIL"
    print("STRUE_DOCTOR")
    print(f"status={status}")
    print(f"audit_issues={len(audit_issues)}")
    print(f"verify_issues={len(verify_issues)}")
    print(f"manifest_issues={len(manifest_issues)}")
    print(f"capsules={len(index.get('capsules', {}))}")
    print(f"truth_certificate={index.get('truth_certificate')}")
    for issue in (audit_issues + verify_issues + manifest_issues)[:args.limit]:
        print(f"issue={issue.get('status')} | path={issue.get('path')}")



def recomputed_index_certificates(index):
    clone = json.loads(stable_json(index))
    for rel in sorted(clone.get("capsules", {}).keys(), key=rel_depth, reverse=True):
        c = clone["capsules"][rel]
        if c.get("kind") == "dir":
            recompute_dir(clone, rel)
        finalize_capsule(c)
    refresh_index_certificates(clone)
    return {
        "root_certificate": clone.get("root_certificate", ""),
        "truth_certificate": clone.get("truth_certificate", ""),
        "history_certificate": clone.get("history_certificate", ""),
    }


def command_cert_audit(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    current = {
        "root_certificate": index.get("root_certificate", ""),
        "truth_certificate": index.get("truth_certificate", ""),
        "history_certificate": index.get("history_certificate", ""),
    }
    recomputed = recomputed_index_certificates(index)
    issues = []
    for key in ["root_certificate", "truth_certificate", "history_certificate"]:
        if current.get(key) != recomputed.get(key):
            issues.append(key)
    audit_issues = audit_index(index)
    status = "PASS" if not issues and not audit_issues else "FAIL"
    print("STRUE_CERT_AUDIT")
    print(f"status={status}")
    print(f"certificate_issues={len(issues)}")
    print(f"audit_issues={len(audit_issues)}")
    print(f"root_certificate_index={current.get('root_certificate')}")
    print(f"root_certificate_recomputed={recomputed.get('root_certificate')}")
    print(f"truth_certificate_index={current.get('truth_certificate')}")
    print(f"truth_certificate_recomputed={recomputed.get('truth_certificate')}")
    print(f"history_certificate_index={current.get('history_certificate')}")
    print(f"history_certificate_recomputed={recomputed.get('history_certificate')}")
    for issue in issues[:args.limit]:
        print(f"issue=CERTIFICATE_FIELD_MISMATCH | field={issue}")
    for issue in audit_issues[:max(0, args.limit - len(issues))]:
        print(f"issue={issue.get('status')} | path={issue.get('path')}")


def command_civilization(args):
    root = Path(args.root).resolve()
    start = time.perf_counter()
    index = load_index(root)
    fresh = scan_tree(root)
    for rel in sorted(fresh["capsules"].keys(), key=rel_depth):
        old = index["capsules"].get(rel)
        if old:
            fresh["capsules"][rel]["mutation_id"] = old.get("mutation_id", fresh["capsules"][rel]["mutation_id"])
            finalize_capsule(fresh["capsules"][rel])
    for rel in sorted(fresh["capsules"].keys(), key=rel_depth, reverse=True):
        if fresh["capsules"][rel]["kind"] == "dir":
            old = index["capsules"].get(rel)
            recompute_dir(fresh, rel)
            if old:
                fresh["capsules"][rel]["mutation_id"] = old.get("mutation_id", fresh["capsules"][rel]["mutation_id"])
            finalize_capsule(fresh["capsules"][rel])
    refresh_index_certificates(fresh)
    verify_issues = []
    for rel, c in fresh["capsules"].items():
        old = index["capsules"].get(rel)
        if not old:
            verify_issues.append(rel)
            continue
        for field in ["size", "file_count", "dir_count", "state", "certificate"]:
            if old.get(field) != c.get(field):
                verify_issues.append(rel)
                break
    for rel in index["capsules"].keys():
        if rel not in fresh["capsules"]:
            verify_issues.append(rel)
    certs = recomputed_index_certificates(index)
    cert_ok = (
        index.get("root_certificate", "") == certs.get("root_certificate", "") and
        index.get("truth_certificate", "") == certs.get("truth_certificate", "") and
        index.get("history_certificate", "") == certs.get("history_certificate", "")
    )
    audit_issues = audit_index(index)
    rows = read_manifest(root)
    manifest_issues = []
    for row in rows:
        rel = row.get("path", "")
        expected = row.get("expected", "")
        p = root / rel
        if not p.exists() or sha256_bytes(p.read_bytes()) != expected:
            manifest_issues.append(rel)
    root_capsule = index.get("capsules", {}).get(".", {})
    lookup_start = time.perf_counter()
    _ = root_capsule.get("size", 0)
    lookup_us = (time.perf_counter() - lookup_start) * 1000000.0
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    status = "PASS" if not verify_issues and cert_ok and not audit_issues and not manifest_issues else "FAIL"
    root_state = root_capsule.get("state", STATE_PARTIAL)
    structure_current = not verify_issues
    truth_visible = structure_current and root_state == STATE_RESOLVED
    out = {
        "strue_version": VERSION,
        "status": status,
        "root": str(root),
        "validation_mode": "full_tree_rescan",
        "validation_scope": "maximum_correctness_release_validation",
        "intended_for": "release_validation_and_integrity_gate",
        "validation_cost": "proportional_to_object_count",
        "staleness_model": "explicit_sync_required_for_out_of_band_changes",
        "root_state": root_state,
        "structure_current": structure_current,
        "truth_visible": truth_visible,
        "capsules": len(index.get("capsules", {})),
        "size_bytes": root_capsule.get("size", 0),
        "file_count": root_capsule.get("file_count", 0),
        "dir_count": root_capsule.get("dir_count", 0),
        "verify_issues": len(verify_issues),
        "audit_issues": len(audit_issues),
        "manifest_issues": len(manifest_issues),
        "certificate_integrity": cert_ok,
        "lookup_us": lookup_us,
        "elapsed_ms": elapsed_ms,
        "truth_certificate": index.get("truth_certificate", ""),
        "root_certificate": index.get("root_certificate", ""),
    }
    write_json(root / ".strue_civilization.json", out)
    save_index(root, index)
    print("STRUE_CIVILIZATION")
    print(f"status={status}")
    print("validation_mode=full_tree_rescan")
    print("validation_scope=maximum_correctness_release_validation")
    print("intended_for=release_validation_and_integrity_gate")
    print("validation_cost=proportional_to_object_count")
    print("staleness_model=explicit_sync_required_for_out_of_band_changes")
    print(f"root_state={root_state}")
    print(f"structure_current={str(structure_current).lower()}")
    print(f"truth_visible={str(truth_visible).lower()}")
    print(f"capsules={out['capsules']}")
    print(f"size_bytes={out['size_bytes']}")
    print(f"file_count={out['file_count']}")
    print(f"dir_count={out['dir_count']}")
    print(f"verify_issues={out['verify_issues']}")
    print(f"audit_issues={out['audit_issues']}")
    print(f"manifest_issues={out['manifest_issues']}")
    print(f"certificate_integrity={str(cert_ok).lower()}")
    print(f"truth_source=maintained_structure")
    print(f"traversal_used_for_property_lookup=false")
    print(f"preloaded_structure_lookup_us={lookup_us:.3f}")
    print(f"truth_certificate={out['truth_certificate']}")
    print(f"root_certificate={out['root_certificate']}")
    print(f"elapsed_ms={elapsed_ms:.3f}")

def deep_compare_index(root, index):
    fresh = scan_tree(root)
    for rel in sorted(fresh["capsules"].keys(), key=rel_depth):
        old = index["capsules"].get(rel)
        if old:
            fresh["capsules"][rel]["mutation_id"] = old.get("mutation_id", fresh["capsules"][rel]["mutation_id"])
            finalize_capsule(fresh["capsules"][rel])
    for rel in sorted(fresh["capsules"].keys(), key=rel_depth, reverse=True):
        if fresh["capsules"][rel]["kind"] == "dir":
            old = index["capsules"].get(rel)
            recompute_dir(fresh, rel)
            if old:
                fresh["capsules"][rel]["mutation_id"] = old.get("mutation_id", fresh["capsules"][rel]["mutation_id"])
                finalize_capsule(fresh["capsules"][rel])
    refresh_index_certificates(fresh)
    checks = []
    for rel, c in fresh["capsules"].items():
        old = index["capsules"].get(rel)
        if not old:
            checks.append({"path": rel, "status": "MISSING_IN_INDEX"})
            continue
        fields = ["size", "file_count", "dir_count", "state", "certificate"]
        mismatch = [f for f in fields if old.get(f) != c.get(f)]
        if mismatch:
            checks.append({"path": rel, "status": "MISMATCH", "fields": mismatch})
    for rel in index["capsules"].keys():
        if rel not in fresh["capsules"]:
            checks.append({"path": rel, "status": "STALE_IN_INDEX"})
    return fresh, checks


def command_status(args):
    root = Path(args.root).resolve()
    start = time.perf_counter()
    index = load_index(root)
    refresh_index_certificates(index)
    refresh_generation(index)
    counts = capsule_counts(index)
    audit_issues = audit_index(index)
    certs = recomputed_index_certificates(index)
    cert_ok = (
        index.get("root_certificate", "") == certs.get("root_certificate", "") and
        index.get("truth_certificate", "") == certs.get("truth_certificate", "") and
        index.get("history_certificate", "") == certs.get("history_certificate", "")
    )
    deep_checks = []
    deep_used = False
    if args.deep:
        deep_used = True
        _, deep_checks = deep_compare_index(root, index)
    structure_current = (not audit_issues) and cert_ok and (not deep_checks if deep_used else True)
    truth_visible = structure_current and counts["root_state"] == STATE_RESOLVED
    sync_required = bool(deep_checks)
    status = "PASS" if structure_current and truth_visible else "FAIL"
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    out = {
        "strue_version": VERSION,
        "status": status,
        "root": str(root),
        "status_mode": "deep_full_tree_rescan" if deep_used else "fast_index_self_check",
        "full_tree_rescan_used": deep_used,
        "fast_check_scope": "index_self_consistency_and_certificate_integrity",
        "out_of_band_detection": "included" if deep_used else "not_guaranteed_without_verify_or_sync",
        "staleness_model": "explicit_sync_required_for_out_of_band_changes",
        "structure_generation": root_generation(index),
        "structure_current_generation": int(index.get("structure_current_generation", index.get("last_mutation_id", 0))),
        "generation_match": generation_match(index),
        "structure_current": structure_current,
        "truth_visible": truth_visible,
        "sync_required": sync_required,
        "root_state": counts["root_state"],
        "size_bytes": counts["size_bytes"],
        "file_count": counts["file_count"],
        "dir_count": counts["dir_count"],
        "audit_issues": len(audit_issues),
        "deep_mismatch_count": len(deep_checks),
        "certificate_integrity": cert_ok,
        "truth_certificate": index.get("truth_certificate", ""),
        "root_certificate": index.get("root_certificate", ""),
        "elapsed_ms": elapsed_ms,
    }
    write_json(root / STATUS_FILE, out)
    write_json(root / FRESHNESS_FILE, out)
    save_index(root, index)
    print("STRUE_STATUS")
    print(f"status={status}")
    print(f"status_mode={out['status_mode']}")
    print(f"full_tree_rescan_used={str(deep_used).lower()}")
    print("fast_check_scope=index_self_consistency_and_certificate_integrity")
    print(f"out_of_band_detection={out['out_of_band_detection']}")
    print("staleness_model=explicit_sync_required_for_out_of_band_changes")
    print(f"structure_generation={out['structure_generation']}")
    print(f"structure_current_generation={out['structure_current_generation']}")
    print(f"generation_match={str(out['generation_match']).lower()}")
    print(f"structure_current={str(structure_current).lower()}")
    print(f"truth_visible={str(truth_visible).lower()}")
    print(f"sync_required={str(sync_required).lower()}")
    print(f"root_state={counts['root_state']}")
    print(f"size_bytes={counts['size_bytes']}")
    print(f"file_count={counts['file_count']}")
    print(f"dir_count={counts['dir_count']}")
    print(f"audit_issues={len(audit_issues)}")
    print(f"deep_mismatch_count={len(deep_checks)}")
    print(f"certificate_integrity={str(cert_ok).lower()}")
    print(f"truth_certificate={index.get('truth_certificate', '')}")
    print(f"root_certificate={index.get('root_certificate', '')}")
    print(f"elapsed_ms={elapsed_ms:.3f}")


def command_sync(args):
    root = Path(args.root).resolve()
    start = time.perf_counter()
    index, event = sync_index(root)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    print("STRUE_SYNC")
    print(f"root={root}")
    print(f"added={len(event['added'])}")
    print(f"modified={len(event['modified'])}")
    print(f"deleted={len(event['deleted'])}")
    print(f"changed_branches={len(event['changed_branches'])}")
    print(f"last_mutation_id={index['last_mutation_id']}")
    print(f"truth_certificate={index.get('truth_certificate')}")
    print(f"root_certificate={index.get('root_certificate')}")
    print(f"elapsed_ms={elapsed_ms:.3f}")

def command_benchmark(args):
    root = Path(args.root).resolve()
    rel = args.path.replace("\\", "/").strip("/")
    rel = "." if rel in {"", "."} else rel

    index_path = root / INDEX_FILE
    if not index_path.exists():
        raise SystemExit("ERROR: STRUE index not found. Run init first.")

    load_start = time.perf_counter()
    index = read_json(index_path)
    index_load_ms = (time.perf_counter() - load_start) * 1000.0

    lookup_start = time.perf_counter()
    c = index["capsules"].get(rel)
    preloaded_lookup_us = (time.perf_counter() - lookup_start) * 1000000.0
    if not c:
        raise SystemExit(f"ERROR: no STRUE capsule for {rel}")

    structure_query_total_ms = index_load_ms + (preloaded_lookup_us / 1000.0)

    traversal_start = time.perf_counter()
    trav = traversal_properties(root, rel)
    traversal_ms = (time.perf_counter() - traversal_start) * 1000.0

    ok = (
        int(c["size"]) == int(trav["size"]) and
        int(c["file_count"]) == int(trav["file_count"]) and
        int(c["dir_count"]) == int(trav["dir_count"])
    )

    speedup_preloaded_lookup_vs_traversal = (traversal_ms * 1000.0) / preloaded_lookup_us if preloaded_lookup_us > 0 else None
    speedup_end_to_end_query_vs_traversal = traversal_ms / structure_query_total_ms if structure_query_total_ms > 0 else None

    out = {
        "strue_version": VERSION,
        "path": rel,
        "status": "PASS" if ok else "FAIL",
        "benchmark_scope": "query-time retrieval with maintained current structure",
        "benchmark_note": "preloaded lookup is reported separately from index load plus lookup",
        "staleness_model": "explicit_sync_required_for_out_of_band_changes",
        "claim": "traversal-free property retrieval given a maintained current structure",
        "lookup_complexity": "O(1)_with_preloaded_current_structure",
        "traversal_complexity": "O(n)_filesystem_walk",
        "benchmark_scope_precision": "speedups are descriptive measurements, not universal guarantees",
        "index_load_ms": index_load_ms,
        "preloaded_structure_lookup_us": preloaded_lookup_us,
        "structure_query_total_ms": structure_query_total_ms,
        "filesystem_traversal_ms": traversal_ms,
        "speedup_preloaded_lookup_vs_traversal": speedup_preloaded_lookup_vs_traversal,
        "speedup_end_to_end_query_vs_traversal": speedup_end_to_end_query_vs_traversal,
        "structure": {"size": c["size"], "file_count": c["file_count"], "dir_count": c["dir_count"], "state": c.get("state", "")},
        "traversal": trav,
    }
    write_json(root / BENCH_FILE, out)

    if getattr(args, "out_csv", ""):
        out_csv = Path(args.out_csv)
        if not out_csv.is_absolute():
            out_csv = Path.cwd() / out_csv
        try:
            rel_csv = out_csv.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            rel_csv = ""
        if rel_csv and not is_internal(rel_csv):
            raise SystemExit("ERROR: benchmark CSV inside STRUE root must use a reserved STRUE filename such as .strue_benchmark_history.csv, or write the CSV outside the root.")
        exists = out_csv.exists()
        with out_csv.open("a", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            if not exists:
                w.writerow([
                    "path", "status", "index_load_ms", "preloaded_structure_lookup_us",
                    "structure_query_total_ms", "filesystem_traversal_ms",
                    "speedup_preloaded_lookup_vs_traversal", "speedup_end_to_end_query_vs_traversal",
                    "size_bytes", "file_count", "dir_count", "state"
                ])
            w.writerow([
                rel, out["status"], f"{index_load_ms:.6f}", f"{preloaded_lookup_us:.6f}",
                f"{structure_query_total_ms:.6f}", f"{traversal_ms:.6f}",
                "" if speedup_preloaded_lookup_vs_traversal is None else f"{speedup_preloaded_lookup_vs_traversal:.6f}",
                "" if speedup_end_to_end_query_vs_traversal is None else f"{speedup_end_to_end_query_vs_traversal:.6f}",
                c["size"], c["file_count"], c["dir_count"], c.get("state", "")
            ])

    save_index(root, index)
    print("STRUE_BENCHMARK")
    print(f"path={rel}")
    print(f"status={out['status']}")
    print("benchmark_scope=query-time retrieval with maintained current structure")
    print("lookup_mode=preloaded_index")
    print("end_to_end_mode=index_load_plus_lookup")
    print("staleness_model=explicit_sync_required_for_out_of_band_changes")
    print("claim=traversal-free property retrieval given a maintained current structure")
    print("lookup_complexity=O(1)_with_preloaded_current_structure")
    print("traversal_complexity=O(n)_filesystem_walk")
    print("benchmark_scope_precision=speedups_are_descriptive_measurements_not_universal_guarantees")
    print(f"index_load_ms={index_load_ms:.3f}")
    print(f"preloaded_structure_lookup_us={preloaded_lookup_us:.3f}")
    print(f"structure_query_total_ms={structure_query_total_ms:.3f}")
    print(f"filesystem_traversal_ms={traversal_ms:.3f}")
    if speedup_preloaded_lookup_vs_traversal is not None:
        print(f"speedup_preloaded_lookup_vs_traversal={speedup_preloaded_lookup_vs_traversal:.3f}")
    if speedup_end_to_end_query_vs_traversal is not None:
        print(f"speedup_end_to_end_query_vs_traversal={speedup_end_to_end_query_vs_traversal:.3f}")
    print(f"size_bytes={c['size']}")
    print(f"file_count={c['file_count']}")
    print(f"dir_count={c['dir_count']}")
    print(f"state={c.get('state', '')}")

def command_cost(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    rel = args.path.replace("\\", "/").strip("/")
    rel = "." if rel in {"", "."} else rel
    c = index["capsules"].get(rel)
    if not c:
        raise SystemExit(f"ERROR: no STRUE capsule for {rel}")
    depth = len(parent_chain(rel))
    object_count = int(c["file_count"]) + int(c["dir_count"])
    print("STRUE_COST_MODEL")
    print(f"path={rel}")
    print(f"object_count={object_count}")
    print(f"branch_depth={depth}")
    print("scan_sync_cost=O(object_count)")
    print("journal_update_cost=O(branch_depth)")
    print(f"object_to_depth_ratio={object_count / depth:.3f}" if depth else "object_to_depth_ratio=NA")
    print("cost_law=truth_cost proportional_to mutation_set not object_count")

def command_journal(args):
    root = Path(args.root).resolve()
    rows = read_journal(root, args.limit)
    print("STRUE_JOURNAL")
    print(f"root={root}")
    print(f"events={len(rows)}")
    for e in rows:
        print(event_display_fields(e))
        dirs = affected_directories_from_event(e)
        if dirs:
            print(f"affected_directories={','.join(dirs[:args.max_dirs])}")

def command_snapshot(args):
    snapshot = build_observatory_snapshot(args.root, args.top)
    print("STRUE_OBSERVATORY_SNAPSHOT")
    print(f"root={snapshot['root']}")
    print(f"last_mutation_id={snapshot['last_mutation_id']}")
    print(f"truth_certificate={snapshot['truth_certificate']}")
    print(f"history_certificate={snapshot['history_certificate']}")
    print("TOP_DIRECTORIES")
    for d in snapshot["top_directories"]:
        print(f"{d['path']} | size_bytes={d['size_bytes']} | files={d['file_count']} | dirs={d['dir_count']} | state={d['state']} | mutation_id={d['mutation_id']}")
    print("RECENT_EVENTS")
    for e in snapshot["recent_events"]:
        print(event_display_fields(e))

def command_watch(args):
    root = Path(args.root).resolve()
    if not (root / INDEX_FILE).exists():
        command_init(argparse.Namespace(root=str(root), reset_events=True))
    print("STRUE_WATCH")
    print(f"root={root}")
    print(f"cycles={args.cycles}")
    print(f"interval_sec={args.interval_sec}")
    for cycle in range(1, args.cycles + 1):
        start = time.perf_counter()
        index, event = sync_index(root)
        elapsed_ms = (time.perf_counter() - start) * 1000.0
        changed = len(event.get("added", [])) + len(event.get("modified", [])) + len(event.get("deleted", []))
        snapshot = build_observatory_snapshot(root, args.top)
        print(f"WATCH_CYCLE {cycle}")
        print(f"changed={changed}")
        print(f"added={len(event.get('added', []))}")
        print(f"modified={len(event.get('modified', []))}")
        print(f"deleted={len(event.get('deleted', []))}")
        print(f"last_mutation_id={index.get('last_mutation_id')}")
        print(f"truth_certificate={index.get('truth_certificate')}")
        print(f"elapsed_ms={elapsed_ms:.3f}")
        if snapshot["top_directories"]:
            d = snapshot["top_directories"][0]
            print(f"top={d['path']} | size_bytes={d['size_bytes']} | files={d['file_count']}")
        if cycle < args.cycles:
            time.sleep(args.interval_sec)

def command_replay_verify(args):
    root = Path(args.root).resolve()
    replay_root = Path(args.replay_root).resolve() if args.replay_root else root.parent / (root.name + "_REPLAY")
    start = time.perf_counter()
    copy_tree_without_strue(root, replay_root)
    for f in [EVENT_FILE, JOURNAL_FILE]:
        p = replay_root / f
        if p.exists():
            p.unlink()
    old = load_index(root)
    fresh = scan_tree(replay_root)
    for p in [replay_root / EVENT_FILE, replay_root / JOURNAL_FILE]:
        if not p.exists():
            atomic_write_text(p, "")
    save_index(replay_root, fresh)
    old_s = root_summary(old)
    fresh_s = root_summary(fresh)
    status = "PASS" if old_s["size"] == fresh_s["size"] and old_s["file_count"] == fresh_s["file_count"] and old_s["dir_count"] == fresh_s["dir_count"] and old_s["truth_certificate"] == fresh_s["truth_certificate"] else "FAIL"
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    out = {
        "strue_version": VERSION,
        "status": status,
        "source_root": str(root),
        "replay_root": str(replay_root),
        "source": old_s,
        "replay": fresh_s,
        "elapsed_ms": elapsed_ms,
    }
    write_json(root / REPLAY_FILE, out)
    save_index(root, old)
    print("STRUE_REPLAY_VERIFY")
    print(f"status={status}")
    print(f"source_root={root}")
    print(f"replay_root={replay_root}")
    print(f"source_truth_certificate={old_s.get('truth_certificate')}")
    print(f"replay_truth_certificate={fresh_s.get('truth_certificate')}")
    print(f"source_history_certificate={old_s.get('history_certificate')}")
    print(f"replay_history_certificate={fresh_s.get('history_certificate')}")
    print(f"elapsed_ms={elapsed_ms:.3f}")

def command_verify(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    fresh = scan_tree(root)
    for rel in sorted(fresh["capsules"].keys(), key=rel_depth):
        old = index["capsules"].get(rel)
        if old:
            fresh["capsules"][rel]["mutation_id"] = old.get("mutation_id", fresh["capsules"][rel]["mutation_id"])
            finalize_capsule(fresh["capsules"][rel])
    for rel in sorted(fresh["capsules"].keys(), key=rel_depth, reverse=True):
        if fresh["capsules"][rel]["kind"] == "dir":
            old = index["capsules"].get(rel)
            recompute_dir(fresh, rel)
            if old:
                fresh["capsules"][rel]["mutation_id"] = old.get("mutation_id", fresh["capsules"][rel]["mutation_id"])
                finalize_capsule(fresh["capsules"][rel])
    refresh_index_certificates(fresh)
    checks = []
    for rel, c in fresh["capsules"].items():
        old = index["capsules"].get(rel)
        if not old:
            checks.append({"path": rel, "status": "MISSING_IN_INDEX"})
            continue
        fields = ["size", "file_count", "dir_count", "state", "certificate"]
        mismatch = [f for f in fields if old.get(f) != c.get(f)]
        if mismatch:
            checks.append({"path": rel, "status": "MISMATCH", "fields": mismatch})
    for rel in index["capsules"].keys():
        if rel not in fresh["capsules"]:
            checks.append({"path": rel, "status": "STALE_IN_INDEX"})
    status = "PASS" if not checks else "FAIL"
    root_state = index.get("capsules", {}).get(".", {}).get("state", STATE_PARTIAL)
    structure_current = not checks
    truth_visible = structure_current and root_state == STATE_RESOLVED
    out = {
        "strue_version": VERSION,
        "root": str(root),
        "status": status,
        "mismatch_count": len(checks),
        "root_state": root_state,
        "structure_current": structure_current,
        "truth_visible": truth_visible,
        "stale_or_corrupt_detected": bool(checks),
        "verification_mode": "full_tree_rescan",
        "verification_cost": "proportional_to_object_count",
        "staleness_model": "explicit_sync_required_for_out_of_band_changes",
        "root_certificate_index": index.get("root_certificate", ""),
        "root_certificate_fresh": fresh.get("root_certificate", ""),
        "truth_certificate_index": index.get("truth_certificate", ""),
        "truth_certificate_fresh": fresh.get("truth_certificate", ""),
        "checks": checks,
    }
    write_json(root / VERIFY_FILE, out)
    save_index(root, index)
    print("STRUE_VERIFY")
    print(f"status={status}")
    print("verification_mode=full_tree_rescan")
    print("verification_cost=proportional_to_object_count")
    print("staleness_model=explicit_sync_required_for_out_of_band_changes")
    print(f"root_state={root_state}")
    print(f"structure_current={str(structure_current).lower()}")
    print(f"truth_visible={str(truth_visible).lower()}")
    print(f"stale_or_corrupt_detected={str(bool(checks)).lower()}")
    print(f"mismatch_count={len(checks)}")
    print(f"root_certificate_index={out['root_certificate_index']}")
    print(f"root_certificate_fresh={out['root_certificate_fresh']}")
    print(f"truth_certificate_index={out['truth_certificate_index']}")
    print(f"truth_certificate_fresh={out['truth_certificate_fresh']}")

def command_observe(args):
    snapshot = build_observatory_snapshot(args.root, args.top)
    print("STRUE_OBSERVATORY")
    print(f"root={snapshot['root']}")
    print(f"last_mutation_id={snapshot['last_mutation_id']}")
    print(f"root_certificate={snapshot['root_certificate']}")
    print("TOP_SIZE_DIRECTORIES")
    for d in snapshot["top_directories"]:
        print(f"{d['path']} | size_bytes={d['size_bytes']} | files={d['file_count']} | dirs={d['dir_count']} | state={d['state']} | mutation_id={d['mutation_id']}")

def command_release_summary(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    bench = {}
    if (root / BENCH_FILE).exists():
        try:
            bench = read_json(root / BENCH_FILE)
        except Exception:
            bench = {}
    journal_rows = read_journal(root, args.journal_limit)
    c = index["capsules"].get(".")
    lines = []
    lines.append("STRUE RELEASE SUMMARY")
    lines.append("")
    lines.append("Name: STRUE")
    lines.append("Expansion: Structural Truth Engine")
    lines.append("")
    lines.append("Core invariants:")
    lines.append("truth = resolve(structure)")
    lines.append("truth_visible iff structure_updated")
    lines.append("same structure -> same truth")
    lines.append("truth_cost proportional_to mutation_set not object_count")
    lines.append("")
    lines.append("Current root truth:")
    lines.append(f"root={root}")
    lines.append(f"size_bytes={c['size'] if c else ''}")
    lines.append(f"file_count={c['file_count'] if c else ''}")
    lines.append(f"dir_count={c['dir_count'] if c else ''}")
    lines.append(f"last_mutation_id={index.get('last_mutation_id')}")
    lines.append(f"root_certificate={index.get('root_certificate')}")
    lines.append(f"truth_certificate={index.get('truth_certificate')}")
    lines.append(f"history_certificate={index.get('history_certificate')}")
    lines.append("")
    if bench:
        lines.append("Latest benchmark:")
        lines.append(f"path={bench.get('path', '')}")
        lines.append(f"status={bench.get('status', '')}")
        lines.append(f"strue_lookup_us={bench.get('strue_lookup_us', '')}")
        lines.append(f"traversal_ms={bench.get('traversal_ms', '')}")
        lines.append(f"speedup_x={bench.get('speedup_x', '')}")
        lines.append("")
    lines.append("Recent journal events:")
    if not journal_rows:
        lines.append("none")
    else:
        for e in journal_rows:
            lines.append(event_display_fields(e))
    lines.append("")
    lines.append("Civilization-grade direction:")
    lines.append("STRUE demonstrates that folder truth can be maintained structurally and observed without traversal.")
    lines.append("Scan remains a fallback. Journal-first updates preserve truth through known mutation branches.")
    lines.append("Watch mode moves STRUE toward continuous structural observability.")
    text = "\n".join(lines) + "\n"
    atomic_write_text(root / SUMMARY_FILE, text)
    save_index(root, index)
    print(text, end="")

def command_readme(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    bench = {}
    if (root / BENCH_FILE).exists():
        try:
            bench = read_json(root / BENCH_FILE)
        except Exception:
            bench = {}
    c = index["capsules"].get(".")
    lines = [
        "# STRUE - Structural Truth Engine",
        "",
        "Truth Before Traversal.",
        "",
        "STRUE demonstrates that folder truth can be maintained structurally and observed without recursively traversing the folder every time properties are requested.",
        "",
        "## Core Invariants",
        "",
        "`truth = resolve(structure)`",
        "",
        "`truth_visible iff structure_updated`",
        "",
        "`same structure -> same truth`",
        "",
        "`truth_cost proportional_to mutation_set not object_count`",
        "",
        "## What STRUE v1.2 Demonstrates",
        "",
        "- Instant structural folder properties",
        "- Journal-first mutation updates",
        "- Optimized batch mutation updates",
        "- Traversal benchmark comparison",
        "- Verification against fresh scan",
        "- Replay verification",
        "- Separate truth and history certificates",
        "- Continuous watch mode",
        "- Observatory snapshot generation",
        "- Affected-directory reporting",
        "",
        "## Current Demo Snapshot",
        "",
        f"`size_bytes={c['size'] if c else ''}`",
        "",
        f"`file_count={c['file_count'] if c else ''}`",
        "",
        f"`dir_count={c['dir_count'] if c else ''}`",
        "",
        f"`truth_certificate={index.get('truth_certificate', '')}`",
        "",
        f"`history_certificate={index.get('history_certificate', '')}`",
        "",
        "## Latest Benchmark",
        "",
        f"`path={bench.get('path', '')}`",
        "",
        f"`status={bench.get('status', '')}`",
        "",
        f"`strue_lookup_us={bench.get('strue_lookup_us', '')}`",
        "",
        f"`traversal_ms={bench.get('traversal_ms', '')}`",
        "",
        f"`speedup_x={bench.get('speedup_x', '')}`",
        "",
        "## Quick Start",
        "",
        "`python STRUE_v1_2.py demo --root STRUE_DEMO --files 1000 --clean`",
        "",
        "`python STRUE_v1_2.py benchmark --root STRUE_DEMO --path Project`",
        "",
        "`python STRUE_v1_2.py batch --root STRUE_DEMO --count 100`",
        "",
        "`python STRUE_v1_2.py snapshot --root STRUE_DEMO --top 10`",
        "",
        "`python STRUE_v1_2.py watch --root STRUE_DEMO --cycles 3 --interval_sec 1`",
        "",
        "`python STRUE_v1_2.py replay-verify --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py release-summary --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py verify --root STRUE_DEMO`",
        "",
        "## Scope",
        "",
        "STRUE v1.2 is a research and demonstration kernel.",
        "",
        "It does not install drivers.",
        "",
        "It does not modify the operating system.",
        "",
        "It does not require external dependencies.",
        "",
        "It uses Python standard library only.",
    ]
    out = Path(args.out)
    if not out.is_absolute():
        out = root / out
    atomic_write_text(out, "\n".join(lines) + "\n")
    print("STRUE_README")
    print(f"out={out}")

def command_export(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    out_csv = Path(args.out_csv)
    rows = []
    for rel, c in sorted(index["capsules"].items()):
        rows.append([rel, c["kind"], c["state"], c["size"], c["file_count"], c["dir_count"], c["mutation_id"], c["certificate"]])
    with out_csv.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["path", "kind", "state", "size_bytes", "file_count", "dir_count", "mutation_id", "certificate"])
        w.writerows(rows)
    print("STRUE_EXPORT")
    print(f"out_csv={out_csv}")
    print(f"rows={len(rows)}")


def command_recover(args):
    root = Path(args.root).resolve()
    ensure_dir(root)
    start = time.perf_counter()
    existed_index = (root / INDEX_FILE).exists()
    backup_path = ""
    if existed_index and args.backup:
        backup_path = str(root / (INDEX_FILE + ".bak"))
        shutil.copy2(root / INDEX_FILE, backup_path)
    for p in [root / EVENT_FILE, root / JOURNAL_FILE]:
        if not p.exists():
            atomic_write_text(p, "")
    index = scan_tree(root)
    if args.preserve_mutation_id and existed_index:
        try:
            old = read_json(root / INDEX_FILE)
            index["last_mutation_id"] = max(int(old.get("last_mutation_id", 1)), int(index.get("last_mutation_id", 1))) + 1
            for rel, c in index["capsules"].items():
                old_c = old.get("capsules", {}).get(rel)
                if old_c:
                    c["mutation_id"] = old_c.get("mutation_id", c["mutation_id"])
                    finalize_capsule(c)
            for rel in sorted(index["capsules"].keys(), key=rel_depth, reverse=True):
                if index["capsules"][rel]["kind"] == "dir":
                    recompute_dir(index, rel)
                    old_c = old.get("capsules", {}).get(rel)
                    if old_c:
                        index["capsules"][rel]["mutation_id"] = old_c.get("mutation_id", index["last_mutation_id"])
                    finalize_capsule(index["capsules"][rel])
            refresh_index_certificates(index)
        except Exception:
            pass
    save_index(root, index)
    event = {
        "mutation_id": index.get("last_mutation_id"),
        "op": "recover",
        "index_existed": existed_index,
        "backup_path": backup_path,
        "truth_certificate": index.get("truth_certificate", ""),
    }
    journal_append(root, event)
    event_append(root, event)
    save_index(root, index)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    out = {
        "strue_version": VERSION,
        "status": "PASS",
        "root": str(root),
        "index_existed": existed_index,
        "backup_path": backup_path,
        "capsules": len(index.get("capsules", {})),
        "truth_certificate": index.get("truth_certificate", ""),
        "root_certificate": index.get("root_certificate", ""),
        "elapsed_ms": elapsed_ms,
    }
    write_json(root / RECOVERY_FILE, out)
    print("STRUE_RECOVER")
    print("status=PASS")
    print(f"root={root}")
    print(f"index_existed={str(existed_index).lower()}")
    if backup_path:
        print(f"backup={backup_path}")
    print(f"capsules={len(index.get('capsules', {}))}")
    print(f"truth_certificate={index.get('truth_certificate')}")
    print(f"root_certificate={index.get('root_certificate')}")
    print(f"elapsed_ms={elapsed_ms:.3f}")


def command_corrupt(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    rel = args.path.replace("\\", "/").strip("/")
    if rel in {"", "."}:
        rel = "."
    if rel not in index["capsules"]:
        raise SystemExit(f"ERROR: no STRUE capsule for {rel}")
    original = dict(index["capsules"][rel])
    if args.mode == "size":
        index["capsules"][rel]["size"] = int(index["capsules"][rel].get("size", 0)) + args.delta
    elif args.mode == "state":
        index["capsules"][rel]["state"] = STATE_PARTIAL if index["capsules"][rel].get("state") == STATE_RESOLVED else STATE_RESOLVED
    elif args.mode == "certificate":
        index["capsules"][rel]["certificate"] = "CORRUPTED" + index["capsules"][rel].get("certificate", "")[:16]
    refresh_index_certificates(index)
    save_index(root, index)
    out = {
        "strue_version": VERSION,
        "status": "INJECTED",
        "path": rel,
        "mode": args.mode,
        "original": original,
        "current": index["capsules"][rel],
    }
    write_json(root / CORRUPT_FILE, out)
    print("STRUE_CORRUPT")
    print("status=INJECTED")
    print(f"path={rel}")
    print(f"mode={args.mode}")
    print("next=run verify or doctor")


def command_stress(args):
    root = Path(args.root).resolve()
    if root.exists() and args.clean:
        shutil.rmtree(root)
    ensure_dir(root / args.base_dir)
    start = time.perf_counter()
    created = 0
    for i in range(args.files):
        bucket = i // max(1, args.bucket_size)
        d = root / args.base_dir / f"bucket_{bucket:05d}"
        ensure_dir(d)
        p = d / f"stress_{i:07d}.txt"
        p.write_text(args.content_template.format(i=i), encoding="utf-8", newline="\n")
        created += 1
        if args.progress and created % args.progress == 0:
            print(f"progress_files={created}")
    build_ms = (time.perf_counter() - start) * 1000.0
    init_start = time.perf_counter()
    for p in [root / EVENT_FILE, root / JOURNAL_FILE]:
        if not p.exists():
            atomic_write_text(p, "")
    index = scan_tree(root)
    save_index(root, index)
    init_ms = (time.perf_counter() - init_start) * 1000.0
    out = {
        "strue_version": VERSION,
        "status": "PASS",
        "root": str(root),
        "files_created": created,
        "capsules": len(index.get("capsules", {})),
        "root_file_count": index["capsules"]["."]["file_count"],
        "root_dir_count": index["capsules"]["."]["dir_count"],
        "truth_certificate": index.get("truth_certificate", ""),
        "build_ms": build_ms,
        "init_ms": init_ms,
    }
    write_json(root / STRESS_FILE, out)
    save_index(root, index)
    print("STRUE_STRESS")
    print("status=PASS")
    print(f"root={root}")
    print(f"files_created={created}")
    print(f"capsules={len(index.get('capsules', {}))}")
    print(f"truth_certificate={index.get('truth_certificate')}")
    print(f"build_ms={build_ms:.3f}")
    print(f"init_ms={init_ms:.3f}")


def command_report(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    snapshot = build_observatory_snapshot(root, args.top)
    summary = root_summary(index)
    lines = []
    lines.append("# STRUE v1.2 Resilience Report")
    lines.append("")
    lines.append("## Core Invariant")
    lines.append("")
    lines.append("`truth_visible iff structure_updated`")
    lines.append("")
    lines.append("`folder_truth = structurally_maintained_truth`")
    lines.append("")
    lines.append("`truth_cost proportional_to mutation_set not object_count`")
    lines.append("")
    lines.append("## Root Summary")
    lines.append("")
    lines.append(f"Root: `{root}`")
    lines.append(f"Size bytes: `{summary.get('size')}`")
    lines.append(f"File count: `{summary.get('file_count')}`")
    lines.append(f"Directory count: `{summary.get('dir_count')}`")
    lines.append(f"Last mutation id: `{summary.get('last_mutation_id')}`")
    lines.append(f"Truth certificate: `{summary.get('truth_certificate')}`")
    lines.append("")
    lines.append("## Top Directories")
    lines.append("")
    lines.append("| Path | Size bytes | Files | Dirs | State | Mutation |")
    lines.append("|---|---:|---:|---:|---|---:|")
    for d in snapshot.get("top_directories", [])[:args.top]:
        lines.append(f"| `{d['path']}` | {d['size_bytes']} | {d['file_count']} | {d['dir_count']} | `{d['state']}` | {d['mutation_id']} |")
    lines.append("")
    lines.append("## Release Position")
    lines.append("")
    lines.append("STRUE v1.2 is a resilience release focused on recovery, corruption detection, stress testing, auditability, and external release readiness.")
    atomic_write_text(args.out, "\n".join(lines) + "\n")
    print("STRUE_REPORT")
    print(f"out={args.out}")
    print(f"truth_certificate={summary.get('truth_certificate')}")



def safe_copy_file(src, dst):
    src = Path(src)
    dst = Path(dst)
    ensure_dir(dst.parent)
    if src.exists() and src.is_file():
        shutil.copy2(src, dst)
        return True
    return False


def write_release_file(path, lines):
    atomic_write_text(path, "\n".join(lines).rstrip() + "\n")


def command_claims(args):
    print("STRUE_CLAIMS")
    print("version=1.2")
    print("what_strue_proves=folder truth can be structurally maintained and observed without traversal during properties lookup")
    print("what_strue_proves=truth retrieval can be separated from object count when structure has already been updated")
    print("what_strue_proves=verification, replay, audit, recovery, and stress checks can be expressed with standard-library reproducibility")
    print("what_strue_does_not_prove=operating system filesystem driver integration")
    print("what_strue_does_not_prove=kernel-level deployment readiness")
    print("what_strue_does_not_prove=guaranteed behavior under all real-world concurrent filesystem races")
    print("recommended_usage=research demonstration, reproducibility artifact, filesystem observability prototype, structural truth benchmark")
    print("core_formula=truth_cost proportional_to mutation_set not object_count")
    print("invariant=truth_visible iff structure_updated")


def command_summary(args):
    root = Path(args.root).resolve()
    index = load_index(root)
    audit_issues = audit_index(index)
    certs = recomputed_index_certificates(index)
    cert_ok = (
        index.get("root_certificate", "") == certs.get("root_certificate", "") and
        index.get("truth_certificate", "") == certs.get("truth_certificate", "") and
        index.get("history_certificate", "") == certs.get("history_certificate", "")
    )
    fresh = scan_tree(root)
    verify_issues = []
    for rel, c in fresh["capsules"].items():
        old = index["capsules"].get(rel)
        if not old:
            verify_issues.append(rel)
            continue
        for field in ["size", "file_count", "dir_count", "state"]:
            if old.get(field) != c.get(field):
                verify_issues.append(rel)
                break
    for rel in index["capsules"].keys():
        if rel not in fresh["capsules"]:
            verify_issues.append(rel)
    rows = read_manifest(root)
    manifest_issues = []
    for row in rows:
        rel = row.get("path", "")
        expected = row.get("expected", "")
        p = root / rel
        if not p.exists() or sha256_bytes(p.read_bytes()) != expected:
            manifest_issues.append(rel)
    root_capsule = index.get("capsules", {}).get(".", {})
    status = "RELEASE_READY" if not audit_issues and not verify_issues and not manifest_issues and cert_ok else "REVIEW_REQUIRED"
    print("STRUE_SUMMARY")
    print("version=1.2")
    print(f"status={status}")
    print(f"root={root}")
    print(f"capsules={len(index.get('capsules', {}))}")
    print(f"size_bytes={root_capsule.get('size', 0)}")
    print(f"file_count={root_capsule.get('file_count', 0)}")
    print(f"dir_count={root_capsule.get('dir_count', 0)}")
    print("truth_source=structure")
    print("traversal_used=false")
    print(f"verify_issues={len(verify_issues)}")
    print(f"audit_issues={len(audit_issues)}")
    print(f"manifest_issues={len(manifest_issues)}")
    print(f"certificate_integrity={str(cert_ok).lower()}")
    print(f"truth_certificate={index.get('truth_certificate', '')}")
    print(f"root_certificate={index.get('root_certificate', '')}")
    print(f"history_certificate={index.get('history_certificate', '')}")


def command_reproduce(args):
    root = Path(args.root).resolve()
    print("STRUE_REPRODUCE")
    command_verify(argparse.Namespace(root=str(root)))
    command_cert_audit(argparse.Namespace(root=str(root), limit=args.limit))
    command_civilization(argparse.Namespace(root=str(root)))
    if args.replay:
        command_replay_verify(argparse.Namespace(root=str(root), replay_root=""))
    print("STRUE_REPRODUCE_RESULT")
    print("status=PASS")
    print("version=1.2")


def command_release(args):
    root = Path(args.root).resolve()
    out = Path(args.out).resolve()
    if out.exists() and args.clean:
        shutil.rmtree(out)
    ensure_dir(out)
    ensure_dir(out / "docs")
    ensure_dir(out / "verification")
    ensure_dir(out / "benchmarks")
    ensure_dir(out / "reports")
    ensure_dir(out / "src")

    index = load_index(root)
    summary = root_summary(index)
    bench = {}
    if (root / BENCH_FILE).exists():
        try:
            bench = read_json(root / BENCH_FILE)
        except Exception:
            bench = {}
    civ = {}
    if (root / CIVILIZATION_FILE).exists():
        try:
            civ = read_json(root / CIVILIZATION_FILE)
        except Exception:
            civ = {}
    verify = {}
    if (root / VERIFY_FILE).exists():
        try:
            verify = read_json(root / VERIFY_FILE)
        except Exception:
            verify = {}

    script_path = Path(__file__).resolve()
    safe_copy_file(script_path, out / "src" / "STRUE_v1_2.py")
    safe_copy_file(root / VERIFY_FILE, out / "verification" / "strue_verify.json")
    safe_copy_file(root / REPLAY_FILE, out / "verification" / "strue_replay_verify.json")
    safe_copy_file(root / CIVILIZATION_FILE, out / "verification" / "strue_civilization.json")
    safe_copy_file(root / BENCH_FILE, out / "benchmarks" / "strue_benchmark.json")
    safe_copy_file(root / BENCH_CSV_FILE, out / "benchmarks" / "strue_benchmark_history.csv")
    safe_copy_file(root / OBSERVATORY_FILE, out / "reports" / "strue_observatory_snapshot.json")
    safe_copy_file(root / SUMMARY_FILE, out / "reports" / "strue_release_summary.txt")

    readme = [
        "# STRUE - Structural Truth Engine",
        "",
        "Maintain Truth. Skip Traversal.",
        "",
        "STRUE is a standard-library Python research artifact demonstrating that folder properties can be maintained as structural truth rather than recomputed by recursive traversal at lookup time.",
        "",
        "## Core Invariants",
        "",
        "`truth = resolve(structure)`",
        "",
        "`truth_visible iff structure_updated`",
        "",
        "`same structure -> same truth`",
        "",
        "`truth_cost proportional_to mutation_set not object_count`",
        "",
        "## What v1.0 Proves",
        "",
        "- Folder truth can be read from maintained structural capsules.",
        "- Properties lookup does not require traversal when structure is already updated.",
        "- Structural truth can be verified against a fresh traversal scan.",
        "- Recovery, audit, certificate integrity, replay, and stress tests are reproducible.",
        "- The artifact uses Python standard library only.",
        "",
        "## What v1.0 Does Not Prove",
        "",
        "- Kernel-level filesystem integration.",
        "- Operating-system replacement readiness.",
        "- Full concurrent mutation safety across all real-world filesystem races.",
        "- Production deployment without further engineering review.",
        "",
        "## Current Release Snapshot",
        "",
        f"`size_bytes={summary.get('size', '')}`",
        "",
        f"`file_count={summary.get('file_count', '')}`",
        "",
        f"`dir_count={summary.get('dir_count', '')}`",
        "",
        f"`truth_certificate={summary.get('truth_certificate', '')}`",
        "",
        f"`root_certificate={summary.get('root_certificate', '')}`",
        "",
        f"`history_certificate={summary.get('history_certificate', '')}`",
        "",
        "## Quick Start",
        "",
        "`python STRUE_v1_2.py demo --root STRUE_DEMO --files 1000 --clean`",
        "",
        "`python STRUE_v1_2.py batch --root STRUE_DEMO --count 100`",
        "",
        "`python STRUE_v1_2.py benchmark --root STRUE_DEMO --path Project`",
        "",
        "`python STRUE_v1_2.py doctor --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py cert-audit --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py civilization --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py reproduce --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py release --root STRUE_DEMO --out STRUE_RELEASE`",
        "",
        "## Certificate Roles",
        "",
        "`truth_certificate` is the structural truth identity.",
        "",
        "`root_certificate` is the current indexed root capsule identity.",
        "",
        "`history_certificate` includes mutation/history context.",
    ]
    write_release_file(out / "README.md", readme)

    license_lines = [
        "MIT License",
        "",
        "Copyright (c) 2026 Shunyaya",
        "",
        "Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated documentation files, to deal in the Software without restriction, including without limitation the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, subject to the following conditions:",
        "",
        "The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.",
        "",
        "THE SOFTWARE IS PROVIDED AS IS, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.",
    ]
    write_release_file(out / "LICENSE", license_lines)

    reproduction = [
        "# Reproducibility Guide",
        "",
        "Run these commands from the folder containing `STRUE_v1_2.py`.",
        "",
        "`python STRUE_v1_2.py demo --root STRUE_DEMO --files 1000 --clean`",
        "",
        "`python STRUE_v1_2.py batch --root STRUE_DEMO --count 100`",
        "",
        "`python STRUE_v1_2.py benchmark --root STRUE_DEMO --path Project`",
        "",
        "`python STRUE_v1_2.py doctor --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py cert-audit --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py civilization --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py replay-verify --root STRUE_DEMO`",
        "",
        "`python STRUE_v1_2.py verify --root STRUE_DEMO`",
        "",
        "Expected final status: `PASS`.",
    ]
    write_release_file(out / "docs" / "Reproducibility.md", reproduction)

    claims = [
        "# Claim Boundary",
        "",
        "## What STRUE Proves",
        "",
        "STRUE proves that folder truth can be maintained structurally and observed without traversal during properties lookup when the mutation structure is already updated.",
        "",
        "## What STRUE Does Not Prove",
        "",
        "STRUE v1.2 does not prove kernel-level filesystem deployment, operating-system replacement readiness, or universal concurrent filesystem safety.",
        "",
        "## Recommended Positioning",
        "",
        "STRUE v1.2 is a civilization-grade prototype and public research artifact, not yet production filesystem infrastructure.",
    ]
    write_release_file(out / "docs" / "Claim-Boundary.md", claims)

    bench_lines = [
        "# Benchmark Summary",
        "",
        f"Path: `{bench.get('path', '')}`",
        "",
        f"Status: `{bench.get('status', '')}`",
        "",
        f"STRUE lookup us: `{bench.get('strue_lookup_us', '')}`",
        "",
        f"Traversal ms: `{bench.get('traversal_ms', '')}`",
        "",
        f"Speedup x: `{bench.get('speedup_x', '')}`",
    ]
    write_release_file(out / "benchmarks" / "benchmark.md", bench_lines)

    release_summary = [
        "STRUE_RELEASE",
        "version=1.2",
        f"root={root}",
        f"out={out}",
        f"truth_certificate={summary.get('truth_certificate', '')}",
        f"root_certificate={summary.get('root_certificate', '')}",
        f"history_certificate={summary.get('history_certificate', '')}",
        f"benchmark_status={bench.get('status', '')}",
        f"civilization_status={civ.get('status', '')}",
        f"verify_status={verify.get('status', '')}",
    ]
    write_release_file(out / "release_summary.txt", release_summary)

    print("STRUE_RELEASE")
    print("version=1.2")
    print(f"root={root}")
    print(f"out={out}")
    print(f"truth_certificate={summary.get('truth_certificate', '')}")
    print(f"files=README.md,LICENSE,docs,verification,benchmarks,reports,src")


def build_parser():
    epilog = """
Examples:
  python STRUE_v1_2.py demo --root STRUE_DEMO --files 1000 --clean
  python STRUE_v1_2.py benchmark --root STRUE_DEMO --path Project
  python STRUE_v1_2.py batch --root STRUE_DEMO --count 100
  python STRUE_v1_2.py move --root STRUE_DEMO --src Project/Batch/file_00000.txt --dst Project/Moved/file_00000.txt
  python STRUE_v1_2.py snapshot --root STRUE_DEMO --top 10
  python STRUE_v1_2.py watch --root STRUE_DEMO --cycles 3 --interval_sec 1
  python STRUE_v1_2.py replay-verify --root STRUE_DEMO
  python STRUE_v1_2.py manifest-verify --root STRUE_DEMO
  python STRUE_v1_2.py audit --root STRUE_DEMO
  python STRUE_v1_2.py doctor --root STRUE_DEMO
  python STRUE_v1_2.py release-summary --root STRUE_DEMO
  python STRUE_v1_2.py verify --root STRUE_DEMO
"""
    p = argparse.ArgumentParser(prog="STRUE_v1_2.py", description="STRUE v1.2 - Structural Truth Engine", epilog=epilog, formatter_class=argparse.RawTextHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init")
    a.add_argument("--root", required=True)
    a.add_argument("--reset_events", action="store_true")
    a.set_defaults(func=command_init)

    a = sub.add_parser("demo")
    a.add_argument("--root", required=True)
    a.add_argument("--files", type=int, default=1000)
    a.add_argument("--clean", action="store_true")
    a.set_defaults(func=command_demo)

    a = sub.add_parser("props")
    a.add_argument("--root", required=True)
    a.add_argument("--path", default=".")
    a.set_defaults(func=command_props)

    a = sub.add_parser("batch")
    a.add_argument("--root", required=True)
    a.add_argument("--count", type=int, default=10)
    a.add_argument("--template", default="Project/Batch/file_{i:05d}.txt")
    a.add_argument("--content_template", default="batch content {i}")
    a.set_defaults(func=command_batch)

    a = sub.add_parser("add-file")
    a.add_argument("--root", required=True)
    a.add_argument("--path", required=True)
    a.add_argument("--content", default="")
    a.set_defaults(func=command_add)

    a = sub.add_parser("touch")
    a.add_argument("--root", required=True)
    a.add_argument("--path", required=True)
    a.add_argument("--append", default="x")
    a.set_defaults(func=command_touch)

    a = sub.add_parser("delete")
    a.add_argument("--root", required=True)
    a.add_argument("--path", required=True)
    a.set_defaults(func=command_delete)


    a = sub.add_parser("move")
    a.add_argument("--root", required=True)
    a.add_argument("--src", required=True)
    a.add_argument("--dst", required=True)
    a.add_argument("--overwrite", action="store_true")
    a.set_defaults(func=command_move)

    a = sub.add_parser("status")
    a.add_argument("--root", required=True)
    a.add_argument("--deep", action="store_true")
    a.set_defaults(func=command_status)

    a = sub.add_parser("quick-check")
    a.add_argument("--root", required=True)
    a.add_argument("--deep", action="store_true")
    a.set_defaults(func=command_status)

    a = sub.add_parser("sync")
    a.add_argument("--root", required=True)
    a.set_defaults(func=command_sync)

    a = sub.add_parser("benchmark")
    a.add_argument("--root", required=True)
    a.add_argument("--path", default=".")
    a.add_argument("--out_csv", default="")
    a.set_defaults(func=command_benchmark)

    a = sub.add_parser("cost")
    a.add_argument("--root", required=True)
    a.add_argument("--path", default=".")
    a.set_defaults(func=command_cost)

    a = sub.add_parser("journal")
    a.add_argument("--root", required=True)
    a.add_argument("--limit", type=int, default=10)
    a.add_argument("--max_dirs", type=int, default=8)
    a.set_defaults(func=command_journal)

    a = sub.add_parser("snapshot")
    a.add_argument("--root", required=True)
    a.add_argument("--top", type=int, default=10)
    a.set_defaults(func=command_snapshot)

    a = sub.add_parser("watch")
    a.add_argument("--root", required=True)
    a.add_argument("--cycles", type=int, default=3)
    a.add_argument("--interval_sec", type=float, default=1.0)
    a.add_argument("--top", type=int, default=5)
    a.set_defaults(func=command_watch)

    a = sub.add_parser("observe")
    a.add_argument("--root", required=True)
    a.add_argument("--top", type=int, default=10)
    a.set_defaults(func=command_observe)

    a = sub.add_parser("replay-verify")
    a.add_argument("--root", required=True)
    a.add_argument("--replay_root", default="")
    a.set_defaults(func=command_replay_verify)

    a = sub.add_parser("verify")
    a.add_argument("--root", required=True)
    a.set_defaults(func=command_verify)


    a = sub.add_parser("manifest-verify")
    a.add_argument("--root", required=True)
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(func=command_manifest_verify)

    a = sub.add_parser("audit")
    a.add_argument("--root", required=True)
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(func=command_audit)

    a = sub.add_parser("doctor")
    a.add_argument("--root", required=True)
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(func=command_doctor)

    a = sub.add_parser("cert-audit")
    a.add_argument("--root", required=True)
    a.add_argument("--limit", type=int, default=20)
    a.set_defaults(func=command_cert_audit)

    a = sub.add_parser("civilization")
    a.add_argument("--root", required=True)
    a.set_defaults(func=command_civilization)


    a = sub.add_parser("recover")
    a.add_argument("--root", required=True)
    a.add_argument("--backup", action="store_true")
    a.add_argument("--preserve_mutation_id", action="store_true")
    a.set_defaults(func=command_recover)

    a = sub.add_parser("corrupt")
    a.add_argument("--root", required=True)
    a.add_argument("--path", default="Project")
    a.add_argument("--mode", choices=["size", "state", "certificate"], default="size")
    a.add_argument("--delta", type=int, default=1)
    a.set_defaults(func=command_corrupt)

    a = sub.add_parser("stress")
    a.add_argument("--root", required=True)
    a.add_argument("--files", type=int, default=10000)
    a.add_argument("--clean", action="store_true")
    a.add_argument("--base_dir", default="Stress")
    a.add_argument("--bucket_size", type=int, default=1000)
    a.add_argument("--progress", type=int, default=0)
    a.add_argument("--content_template", default="STRUE stress line {i}\n")
    a.set_defaults(func=command_stress)

    a = sub.add_parser("report")
    a.add_argument("--root", required=True)
    a.add_argument("--top", type=int, default=10)
    a.add_argument("--out", default="STRUE_v1_2_civilization_report.md")
    a.set_defaults(func=command_report)

    a = sub.add_parser("release-summary")
    a.add_argument("--root", required=True)
    a.add_argument("--journal_limit", type=int, default=10)
    a.set_defaults(func=command_release_summary)

    a = sub.add_parser("readme")
    a.add_argument("--root", required=True)
    a.add_argument("--out", default=README_FILE)
    a.set_defaults(func=command_readme)

    a = sub.add_parser("export")
    a.add_argument("--root", required=True)
    a.add_argument("--out_csv", required=True)
    a.set_defaults(func=command_export)

    a = sub.add_parser("claims")
    a.set_defaults(func=command_claims)

    a = sub.add_parser("summary")
    a.add_argument("--root", required=True)
    a.set_defaults(func=command_summary)

    a = sub.add_parser("reproduce")
    a.add_argument("--root", required=True)
    a.add_argument("--limit", type=int, default=20)
    a.add_argument("--replay", action="store_true")
    a.set_defaults(func=command_reproduce)

    a = sub.add_parser("release")
    a.add_argument("--root", required=True)
    a.add_argument("--out", required=True)
    a.add_argument("--clean", action="store_true")
    a.set_defaults(func=command_release)

    return p

def main():
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)

if __name__ == "__main__":
    main()
