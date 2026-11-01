import os
import sys


def configure_tk_paths():
    if not getattr(sys, "frozen", False):
        return

    base_dir = getattr(sys, "_MEIPASS", "")
    if not base_dir:
        return

    tcl_library = os.path.join(base_dir, "tcl", "tcl8.6")
    tk_library = os.path.join(base_dir, "tcl", "tk8.6")

    if os.path.isdir(tcl_library):
        os.environ["TCL_LIBRARY"] = tcl_library
    if os.path.isdir(tk_library):
        os.environ["TK_LIBRARY"] = tk_library


configure_tk_paths()
