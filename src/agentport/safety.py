import os
import re
import tempfile
from pathlib import Path

from .errors import FormatError, SafetyError

MAX_TEXT_BYTES = 1024 * 1024
MAX_JSON_BYTES = 5 * 1024 * 1024
MAX_SKILL_FILE_BYTES = 512 * 1024
MAX_SKILL_TOTAL_BYTES = 2 * 1024 * 1024
MAX_SKILL_FILES = 200

SECRET_KEY_RE = re.compile(
    r"(token|secret|password|passwd|pwd|api[-_]?key|access[-_]?key|auth|credential|private[-_]?key|session)",
    re.IGNORECASE,
)

UNSAFE_SKILL_EXT = {
    ".exe", ".bat", ".cmd", ".ps1", ".sh", ".bash", ".zsh", ".fish",
    ".dll", ".so", ".dylib", ".com", ".scr", ".msi", ".jar", ".vbs", ".wsf",
}


def _check_path_text(target):
    s = str(target)
    if "\x00" in s:
        raise SafetyError("path contains a NUL byte")
    parts = Path(s).parts
    for idx, part in enumerate(parts):
        if idx == 0 and len(part) >= 2 and part[1] == ":" and part[0].isalpha():
            continue
        if ":" in part:
            raise SafetyError(
                f"path component contains ':' (NTFS alternate data stream?): {part!r}",
                hint="stream-style paths are not supported",
            )
        if "\x00" in part:
            raise SafetyError("path contains a NUL byte")


def ensure_within(root, target):
    root_r = Path(root).resolve()
    t = Path(target)
    _check_path_text(t if t.is_absolute() else t)
    if not t.is_absolute():
        t = root_r / t
    try:
        t_r = t.resolve()
    except OSError as exc:
        raise SafetyError(f"cannot resolve path {t}", hint=str(exc))
    except ValueError as exc:
        raise SafetyError(f"invalid path: {target}", hint=str(exc))
    if t_r != root_r and root_r not in t_r.parents:
        raise SafetyError(
            f"path escapes working root: {target}",
            hint=f"resolved to {t_r}, which is outside {root_r}",
        )
    return t_r


def read_text_capped(path, what="file", max_bytes=MAX_TEXT_BYTES):
    p = Path(path)
    if "\x00" in str(p):
        raise SafetyError("path contains a NUL byte")
    if not p.exists():
        raise FormatError(f"{what} not found: {path}")
    size = p.stat().st_size
    if size > max_bytes:
        raise SafetyError(
            f"{what} too large: {path} ({size} bytes > {max_bytes})",
            hint="refusing to process oversized files",
        )
    try:
        with open(p, "rb") as fh:
            data = fh.read(max_bytes + 1)
    except OSError as exc:
        raise SafetyError(f"cannot read {what}: {path}", hint=str(exc))
    if len(data) > max_bytes:
        raise SafetyError(f"{what} too large: {path}")
    if b"\x00" in data:
        raise FormatError(f"{what} looks binary (NUL byte found): {path}")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FormatError(f"{what} is not valid UTF-8: {path}", hint=str(exc))
    return text.lstrip("\ufeff")


def atomic_write_text(path, text):
    p = Path(path)
    _check_path_text(p if p.is_absolute() else p)
    if p.is_symlink():
        raise SafetyError(
            f"refusing to write through symlink: {p}",
            hint="remove the symlink or point --out elsewhere",
        )
    if p.exists() and p.is_dir():
        raise SafetyError(
            f"output path is an existing directory: {p}",
            hint="pass a file path via --out",
        )
    parent = p.parent
    parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(parent), prefix=".agentport-", suffix=".tmp"
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(str(tmp_path), str(p))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
    return p


def normalize_newlines(text):
    return text.replace("\r\n", "\n").replace("\r", "\n")


def ensure_trailing_newline(text):
    return text if text.endswith("\n") else text + "\n"


def is_secret_key(key):
    return bool(SECRET_KEY_RE.search(str(key)))


def mask_mapping(mapping):
    out = {}
    for k, v in mapping.items():
        if isinstance(v, str) and v and is_secret_key(k):
            out[k] = "***"
        else:
            out[k] = v
    return out


def mask_tree(obj, key=None):
    if isinstance(obj, dict):
        return {k: mask_tree(v, key=k) for k, v in obj.items()}
    if isinstance(obj, list):
        return [mask_tree(x, key=key) for x in obj]
    if isinstance(obj, str) and obj and key is not None and is_secret_key(key):
        return "***"
    return obj


def safe_rel_path(rel):
    from pathlib import PurePosixPath

    s = str(rel).replace("\\", "/")
    pp = PurePosixPath(s)
    if pp.is_absolute() or s.startswith("/"):
        raise SafetyError(f"absolute path not allowed inside skill bundle: {rel}")
    parts = pp.parts
    if not parts:
        raise SafetyError("empty path in skill bundle")
    for part in parts:
        if part in ("..", "."):
            raise SafetyError(f"path traversal not allowed in skill bundle: {rel}")
        if ":" in part:
            raise SafetyError(f"drive-like component not allowed: {rel}")
    return pp


def collect_bundle_files(src_dir, warnings):
    src = Path(src_dir)
    collected = []
    total = 0
    count = 0
    for dirpath, dirnames, filenames in os.walk(src):
        dirnames[:] = sorted(
            d for d in dirnames if d != ".git" and not d.startswith(".")
        )
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full = Path(dirpath) / name
            if full.is_symlink():
                warnings.append(f"WARN skipped symlink: {full.relative_to(src)}")
                continue
            rel = full.relative_to(src)
            safe_rel_path(rel.as_posix())
            ext = full.suffix.lower()
            if ext in UNSAFE_SKILL_EXT:
                warnings.append(
                    f"WARN skipped executable file for security: {rel.as_posix()}"
                )
                continue
            size = full.stat().st_size
            if size > MAX_SKILL_FILE_BYTES:
                warnings.append(
                    f"WARN skipped oversized file ({size} bytes): {rel.as_posix()}"
                )
                continue
            count += 1
            if count > MAX_SKILL_FILES:
                raise SafetyError(
                    f"skill bundle exceeds {MAX_SKILL_FILES} files",
                    hint="split the skill or remove unneeded files",
                )
            total += size
            if total > MAX_SKILL_TOTAL_BYTES:
                raise SafetyError(
                    f"skill bundle exceeds {MAX_SKILL_TOTAL_BYTES} bytes total",
                    hint="split the skill or remove unneeded files",
                )
            collected.append((rel.as_posix(), full))
    return collected


def copy_bundle_file(src_full, dest_root, rel_posix):
    dest_root = Path(dest_root)
    pp = safe_rel_path(rel_posix)
    dest = ensure_within(dest_root, dest_root.joinpath(*pp.parts))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(src_full, "rb") as fh:
        data = fh.read(MAX_SKILL_FILE_BYTES + 1)
    if len(data) > MAX_SKILL_FILE_BYTES:
        raise SafetyError(f"file grew beyond cap during copy: {rel_posix}")
    fd, tmp_name = tempfile.mkstemp(dir=str(dest.parent), prefix=".agentport-", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if dest.exists() and dest.is_symlink():
            raise SafetyError(f"destination is a symlink: {dest}")
        os.replace(str(tmp_path), str(dest))
    except Exception:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
