def pre_find_module_path(hook_api):
    # Override PyInstaller's broken tkinter exclusion on this machine.
    # The interpreter itself imports tkinter successfully, so we keep the
    # normal search path instead of forcing search_dirs = [].
    return
