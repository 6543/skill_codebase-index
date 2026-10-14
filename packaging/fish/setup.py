"""Build the compiled binding: wrapper + generated parser + external scanner."""

from setuptools import Extension, setup
from setuptools.command.bdist_wheel import bdist_wheel


class Abi3Wheel(bdist_wheel):
    """Tag the wheel cp310-abi3: the extension uses only the limited API."""

    def get_tag(self):
        python, abi, platform = super().get_tag()
        if python.startswith("cp"):
            python, abi = "cp310", "abi3"
        return python, abi, platform


setup(
    ext_modules=[
        Extension(
            name="tree_sitter_fish._binding",
            sources=[
                "tree_sitter_fish/binding.c",
                "src/parser.c",
                "src/scanner.c",
            ],
            include_dirs=["src"],
            extra_compile_args=["-std=c11", "-fvisibility=hidden"],
            define_macros=[
                ("Py_LIMITED_API", "0x030A0000"),
                ("PY_SSIZE_T_CLEAN", None),
                ("TREE_SITTER_HIDE_SYMBOLS", None),
            ],
            py_limited_api=True,
        )
    ],
    cmdclass={"bdist_wheel": Abi3Wheel},
)
