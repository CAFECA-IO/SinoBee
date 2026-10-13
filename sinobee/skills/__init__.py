from . import spec
from .manager import Skill, SkillError, SkillManager
from .trust import LABELS, LEVELS, POLICIES



def manager_from_config(config, store=None) -> SkillManager:
    sk = config.get
    return SkillManager(config.skills_dir, store, project_dir=config.project_dir,
                        extra_dirs=sk("skills.extra_dirs") or [], interop=bool(sk("skills.interop", True)),
                        revision_threshold=float(sk("skills.revision_threshold", 0.5)),
                        promote_after=int(sk("skills.promote_after", 2)), disabled=sk("skills.disabled") or [],
                        write_approval=bool(sk("skills.write_approval", False))
                        or str(sk("skills.auto_create", "auto")).lower() == "suggest",
                        home=config.home)


__all__ = ["manager_from_config", "Skill", "SkillError", "SkillManager", "spec", "LABELS", "LEVELS", "POLICIES"]
