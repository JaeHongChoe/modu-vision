/* Optional CPython POSIX ancestor accelerator. No filesystem state is cached. */
#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <errno.h>
#include <string.h>
#ifndef _WIN32
#include <sys/stat.h>
#include <unistd.h>
#endif

typedef struct {
    PyObject *os_module;
    PyObject *stat_module;
    PyObject *original_stat;
    PyObject *original_islink;
    /* Fixed lexical lookup names only; these retain no filesystem attributes. */
    PyObject *os_key;
    PyObject *stat_key;
    PyObject *islink_key;
} GuardState;

static GuardState *state_of(PyObject *module) {
    return (GuardState *)PyModule_GetState(module);
}

/* No module __getattr__ probes: a missing/changed attribute falls back once. */
static int pristine(GuardState *state, PyObject *os_module, PyObject *stat_module,
                    PyObject *runtime_namespace) {
    if (Py_TYPE(os_module) != &PyModule_Type || Py_TYPE(stat_module) != &PyModule_Type
            || os_module != state->os_module || stat_module != state->stat_module
            || !PyDict_CheckExact(runtime_namespace)
            || PyDict_GetItem(runtime_namespace, state->stat_key) != stat_module
            || PyDict_GetItem(runtime_namespace, state->os_key) != os_module)
        return 0;
    PyObject *os_dict = PyModule_GetDict(os_module);
    PyObject *stat_dict = PyModule_GetDict(stat_module);
    return PyDict_GetItem(stat_dict, state->islink_key) == state->original_islink
        && PyDict_GetItem(os_dict, state->stat_key) == state->original_stat;
}

/* Only the exact Unicode grammar used by the existing native_parent branch. */
static int normalized_absolute(PyObject *path) {
    if (!PyUnicode_CheckExact(path)) return 0;
    Py_ssize_t n = PyUnicode_GET_LENGTH(path);
    int kind = PyUnicode_KIND(path);
    const void *data = PyUnicode_DATA(path);
    if (n == 0 || PyUnicode_READ(kind, data, 0) != '/') return 0;
    if (n >= 3 && PyUnicode_READ(kind, data, 1) == '/'
               && PyUnicode_READ(kind, data, 2) == '/') return 0;
    if (n > 2 && PyUnicode_READ(kind, data, n - 1) == '/') return 0;
    for (Py_ssize_t i = 2; i + 1 < n; ++i)
        if (PyUnicode_READ(kind, data, i) == '/' && PyUnicode_READ(kind, data, i + 1) == '/')
            return 0;
    return 1;
}

/* Match the selective Python except block with the actual original error. */
static int classify_error(PyObject *handler) {
    PyObject *type = NULL, *error = NULL, *traceback = NULL;
    PyErr_Fetch(&type, &error, &traceback);
    PyErr_NormalizeException(&type, &error, &traceback);
    if (PyErr_Occurred() || error == NULL) {
        Py_XDECREF(type); Py_XDECREF(error); Py_XDECREF(traceback);
        return -1;
    }
    if (traceback != NULL && PyException_SetTraceback(error, traceback) < 0) {
        Py_DECREF(type); Py_DECREF(error); Py_DECREF(traceback);
        return -1;
    }
    PyObject *accepted = PyObject_CallOneArg(handler, error);
    Py_XDECREF(type); Py_DECREF(error); Py_XDECREF(traceback);
    if (accepted == NULL) return -1;
    Py_DECREF(accepted);
    return 0;
}

