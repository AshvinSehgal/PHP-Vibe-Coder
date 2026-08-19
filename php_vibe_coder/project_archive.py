from io import BytesIO
from pathlib import Path, PurePosixPath
from zipfile import ZipFile

MAX_FILES = 1000
MAX_UNCOMPRESSED_SIZE = 30 * 1024 * 1024
IGNORED_PARTS = {".git", ".env", "vendor", "node_modules", "logs", "cache", "session"}

def extract_project_zip(archive_bytes, destination):
    destination = Path(destination).resolve()
    with ZipFile(BytesIO(archive_bytes)) as archive:
        members = [member for member in archive.infolist() if not member.is_dir()]
        if len(members) > MAX_FILES:
            raise ValueError(f"The ZIP contains more than {MAX_FILES} files.")
        total_size = sum(member.file_size for member in members)
        if total_size > MAX_UNCOMPRESSED_SIZE:
            raise ValueError("The extracted ZIP would be larger than 30 MB.")
        prefix = project_prefix(members)
        extracted = []
        for member in members:
            if is_symbolic_link(member):
                continue
            relative = normalized_member_path(member.filename, prefix)
            if relative is None or any(part in IGNORED_PARTS for part in relative.parts):
                continue
            target = (destination / Path(*relative.parts)).resolve()
            if destination not in target.parents:
                raise ValueError(f"Unsafe ZIP path: {member.filename}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
            extracted.append(relative.as_posix())
    if not extracted:
        raise ValueError("The ZIP did not contain any usable project files.")
    if not any(filename == "spark" or filename.startswith("app/") for filename in extracted):
        raise ValueError("The ZIP does not look like a CodeIgniter project because it has no app folder or spark file.")
    return extracted

def project_prefix(members):
    paths = [PurePosixPath(member.filename.replace("\\", "/")) for member in members]
    first_parts = {path.parts[0] for path in paths if path.parts}
    if len(first_parts) != 1:
        return None
    first = next(iter(first_parts))
    has_project_marker = any(
        len(path.parts) > 1 and path.parts[0] == first and path.parts[1] in ("app", "public", "spark")
        for path in paths
    )
    return first if has_project_marker else None

def normalized_member_path(filename, prefix):
    path = PurePosixPath(filename.replace("\\", "/"))
    parts = list(path.parts)
    if prefix and parts and parts[0] == prefix:
        parts = parts[1:]
    if not parts or any(part in ("", ".", "..") for part in parts):
        return None
    if path.is_absolute():
        raise ValueError(f"Unsafe ZIP path: {filename}")
    return PurePosixPath(*parts)

def is_symbolic_link(member):
    return (member.external_attr >> 16) & 0o170000 == 0o120000
