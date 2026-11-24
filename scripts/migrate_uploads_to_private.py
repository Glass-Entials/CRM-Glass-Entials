"""
scripts/migrate_uploads_to_private.py
Safe, non-destructive migration utility to copy uploaded files from
the legacy public web directory (static/uploads/) to the secure private
storage directory (var/uploads/).

Guarantees:
- Copies files without deleting or modifying any existing production files.
- Preserves directory structures and file timestamps.
- Idempotent: skips files that have already been copied.
- Validates file sizes after copy.
"""
import os
import shutil
import sys
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("upload_migration")

def migrate_uploads(source_dir: str, target_dir: str, dry_run: bool = False) -> dict:
    source_dir = os.path.abspath(source_dir)
    target_dir = os.path.abspath(target_dir)

    logger.info(f"Source (Legacy): {source_dir}")
    logger.info(f"Target (Private): {target_dir}")
    logger.info(f"Dry Run: {dry_run}")

    if not os.path.exists(source_dir):
        logger.warning(f"Source directory does not exist: {source_dir}. Nothing to migrate.")
        return {"total_found": 0, "copied": 0, "skipped": 0, "errors": 0}

    stats = {"total_found": 0, "copied": 0, "skipped": 0, "errors": 0}

    for root, dirs, files in os.walk(source_dir):
        for filename in files:
            stats["total_found"] += 1
            src_file = os.path.join(root, filename)
            rel_path = os.path.relpath(src_file, source_dir)
            dst_file = os.path.join(target_dir, rel_path)

            try:
                if os.path.exists(dst_file):
                    if os.path.getsize(src_file) == os.path.getsize(dst_file):
                        logger.debug(f"Skipping already migrated: {rel_path}")
                        stats["skipped"] += 1
                        continue

                dst_dir = os.path.dirname(dst_file)
                if not dry_run:
                    os.makedirs(dst_dir, exist_ok=True)
                    shutil.copy2(src_file, dst_file)
                    # Verify copy
                    if os.path.getsize(src_file) != os.path.getsize(dst_file):
                        raise IOError(f"File size mismatch after copying {rel_path}")

                logger.info(f"Copied: {rel_path}")
                stats["copied"] += 1
            except Exception as e:
                logger.error(f"Error copying {src_file} -> {dst_file}: {e}")
                stats["errors"] += 1

    logger.info(f"Migration completed. Summary: {stats}")
    return stats


if __name__ == "__main__":
    basedir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    legacy_uploads = os.path.join(basedir, "static", "uploads")
    private_uploads = os.path.join(basedir, "var", "uploads")

    dry_run = "--dry-run" in sys.argv
    migrate_uploads(legacy_uploads, private_uploads, dry_run=dry_run)