static PyObject *check_unlinked(PyObject *module, PyObject *args) {
    PyObject *current, *os_module, *stat_module, *runtime_namespace;
    PyObject *reject_handler, *error_handler;
    if (!PyArg_ParseTuple(args, "OOOOOO:check_unlinked", &current, &os_module,
                         &stat_module, &runtime_namespace, &reject_handler,
                         &error_handler)) return NULL;
    GuardState *state = state_of(module);
    if (state == NULL) return NULL;
    if (!normalized_absolute(current) || state->original_stat == NULL)
        return Py_NewRef(current);
    if (!PyCallable_Check(reject_handler) || !PyCallable_Check(error_handler)) {
        PyErr_SetString(PyExc_TypeError, "Invalid native path guard callbacks");
        return NULL;
    }
    PyObject *cursor = Py_NewRef(current);
    /* Invocation-owned lexical bytes only. No filesystem result is retained. */
    char *ascii_buffer = NULL;
    int ascii_attempt = PyUnicode_IS_ASCII(current);
    for (;;) {
        /* A callable changed while a previous syscall released the GIL:
           return the next unprobed prefix to the unchanged Python loop. */
        if (!pristine(state, os_module, stat_module, runtime_namespace)) {
            PyMem_Free(ascii_buffer);
            return cursor;
        }
#ifndef _WIN32
        PyObject *encoded = ascii_buffer == NULL ? PyUnicode_EncodeFSDefault(cursor) : NULL;
        int linked = 0;
        if (ascii_buffer == NULL && encoded == NULL) {
            ascii_attempt = 0;
            if (!PyErr_ExceptionMatches(PyExc_ValueError)) goto failed;
            if (classify_error(error_handler) < 0) goto failed;
        } else if (ascii_buffer == NULL && memchr(PyBytes_AS_STRING(encoded), 0,
                          (size_t)PyBytes_GET_SIZE(encoded)) != NULL) {
            ascii_attempt = 0;
            /* CPython os.stat's path converter rejects an embedded NUL.
               The existing ValueError handler ignores that prefix only. */
            Py_DECREF(encoded);
            PyErr_SetString(PyExc_ValueError, "embedded null byte");
            if (classify_error(error_handler) < 0) goto failed;
        } else {
            if (ascii_buffer == NULL && ascii_attempt) {
                /* Verify the actual first filesystem encoding is the same
                   ASCII byte sequence before reusing any lexical prefix. */
                Py_ssize_t length = PyUnicode_GET_LENGTH(cursor);
                ascii_attempt = 0;
                if (PyBytes_GET_SIZE(encoded) == length
                        && memcmp(PyBytes_AS_STRING(encoded), PyUnicode_1BYTE_DATA(cursor),
                                  (size_t)length) == 0) {
                    ascii_buffer = PyMem_Malloc((size_t)length + 1);
                    if (ascii_buffer != NULL)
                        memcpy(ascii_buffer, PyBytes_AS_STRING(encoded), (size_t)length + 1);
                    /* Optional buffer allocation failure retains the original
                       current-prefix encoding and the unchanged slow loop. */
                }
            }
            const char *filename = ascii_buffer != NULL ? ascii_buffer : PyBytes_AS_STRING(encoded);
            struct stat observed;
            int result, saved_errno;
            do {
                Py_BEGIN_ALLOW_THREADS
                result = lstat(filename, &observed);
                saved_errno = errno;
                Py_END_ALLOW_THREADS
            } while (result < 0 && saved_errno == EINTR && !PyErr_CheckSignals());
            Py_XDECREF(encoded);
            if (result < 0) {
                if (PyErr_Occurred()) goto failed;
                errno = saved_errno;
                PyErr_SetFromErrnoWithFilenameObject(PyExc_OSError, cursor);
                if (classify_error(error_handler) < 0) goto failed;
            } else {
                /* Same predicate captured before the syscall; no stat_result
                   allocation, no inode/proof reuse, and no skipped ancestor. */
                linked = S_ISLNK(observed.st_mode);
            }
        }
        if (linked) {
            /* The original expression resolves UpdateError after its probe.
               This callback reads the live runtime namespace at that moment. */
            PyObject *rejected = PyObject_CallNoArgs(reject_handler);
            if (rejected != NULL) {
                Py_DECREF(rejected);
                PyErr_SetString(PyExc_SystemError, "Native path rejection callback returned");
            }
            goto failed;
        }
        if (PyErr_CheckSignals() < 0) goto failed;
        Py_ssize_t n = PyUnicode_GET_LENGTH(cursor);
        Py_ssize_t slash = PyUnicode_FindChar(cursor, '/', 0, n, -1);
        if (slash < 0) goto failed;
        Py_ssize_t parent_length = slash > 1 ? slash : slash + 1;
        if (parent_length == n) {
            PyMem_Free(ascii_buffer);
            Py_DECREF(cursor);
            Py_RETURN_NONE;
        }
        PyObject *parent = PyUnicode_Substring(cursor, 0, parent_length);
        if (parent == NULL) goto failed;
        if (ascii_buffer != NULL) ascii_buffer[parent_length] = '\0';
        Py_DECREF(cursor);
        cursor = parent;
#else
        PyMem_Free(ascii_buffer);
        return cursor;
#endif
    }
failed:
    PyMem_Free(ascii_buffer);
    Py_DECREF(cursor);
    return NULL;
}

