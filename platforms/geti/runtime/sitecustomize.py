"""Apply the owned egress policy before upstream/worker module imports."""
import os
if os.environ.get('MODU_GETI_SECRET'):
    from network_guard import install
    install()
