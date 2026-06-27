"""Put the code/ root on sys.path so tests can `import src...`."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
