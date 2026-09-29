"""Project detection: languages, frameworks, database, test commands.

Tuned first for the stacks Forge is expected to work in most often -
React front ends, Go (Fiber) back ends and PostgreSQL - while still
recognising Python, Node and other common projects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIRS = {
    ".git",
    "node_modules",
    "dist",
    "build",
    ".next",
    ".turbo",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "coverage",
    ".forge",
    "vendor",
    "target",
}

CODE_EXTENSIONS = {
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".go": "Go",
    ".py": "Python",
    ".sql": "SQL",
    ".rs": "Rust",
    ".java": "Java",
    ".rb": "Ruby",
    ".php": "PHP",
    ".css": "CSS",
    ".md": "Markdown",
}


@dataclass
class ProjectProfile:
    root: Path
    languages: list[str] = field(default_factory=list)
    frameworks: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    package_managers: list[str] = field(default_factory=list)
    test_commands: list[str] = field(default_factory=list)
    build_commands: list[str] = field(default_factory=list)
    dev_commands: list[str] = field(default_factory=list)
    file_count: int = 0

    def summary(self) -> str:
        parts = []
        if self.languages:
            parts.append("Languages: " + ", ".join(self.languages))
        if self.frameworks:
            parts.append("Frameworks: " + ", ".join(self.frameworks))
        if self.databases:
            parts.append("Data: " + ", ".join(self.databases))
        if self.test_commands:
            parts.append("Tests: " + " | ".join(self.test_commands))
        if self.build_commands:
            parts.append("Build: " + " | ".join(self.build_commands))
        parts.append(f"Indexed files: {self.file_count}")
        return "\n".join(parts)


def iter_source_files(root: Path, limit: int = 8000) -> list[Path]:
    files: list[Path] = []
    stack = [root]
    while stack and len(files) < limit:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (PermissionError, OSError):
            continue
        for entry in entries:
            if entry.name.startswith(".") and entry.name not in {".env.example", ".vscode"}:
                if entry.is_dir():
                    continue
            if entry.is_dir():
                if entry.name in IGNORE_DIRS:
                    continue
                stack.append(entry)
            elif entry.suffix in CODE_EXTENSIONS or entry.name in {
                "go.mod",
                "package.json",
                "pyproject.toml",
                "requirements.txt",
                "Dockerfile",
                "docker-compose.yml",
                "Makefile",
            }:
                files.append(entry)
    return files


def detect(root: Path) -> ProjectProfile:
    profile = ProjectProfile(root=root)
    files = iter_source_files(root)
    profile.file_count = len(files)

    langs: dict[str, int] = {}
    for path in files:
        lang = CODE_EXTENSIONS.get(path.suffix)
        if lang:
            langs[lang] = langs.get(lang, 0) + 1
    profile.languages = [
        name for name, _ in sorted(langs.items(), key=lambda kv: kv[1], reverse=True)[:6]
    ]

    pkg = root / "package.json"
    if pkg.exists():
        profile.package_managers.append(
            "bun" if (root / "bun.lock").exists() or (root / "bun.lockb").exists()
            else "pnpm" if (root / "pnpm-lock.yaml").exists()
            else "yarn" if (root / "yarn.lock").exists()
            else "npm"
        )
        try:
            data = json.loads(pkg.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {}
        deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
        for name, label in (
            ("react", "React"),
            ("next", "Next.js"),
            ("@tanstack/react-router", "TanStack Router"),
            ("@tanstack/react-query", "TanStack Query"),
            ("vite", "Vite"),
            ("tailwindcss", "Tailwind CSS"),
            ("express", "Express"),
            ("prisma", "Prisma"),
            ("drizzle-orm", "Drizzle"),
            ("pg", "PostgreSQL (node-postgres)"),
            ("vitest", "Vitest"),
            ("jest", "Jest"),
            ("playwright", "Playwright"),
        ):
            if name in deps:
                (profile.databases if "PostgreSQL" in label else profile.frameworks).append(label)
        scripts = data.get("scripts") or {}
        runner = profile.package_managers[0]
        for key, bucket in (
            ("test", profile.test_commands),
            ("build", profile.build_commands),
            ("dev", profile.dev_commands),
            ("typecheck", profile.build_commands),
            ("lint", profile.build_commands),
        ):
            if key in scripts:
                bucket.append(f"{runner} run {key}")

    gomod = root / "go.mod"
    if gomod.exists():
        profile.languages = ["Go", *[l for l in profile.languages if l != "Go"]]
        text = gomod.read_text(encoding="utf-8", errors="ignore")
        if "gofiber/fiber" in text:
            profile.frameworks.append("Fiber")
        if "gin-gonic" in text:
            profile.frameworks.append("Gin")
        for dep, label in (
            ("jackc/pgx", "PostgreSQL (pgx)"),
            ("lib/pq", "PostgreSQL (lib/pq)"),
            ("gorm.io/gorm", "GORM"),
            ("sqlc", "sqlc"),
        ):
            if dep in text:
                profile.databases.append(label)
        profile.test_commands.append("go test ./...")
        profile.build_commands.append("go build ./...")

    if (
        (root / "pyproject.toml").exists()
        or (root / "requirements.txt").exists()
        or (root / "tests").is_dir()
        or any(root.glob("test_*.py"))
    ):
        profile.test_commands.append("pytest -q")

    for marker, label in (
        ("docker-compose.yml", "Docker Compose"),
        ("Dockerfile", "Docker"),
        ("Makefile", "Make"),
    ):
        if (root / marker).exists():
            profile.frameworks.append(label)

    sql_dirs = [p for p in (root / "migrations", root / "db", root / "sql") if p.exists()]
    if sql_dirs or any(p.suffix == ".sql" for p in files):
        profile.databases.append("SQL migrations")

    profile.frameworks = _dedupe(profile.frameworks)
    profile.databases = _dedupe(profile.databases)
    profile.test_commands = _dedupe(profile.test_commands)
    profile.build_commands = _dedupe(profile.build_commands)
    profile.dev_commands = _dedupe(profile.dev_commands)
    return profile


def _dedupe(items: list[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item not in seen:
            seen.append(item)
    return seen


def repo_map(root: Path, max_entries: int = 400) -> str:
    """A compact tree of the most relevant files, for the model's first look."""
    files = iter_source_files(root)
    rels = sorted(str(p.relative_to(root)) for p in files)
    if len(rels) > max_entries:
        head = rels[:max_entries]
        return "\n".join(head) + f"\n… and {len(rels) - max_entries} more files"
    return "\n".join(rels)


IMPORT_PATTERNS = [
    re.compile(r"""^\s*import\s+.*?from\s+['"](?P<t>[^'"]+)['"]""", re.M),
    re.compile(r"""^\s*(?:from|import)\s+(?P<t>[A-Za-z0-9_.]+)""", re.M),
    re.compile(r"""^\s*"(?P<t>[a-z0-9._/-]+)"\s*$""", re.M),
]


def imports_of(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out: list[str] = []
    for pattern in IMPORT_PATTERNS:
        for match in pattern.finditer(text):
            target = match.group("t")
            if target and target not in out:
                out.append(target)
    return out[:60]
