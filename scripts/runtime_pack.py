"""CLI facade for the shared runtime pack inventory and inert installer."""
from pathlib import Path
import sys

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.engine.runtime_pack import (DESCRIPTOR_FIELDS, MANIFEST_FIELDS, canonical_bytes,
    inventory_pack, verify_pack, install_pack, main)

if __name__ == '__main__':
    main()
