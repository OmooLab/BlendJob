from pathlib import Path


def split_entrypoint(entrypoint):
    if not isinstance(entrypoint, str):
        raise TypeError("server entrypoint must be a string")
    path_value, separator, attribute = entrypoint.rpartition(":")
    if not separator or not path_value or not attribute.isidentifier():
        raise ValueError(
            "server entrypoint must use '<python-file>:<attribute>'"
        )
    return Path(path_value).expanduser(), attribute


def normalized_entrypoint(entrypoint, root=None):
    path, attribute = split_entrypoint(entrypoint)
    if not path.is_absolute():
        if root is None:
            raise ValueError(
                "relative server entrypoint requires entrypoint_root"
            )
        path = Path(root).expanduser().resolve() / path
    if path.is_dir():
        path /= "__init__.py"
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"Server entrypoint file does not exist: {path}")
    return f"{path}:{attribute}"
