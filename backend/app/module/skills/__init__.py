"""Skill 註冊表：自動掃描本資料夾內所有 skill 模組。"""
import importlib
import pkgutil
from functools import lru_cache

from .base import Skill


@lru_cache(maxsize=1)
def load_skills() -> list[Skill]:
    skills: list[Skill] = []
    for mod in pkgutil.iter_modules(__path__):
        if mod.name == "base":
            continue
        module = importlib.import_module(f"{__name__}.{mod.name}")
        skill = getattr(module, "SKILL", None)
        if isinstance(skill, Skill):
            skills.append(skill)
    return sorted(skills, key=lambda s: s.name)
