"""Only reviewed overlay assets enter the portable immutable inventory."""
from pathlib import Path


def test_overlay_builder_excludes_transient_bytecode_and_local_installation(tmp_path):
    from scripts.build_geti_runtime import copy_overlay
    source = tmp_path/'source'; source.mkdir()
    for name, content in {'server.py':'# reviewed entry\n','network_guard.py':'# reviewed policy\n',
        'nested/rules.py':'# reviewed helper\n','__pycache__/server.cpython-314.pyc':'relocated cache',
        'nested/__pycache__/rules.cpython-313.pyc':'local cache','orphan.pyc':'bytecode',
        'orphan.pyo':'bytecode','installed-runtime.json':'local installation descriptor'}.items():
        file=source/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(content)
    output = tmp_path/'output'
    copy_overlay(source,output)
    assert {p.relative_to(output).as_posix() for p in output.rglob('*') if p.is_file()} == {'server.py','network_guard.py','nested/rules.py'}
    assert (source/'installed-runtime.json').read_text()=='local installation descriptor'
    assert (source/'__pycache__/server.cpython-314.pyc').read_text()=='relocated cache'
