import sys
from pathlib import Path


PACKAGE_ROOT = str(Path(__file__).resolve().parents[2])
inserted_package_root = PACKAGE_ROOT not in sys.path
if inserted_package_root:
    sys.path.insert(0, PACKAGE_ROOT)

try:
    from blendjob.runner import main
finally:
    if inserted_package_root:
        sys.path.remove(PACKAGE_ROOT)


if __name__ == "__main__":
    main()
