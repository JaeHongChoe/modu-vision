"""Build the C ABI with this interpreter's Python headers and shared library."""
import argparse
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import sysconfig

parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();root=Path(__file__).resolve().parent;args.output.mkdir(parents=True,exist_ok=True)
if platform.system()=='Windows':
    subprocess.run(['cmake','-S',str(root),'-B',str(args.output),f'-DPython3_EXECUTABLE={sys.executable}'],check=True)
    subprocess.run(['cmake','--build',str(args.output),'--config','Release'],check=True)
    # Visual Studio is a multi-config generator. Keep the public command paths
    # identical to POSIX builds and the packaged C#/C++ execution checks.
    artifacts=('modu_vision_runtime.dll','modu_vision_runtime.lib','vision_predict.exe','vision_execute.exe')
    release=args.output/'Release'
    built=release if release.is_dir() else args.output
    for name in artifacts:
        if not (built/name).is_file():
            raise FileNotFoundError('Native build did not produce '+name)
    if built!=args.output:
        for name in artifacts:shutil.copyfile(built/name,args.output/name)
else:
    compiler=shutil.which('clang++') or shutil.which('g++')
    if not compiler: raise SystemExit('Install a C++17 compiler and matching Python development headers')
    libdir=sysconfig.get_config_var('LIBDIR')
    library='python'+sysconfig.get_config_var('VERSION')
    suffix='.dylib' if platform.system()=='Darwin' else '.so'
    output=args.output/('libmodu_vision_runtime'+suffix)
    common=[compiler,'-std=c++17','-O2','-I'+str(root)]
    subprocess.run(common+['-shared','-fPIC','-I'+sysconfig.get_paths()['include'],
        '-DMV_PYTHON_EXECUTABLE='+json.dumps(sys.executable),str(root/'vision_runtime.cpp'),
        '-L'+libdir,'-l'+library,'-Wl,-rpath,'+libdir,'-o',str(output)],check=True)
    for source,name in [('predict.cpp','vision_predict'),('execute.cpp','vision_execute')]:
        subprocess.run(common+[str(root/source),'-L'+str(args.output),'-lmodu_vision_runtime',
            '-Wl,-rpath,'+str(args.output),'-o',str(args.output/name)],check=True)
print(json.dumps({'status':'built','python':sys.executable,'output':str(args.output)}))
