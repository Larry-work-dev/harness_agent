"""Workflow 註冊表：自動掃描本資料夾內所有 workflow 模組。"""
import importlib
import pkgutil

from .base import Workflow


def load_workflows() -> list[Workflow]:
    out: list[Workflow] = []
    for mod in pkgutil.iter_modules(__path__):
        if mod.name == "base":
            continue
        m = importlib.import_module(f"{__name__}.{mod.name}")
        wf = getattr(m, "WORKFLOW", None)
        if isinstance(wf, Workflow):
            out.append(wf)
    return out


def get_workflow(name: str) -> Workflow | None:
    return next((w for w in load_workflows() if w.name == name), None)
