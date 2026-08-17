import sys
from pathlib import Path


BLENDJOB_DIRECTORY = Path(__file__).resolve().parent.parent
if str(BLENDJOB_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(BLENDJOB_DIRECTORY))

from environment import main


if __name__ == "__main__":
    main()
