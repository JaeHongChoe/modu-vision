#include "vision_runtime.h"
#include <Python.h>
#include <cstdlib>
#include <cstring>
#include <mutex>
#include <string>

#ifndef MV_PYTHON_EXECUTABLE
#define MV_PYTHON_EXECUTABLE "python3"
#endif

namespace {
std::mutex initialization;
struct Handle { PyObject* executor; PyObject* execute; std::mutex mutex; };
char* copy(const char* value) {
    if (!value) value="Unknown native runtime error";
    auto size=std::strlen(value)+1;
    auto result=static_cast<char*>(std::malloc(size));
    if (result) std::memcpy(result,value,size);
    return result;
}
char* error_text() {
    PyObject *type=nullptr,*value=nullptr,*trace=nullptr;
    PyErr_Fetch(&type,&value,&trace);
    PyObject* text=value?PyObject_Str(value):nullptr;
    char* result=copy(text?PyUnicode_AsUTF8(text):"Native Python execution failed");
    Py_XDECREF(text);Py_XDECREF(type);Py_XDECREF(value);Py_XDECREF(trace);
    return result;
}
bool initialize(char** error) {
    std::lock_guard<std::mutex> guard(initialization);
    if (Py_IsInitialized()) return true;
    PyConfig config;PyConfig_InitPythonConfig(&config);
    config.parse_argv=0;config.use_environment=0;config.user_site_directory=0;
    auto status=PyConfig_SetBytesString(&config,&config.program_name,MV_PYTHON_EXECUTABLE);
    if (!PyStatus_Exception(status)) status=PyConfig_SetBytesString(&config,&config.executable,MV_PYTHON_EXECUTABLE);
    if (!PyStatus_Exception(status)) status=Py_InitializeFromConfig(&config);
    if (PyStatus_Exception(status)) { if(error)*error=copy(status.err_msg);PyConfig_Clear(&config);return false; }
    PyConfig_Clear(&config);
    PyEval_SaveThread();
    return true;
}
bool verify_package(const char* package,char** error) {
    // Use only the interpreter's standard library before any package import.
    PyObject* scope=PyDict_New();
    PyObject* path=PyUnicode_FromString(package);
    if(!scope||!path){Py_XDECREF(scope);Py_XDECREF(path);if(error)*error=error_text();return false;}
    PyDict_SetItemString(scope,"__builtins__",PyEval_GetBuiltins());
    PyDict_SetItemString(scope,"_mv_package",path);Py_DECREF(path);
    const char* verification=R"PY(
import pathlib,json,hashlib
root=pathlib.Path(_mv_package).absolute()
if any(p.is_symlink() for p in (root,*root.parents)):
    raise ValueError('Native package path is linked')
root=root.resolve(strict=True)
manifest_path=root/'manifest.json'
if manifest_path.is_symlink():raise ValueError('Native manifest path is linked')
manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
seen=set()
for row in manifest['files']:
    relative=pathlib.PurePosixPath(row['path'])
    if relative.is_absolute() or '..' in relative.parts or '\\' in row['path'] or row['path'] in seen:
        raise ValueError('Native package checksum path is invalid')
    target=root/relative
    if any(p.is_symlink() for p in (target,*target.parents)) or not target.is_file() or not target.resolve().is_relative_to(root):
        raise ValueError('Native package checksum path is invalid')
    if ('size' in row and target.stat().st_size!=row['size']) or hashlib.sha256(target.read_bytes()).hexdigest()!=row['sha256']:
        raise ValueError('Native package checksum mismatch: '+row['path'])
    seen.add(row['path'])
if 'backend/engine/native_runtime_bridge.py' not in seen:
    raise ValueError('Native package checksum manifest has no runtime bridge')
if any(p.relative_to(root).as_posix() not in seen for p in (root/'backend').rglob('*.py')):
    raise ValueError('Native package contains unlisted runtime code')
)PY";
    PyObject* result=PyRun_String(verification,Py_file_input,scope,scope);
    Py_DECREF(scope);
    if(!result){if(error)*error=error_text();return false;}
    Py_DECREF(result);return true;
}
int call(Handle* handle,PyObject* input,char** output) {
    if(!input){*output=error_text();return 1;}
    PyObject* result=PyObject_CallFunctionObjArgs(handle->execute,handle->executor,input,nullptr);
    Py_DECREF(input);
    if(!result){*output=error_text();return 1;}
    *output=copy(PyUnicode_AsUTF8(result));
    Py_DECREF(result);
    // The Python bridge serializes the complete graph result with this status.
    return *output&&std::strstr(*output,"\"status\": \"timeout\"")?2:0;
}
}

