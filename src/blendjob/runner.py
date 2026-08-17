import argparse
import hashlib
import importlib.util
import sys
import threading

from .entrypoint import split_entrypoint


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--entrypoint", required=True)
    parser.add_argument("--storage-root", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    return parser.parse_args()


def load_server(entrypoint):
    path, attribute = split_entrypoint(entrypoint)
    path = path.resolve()
    module_name = "_blendjob_entrypoint_" + hashlib.sha256(
        str(path).encode("utf-8")
    ).hexdigest()
    search_locations = [str(path.parent)] if path.name == "__init__.py" else None
    spec = importlib.util.spec_from_file_location(
        module_name,
        path,
        submodule_search_locations=search_locations,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load Server entrypoint: {entrypoint}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(module_name, None)
        raise
    server = getattr(module, attribute, None)
    if server is None:
        raise RuntimeError(
            f"Entrypoint does not export {attribute!r}: {entrypoint}"
        )
    return server


def main():
    args = parse_args()
    server = load_server(args.entrypoint)
    server.bind(args.storage_root)

    import uvicorn
    from blendjob.server import watch_parent

    app = server.create_app(args.instance_id)
    config = uvicorn.Config(
        app,
        host=args.host,
        port=args.port,
        access_log=False,
        log_level="warning",
    )
    http_server = uvicorn.Server(config)

    def stop_http_server():
        server.shutdown_event.wait()
        http_server.should_exit = True

    threading.Thread(target=stop_http_server, daemon=True).start()
    threading.Thread(
        target=watch_parent,
        args=(args.parent_pid, server.shutdown_event),
        name="BlendJobParentMonitor",
        daemon=True,
    ).start()
    http_server.run()


if __name__ == "__main__":
    main()