/* A Python/mock replacement present before import also disables acceleration. */
static int builtin_named(PyObject *callable, const char *module, const char *name) {
    if (!PyCFunction_Check(callable)) return 0;
    PyObject *actual_module = PyObject_GetAttrString(callable, "__module__");
    PyObject *actual_name = PyObject_GetAttrString(callable, "__name__");
    if (actual_module == NULL || actual_name == NULL) {
        Py_XDECREF(actual_module); Py_XDECREF(actual_name);
        PyErr_Clear(); return 0;
    }
    int result = PyUnicode_CheckExact(actual_module) && PyUnicode_CheckExact(actual_name)
        && PyUnicode_CompareWithASCIIString(actual_module, module) == 0
        && PyUnicode_CompareWithASCIIString(actual_name, name) == 0;
    Py_DECREF(actual_module); Py_DECREF(actual_name);
    return result;
}

static int guard_exec(PyObject *module) {
#ifdef _WIN32
    PyErr_SetString(PyExc_ImportError, "Native path guard is POSIX only");
    return -1;
#else
    GuardState *state = state_of(module);
    state->os_key = PyUnicode_InternFromString("os");
    state->stat_key = PyUnicode_InternFromString("stat");
    state->islink_key = PyUnicode_InternFromString("S_ISLNK");
    if (state->os_key == NULL || state->stat_key == NULL || state->islink_key == NULL)
        return -1;
    state->os_module = PyImport_ImportModule("os");
    state->stat_module = PyImport_ImportModule("stat");
    PyObject *posix = PyImport_ImportModule("posix");
    PyObject *native_stat = PyImport_ImportModule("_stat");
    if (state->os_module == NULL || state->stat_module == NULL
            || posix == NULL || native_stat == NULL) {
        Py_XDECREF(posix); Py_XDECREF(native_stat); return -1;
    }
    /* A proxy or ModuleType subclass has Python attribute dispatch which
       dictionary-only reads cannot preserve. Disable the native path. */
    if (Py_TYPE(state->os_module) != &PyModule_Type
            || Py_TYPE(state->stat_module) != &PyModule_Type
            || Py_TYPE(posix) != &PyModule_Type
            || Py_TYPE(native_stat) != &PyModule_Type) {
        Py_DECREF(posix); Py_DECREF(native_stat); return 0;
    }
    PyObject *stat_fn = PyDict_GetItemString(PyModule_GetDict(posix), "stat");
    PyObject *islink_fn = PyDict_GetItemString(PyModule_GetDict(native_stat), "S_ISLNK");
    if (stat_fn != NULL && islink_fn != NULL
            && builtin_named(stat_fn, "posix", "stat")
            && builtin_named(islink_fn, "_stat", "S_ISLNK")) {
        state->original_stat = Py_NewRef(stat_fn);
        state->original_islink = Py_NewRef(islink_fn);
    }
    Py_DECREF(posix); Py_DECREF(native_stat);
    return 0;
#endif
}

static int guard_traverse(PyObject *module, visitproc visit, void *arg) {
    GuardState *state = state_of(module);
    Py_VISIT(state->os_module); Py_VISIT(state->stat_module);
    Py_VISIT(state->original_stat); Py_VISIT(state->original_islink);
    Py_VISIT(state->os_key); Py_VISIT(state->stat_key); Py_VISIT(state->islink_key);
    return 0;
}

static int guard_clear(PyObject *module) {
    GuardState *state = state_of(module);
    Py_CLEAR(state->os_module); Py_CLEAR(state->stat_module);
    Py_CLEAR(state->original_stat); Py_CLEAR(state->original_islink);
    Py_CLEAR(state->os_key); Py_CLEAR(state->stat_key); Py_CLEAR(state->islink_key);
    return 0;
}

static PyMethodDef guard_methods[] = {
    {"check_unlinked", check_unlinked, METH_VARARGS,
     "Fresh leaf-to-root lstat probes; None complete, str remaining Python prefix."},
    {NULL, NULL, 0, NULL}
};
static PyModuleDef_Slot guard_slots[] = {
    {Py_mod_exec, guard_exec}, {0, NULL}
};
static struct PyModuleDef guard_definition = {
    PyModuleDef_HEAD_INIT, "_runtime_path_guard", NULL, sizeof(GuardState),
    guard_methods, guard_slots, guard_traverse, guard_clear, NULL
};
PyMODINIT_FUNC PyInit__runtime_path_guard(void) {
    return PyModuleDef_Init(&guard_definition);
}
