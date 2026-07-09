#!/usr/bin/env python3
"""
hermes memory backup — export all MemoryManager layers.

Usage:
  python3 hermes_backup.py [--output BACKUP_DIR]
  python3 hermes_backup.py --restore BACKUP_DIR
"""
import json, os, shutil, sqlite3, sys, time
from pathlib import Path

STORE = os.path.expanduser("~/.openclaw/memory-store")


def backup(output_dir: str):
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    print(f"📦 Backup → {out}")

    # L3 archive
    src = Path(STORE) / "l3_archive"
    dst = out / "l3_archive"
    if src.exists():
        shutil.copytree(src, dst, dirs_exist_ok=True)
        count = sum(1 for _ in dst.glob("*.jsonl"))
        print(f"  ✅ L3: {count} session files")

    # SQLite dumps
    for db_name in ["metadata.db", "mem0.db"]:
        src_db = Path(STORE) / db_name
        if src_db.exists():
            dst_sql = out / f"{db_name}.sql"
            # Use sqlite3 backup API for safe copy
            src_conn = sqlite3.connect(str(src_db))
            dst_conn = sqlite3.connect(str(out / f"{db_name}.backup"))
            src_conn.backup(dst_conn)
            dst_conn.close()
            # Also create text dump
            dump = "\n".join(src_conn.iterdump())
            with open(dst_sql, "w") as f:
                f.write(dump)
            src_conn.close()
            size = os.path.getsize(str(out / f"{db_name}.backup"))
            print(f"  ✅ {db_name}: {size:,} bytes (backup + SQL dump)")

    # FAISS index
    faiss_src = Path(STORE) / "faiss"
    if faiss_src.exists():
        faiss_dst = out / "faiss"
        shutil.copytree(faiss_src, faiss_dst, dirs_exist_ok=True)
        print(f"  ✅ FAISS: index preserved")

    # Cognitive state
    cog_src = Path(STORE) / "cognitive" / "planning_state.json"
    if cog_src.exists():
        cog_dst = out / "cognitive"
        cog_dst.mkdir(exist_ok=True)
        shutil.copy2(cog_src, cog_dst / "planning_state.json")
        print(f"  ✅ Cognitive: planning state preserved")

    # Manifest
    manifest = {
        "backup_time": time.time(),
        "backup_date": time.strftime("%Y-%m-%d %H:%M:%S"),
        "source": STORE,
        "layers": ["l3_archive", "metadata.db", "mem0.db", "faiss", "cognitive"],
    }
    with open(out / "backup_manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    total = sum(
        os.path.getsize(os.path.join(dp, fn))
        for dp, _, files in os.walk(out) for fn in files
    )
    print(f"\n  📦 Total backup size: {total:,} bytes ({total/1024:.1f} KB)")
    return out


def restore(backup_dir: str):
    src = Path(backup_dir)
    if not src.exists():
        print(f"❌ Backup not found: {src}")
        return

    manifest_path = src / "backup_manifest.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            manifest = json.load(f)
        print(f"📥 Restore from: {manifest.get('backup_date', 'unknown')}")

    # L3 archive
    l3_src = src / "l3_archive"
    if l3_src.exists():
        l3_dst = Path(STORE) / "l3_archive"
        shutil.copytree(l3_src, l3_dst, dirs_exist_ok=True)
        print(f"  ✅ L3: restored")

    # SQLite
    for db_name in ["metadata.db", "mem0.db"]:
        backup_file = src / f"{db_name}.backup"
        if backup_file.exists():
            shutil.copy2(backup_file, Path(STORE) / db_name)
            print(f"  ✅ {db_name}: restored")

    # FAISS
    faiss_src = src / "faiss"
    if faiss_src.exists():
        shutil.copytree(faiss_src, Path(STORE) / "faiss", dirs_exist_ok=True)
        print(f"  ✅ FAISS: restored")

    # Cognitive
    cog_src = src / "cognitive" / "planning_state.json"
    if cog_src.exists():
        cog_dst = Path(STORE) / "cognitive"
        cog_dst.mkdir(exist_ok=True)
        shutil.copy2(cog_src, cog_dst / "planning_state.json")
        print(f"  ✅ Cognitive: restored")

    print(f"\n✅ Restore complete")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--output", default=f"/tmp/hermes-backup-{int(time.time())}")
    p.add_argument("--restore")
    args = p.parse_args()

    if args.restore:
        restore(args.restore)
    else:
        backup(args.output)
