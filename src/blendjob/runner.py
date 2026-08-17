import argparse
import importlib
import sys
import threading
from pathlib import Path


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
    path = Path(entrypoint).resolve()
    if path.name == "__init__.py":
        module_root = path.parent.parent
        module_name = path.parent.name
    else:
        module_root = path.parent
        module_name = path.stem
    addon_root = Path(__file__).resolve().parent.parent
    for directory in (module_root, addon_root):
        if str(directory) not in sys.path:
            sys.path.insert(0, str(directory))
    module = importlib.import_module(module_name)
    server = getattr(module, "server", None)
    if server is None:
        raise RuntimeError(f"Entrypoint does not export 'server': {entrypoint}")
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