extern "C" MV_API void* mv_create(const char* package_dir,const char* options,char** error) {
    if(error)*error=nullptr;
    if(!package_dir||!initialize(error))return nullptr;
    auto gil=PyGILState_Ensure();
    if(!verify_package(package_dir,error)){PyGILState_Release(gil);return nullptr;}
    PyObject* path=PyUnicode_FromString(package_dir);
    if(!path||PyList_Insert(PySys_GetObject("path"),0,path)<0){Py_XDECREF(path);if(error)*error=error_text();PyGILState_Release(gil);return nullptr;}
    Py_DECREF(path);
    PyObject* module=PyImport_ImportModule("backend.engine.native_runtime_bridge");
    PyObject* create=module?PyObject_GetAttrString(module,"native_create"):nullptr;
    PyObject* execute=module?PyObject_GetAttrString(module,"native_execute"):nullptr;
    PyObject* executor=create?PyObject_CallFunction(create,"ss",package_dir,options?options:"{}"):nullptr;
    Py_XDECREF(create);Py_XDECREF(module);
    if(!executor||!execute){Py_XDECREF(executor);Py_XDECREF(execute);if(error)*error=error_text();PyGILState_Release(gil);return nullptr;}
    auto handle=new Handle{executor,execute,{}};
    PyGILState_Release(gil);
    return handle;
}
extern "C" MV_API int mv_execute(void* opaque,const char* input,char** output) {
    if(!output)return 1;*output=nullptr;
    if(!opaque||!input){*output=copy("Missing runtime handle or input JSON");return 1;}
    auto handle=static_cast<Handle*>(opaque);std::lock_guard<std::mutex> guard(handle->mutex);
    auto gil=PyGILState_Ensure();int status=call(handle,PyUnicode_FromString(input),output);PyGILState_Release(gil);return status;
}
extern "C" MV_API int mv_predict(void* opaque,const char* image,const char* image_id,char** output) {
    if(!output)return 1;*output=nullptr;
    if(!opaque||!image){*output=copy("Missing runtime handle or image path");return 1;}
    auto handle=static_cast<Handle*>(opaque);std::lock_guard<std::mutex> guard(handle->mutex);
    auto gil=PyGILState_Ensure();
    PyObject* request=Py_BuildValue("{s:s}","image_path",image);
    if(image_id&&request){PyObject* id=PyUnicode_FromString(image_id);if(id){PyDict_SetItemString(request,"image_id",id);Py_DECREF(id);}}
    PyObject* module=PyImport_ImportModule("json");
    PyObject* json=module&&request?PyObject_CallMethod(module,"dumps","O",request):nullptr;
    Py_XDECREF(request);Py_XDECREF(module);
    int status=call(handle,json,output);PyGILState_Release(gil);return status;
}
extern "C" MV_API void mv_release(void* opaque) {
    if(!opaque)return;
    auto handle=static_cast<Handle*>(opaque);auto gil=PyGILState_Ensure();
    Py_DECREF(handle->executor);Py_DECREF(handle->execute);PyGILState_Release(gil);delete handle;
    // Never finalize a Python runtime that may still be used by another host/library.
}
extern "C" MV_API void mv_free(char* value){std::free(value);}
