# litellm reads its model-cost JSON at import time, so the data files must be
# bundled or every command that imports the SDK crashes with FileNotFoundError
# (issue #1113).
from PyInstaller.utils.hooks import collect_data_files

datas = collect_data_files("litellm")
