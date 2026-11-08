"""Project operations: create/update HTML, publish, resolve by id or slug."""

from models.projects import Projects
from services.forge import repo
from services.forge.util import DomainError, sha, slugify


async def get_project(ref: str | int) -> dict:
    """Resolve a numeric id or a slug."""
    text = str(ref)
    if text.isdigit():
        project = await repo.get(Projects, int(text))
    else:
        rows = await repo.find(Projects, Projects.slug == text, limit=1)
        project = rows[0] if rows else None
    if not project:
        raise DomainError("项目不存在")
    return project


async def create_project(name: str, html: str, description: str = "") -> dict:
    """Create a project holding a single-file HTML app."""
    name = (name or "未命名应用")[:40]
    slug = f"{slugify(name)}-{sha(name, html)[:5]}"
    existing = await repo.find(Projects, Projects.slug == slug, limit=1)
    if existing:
        slug = f"{slug}-{(await repo.count(Projects)) + 1}"
    project = await repo.add(Projects, name=name, description=description, slug=slug, html=html, published=False)
    return project


async def update_project_html(project_id: int, html: str, name: str = "") -> dict:
    """Iterative edit: overwrite the generated HTML (name updated only when provided)."""
    project = await get_project(project_id)
    fields = {"html": html}
    if name:
        fields["name"] = name[:40]
    await repo.update(Projects, project_id, **fields)
    return await get_project(project_id)


async def publish_project(project_id: int) -> dict:
    """Mark a project as published so it can be shared via its public slug URL."""
    project = await get_project(project_id)
    if not project.get("html"):
        raise DomainError("应用还没有生成内容，暂不能发布")
    await repo.update(Projects, project_id, published=True)
    return await get_project(project_id)
