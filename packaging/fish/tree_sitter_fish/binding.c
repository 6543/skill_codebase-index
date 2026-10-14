#include <Python.h>

typedef struct TSLanguage TSLanguage;

/* Symbol exported by the generated src/parser.c of the pinned grammar. */
TSLanguage *tree_sitter_fish(void);

static PyObject *_binding_language(PyObject *Py_UNUSED(self), PyObject *Py_UNUSED(args)) {
    /* py-tree-sitter >= 0.22 takes the language as a named capsule. */
    return PyCapsule_New(tree_sitter_fish(), "tree_sitter.Language", NULL);
}

static PyMethodDef methods[] = {
    {"language", _binding_language, METH_NOARGS,
     "Get the tree-sitter language for this grammar."},
    {NULL, NULL, 0, NULL},
};

static struct PyModuleDef_Slot slots[] = {
#ifdef Py_GIL_DISABLED
    {Py_mod_gil, Py_MOD_GIL_NOT_USED},
#endif
    {0, NULL},
};

static struct PyModuleDef module = {
    .m_base = PyModuleDef_HEAD_INIT,
    .m_name = "_binding",
    .m_doc = NULL,
    .m_size = 0,
    .m_methods = methods,
    .m_slots = slots,
};

PyMODINIT_FUNC PyInit__binding(void) {
    return PyModuleDef_Init(&module);
}
