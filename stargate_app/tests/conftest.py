"""Make the app folder importable (server, stargate_rm) whichever directory pytest runs from."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
