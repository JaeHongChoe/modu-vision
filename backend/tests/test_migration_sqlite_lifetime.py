"""Read-only inventories release SQLite handles before Windows temp cleanup."""
from pathlib import Path
import sqlite3
import pytest
from backend.engine import migration_inventory


@pytest.mark.parametrize('refuse', [False, True])
def test_sqlite_inventory_closes_real_connections_before_return(tmp_path, monkeypatch, refuse):
    source=tmp_path/'original.sqlite3'
    connection=sqlite3.connect(source)
    connection.execute('CREATE TABLE fixture(id TEXT PRIMARY KEY)')
    connection.execute("INSERT INTO fixture VALUES('preserved')")
    connection.commit();connection.close()
    before=source.read_bytes()
    opened=[]
    original=sqlite3.connect
    class TrackedConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if refuse and sql.lower().startswith('select count(*)'):
                raise sqlite3.OperationalError('controlled inventory read failure')
            return super().execute(sql,*args,**kwargs)
    def connect(*args, **kwargs):
        # Retain handles as a debugger/traceback may; garbage collection must
        # not determine whether Windows can remove the temporary snapshots.
        result=original(*args,**kwargs,factory=TrackedConnection)
        opened.append(result)
        return result
    monkeypatch.setattr(sqlite3,'connect',connect)
    if refuse:
        with pytest.raises(ValueError,match='unreadable'):
            migration_inventory.inventory(tmp_path)
    else:
        result=migration_inventory.inventory(tmp_path)
        assert result['inventory']['files'][0]['tables']['fixture']['count']==1
    assert source.read_bytes()==before
    assert opened
    for handle in opened:
        with pytest.raises(sqlite3.ProgrammingError,match='closed'):
            handle.execute('SELECT 1')
