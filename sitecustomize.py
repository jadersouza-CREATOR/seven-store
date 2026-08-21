import importlib.abc
import importlib.machinery
import sys

class _AppLoader(importlib.abc.Loader):
    def __init__(self, spec):
        self.spec = spec
        self.loader = spec.loader
    def create_module(self, spec):
        if hasattr(self.loader, "create_module"):
            return self.loader.create_module(spec)
        return None
    def exec_module(self, module):
        self.loader.exec_module(module)
        from pending_extension import instalar as instalar_pendentes
        instalar_pendentes(module.app)
        from payment_extension import instalar as instalar_pagamentos
        instalar_pagamentos(module.app)

class _AppFinder(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname != "app":
            return None
        spec = importlib.machinery.PathFinder.find_spec(fullname, path)
        if spec and spec.loader and not isinstance(spec.loader, _AppLoader):
            spec.loader = _AppLoader(spec)
            return spec
        return None

if not any(isinstance(finder, _AppFinder) for finder in sys.meta_path):
    sys.meta_path.insert(0, _AppFinder())
