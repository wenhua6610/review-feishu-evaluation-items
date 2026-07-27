#!/usr/bin/env python3
"""Safely extract ZIP and TAR-family archives with bounded resource use."""

from __future__ import annotations

import argparse
import os
import stat
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath


class UnsafeArchiveError(RuntimeError):
    pass


def safe_target(root: Path, name: str) -> Path:
    normalized = name.replace("\\", "/")
    pure = PurePosixPath(normalized)
    if pure.is_absolute() or ".." in pure.parts:
        raise UnsafeArchiveError(f"unsafe member path: {name!r}")
    target = root.joinpath(*pure.parts)
    root_resolved = root.resolve()
    target_resolved = target.resolve(strict=False)
    if os.path.commonpath([str(root_resolved), str(target_resolved)]) != str(root_resolved):
        raise UnsafeArchiveError(f"path escapes output directory: {name!r}")
    return target


def ensure_new_output(out_dir: Path) -> None:
    if out_dir.exists() and any(out_dir.iterdir()):
        raise UnsafeArchiveError(f"output directory is not empty: {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)


def extract_zip(
    archive: Path,
    out_dir: Path,
    max_entries: int,
    max_total: int,
    max_file: int,
    max_ratio: float,
) -> int:
    total = 0
    with zipfile.ZipFile(archive) as zf:
        infos = zf.infolist()
        if len(infos) > max_entries:
            raise UnsafeArchiveError(f"too many archive members: {len(infos)}")
        for info in infos:
            mode = (info.external_attr >> 16) & 0xFFFF
            if stat.S_ISLNK(mode):
                raise UnsafeArchiveError(f"symbolic link is not allowed: {info.filename!r}")
            if info.flag_bits & 0x1:
                raise UnsafeArchiveError(f"encrypted member requires manual handling: {info.filename!r}")
            if info.file_size > max_file:
                raise UnsafeArchiveError(f"member exceeds size limit: {info.filename!r}")
            ratio = info.file_size / max(info.compress_size, 1)
            if ratio > max_ratio and info.file_size > 1024 * 1024:
                raise UnsafeArchiveError(f"suspicious compression ratio: {info.filename!r}")
            total += info.file_size
            if total > max_total:
                raise UnsafeArchiveError("archive exceeds total extracted-size limit")
            target = safe_target(out_dir, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if target.exists():
                raise UnsafeArchiveError(f"refusing to overwrite existing file: {target}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("xb") as dst:
                while chunk := src.read(1024 * 1024):
                    dst.write(chunk)
    return total


def extract_tar(
    archive: Path,
    out_dir: Path,
    max_entries: int,
    max_total: int,
    max_file: int,
) -> int:
    total = 0
    with tarfile.open(archive, mode="r:*") as tf:
        members = tf.getmembers()
        if len(members) > max_entries:
            raise UnsafeArchiveError(f"too many archive members: {len(members)}")
        for member in members:
            if member.issym() or member.islnk() or member.isdev():
                raise UnsafeArchiveError(f"links and device files are not allowed: {member.name!r}")
            if member.size > max_file:
                raise UnsafeArchiveError(f"member exceeds size limit: {member.name!r}")
            total += member.size
            if total > max_total:
                raise UnsafeArchiveError("archive exceeds total extracted-size limit")
            target = safe_target(out_dir, member.name)
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                continue
            if target.exists():
                raise UnsafeArchiveError(f"refusing to overwrite existing file: {target}")
            source = tf.extractfile(member)
            if source is None:
                raise UnsafeArchiveError(f"cannot read archive member: {member.name!r}")
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("xb") as dst:
                while chunk := source.read(1024 * 1024):
                    dst.write(chunk)
    return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", type=Path)
    parser.add_argument("output_dir", type=Path)
    parser.add_argument("--max-entries", type=int, default=2000)
    parser.add_argument("--max-total-bytes", type=int, default=1024**3)
    parser.add_argument("--max-file-bytes", type=int, default=256 * 1024**2)
    parser.add_argument("--max-ratio", type=float, default=200.0)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if not args.archive.is_file():
            raise UnsafeArchiveError(f"archive not found: {args.archive}")
        ensure_new_output(args.output_dir)
        if zipfile.is_zipfile(args.archive):
            total = extract_zip(
                args.archive,
                args.output_dir,
                args.max_entries,
                args.max_total_bytes,
                args.max_file_bytes,
                args.max_ratio,
            )
        elif tarfile.is_tarfile(args.archive):
            total = extract_tar(
                args.archive,
                args.output_dir,
                args.max_entries,
                args.max_total_bytes,
                args.max_file_bytes,
            )
        else:
            raise UnsafeArchiveError("unsupported archive type; use a trusted static extractor manually")
    except (UnsafeArchiveError, zipfile.BadZipFile, tarfile.TarError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(f"OK: extracted {total} bytes to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

