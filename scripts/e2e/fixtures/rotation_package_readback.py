"""Run an actual app export from a fresh package-only Python process."""
import base64
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image

request = json.loads(sys.argv[1])
package = Path(request['package_dir']).resolve()
source = Path(request['source']).resolve()
output = package.parent / f'{package.name}-offline-aligned.png'
assert not output.exists()
run = subprocess.run([sys.executable, str(package / 'infer.py'), '--image', str(source), '--output', str(output)],
    cwd=package, env={**os.environ, 'PYTHONPATH': ''}, capture_output=True, text=True, timeout=30)
assert run.returncode == 0, run.stderr
result = json.loads(run.stdout)
reference = request['prediction']
assert abs(result['correction_deg'] - reference['correction_deg']) < .0001
np.testing.assert_allclose(result['transform'], reference['transform'], atol=.000001)
np.testing.assert_allclose(result['inverse_transform'], reference['inverse_transform'], atol=.000001)
assert result['alignment_recipe'] == reference['alignment_recipe']
np.testing.assert_array_equal(np.asarray(Image.open(output)), np.asarray(Image.open(BytesIO(base64.b64decode(reference['aligned_image_base64'])))))
print(json.dumps({'result': result, 'output': str(output), 'fresh_package_process': True, 'pixel_parity': True}))
