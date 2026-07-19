"""Memória de projeto da Helena: um `.helena/` por projeto.

Quando a Helena trabalha num projeto de programação, ela mantém conhecimento
DURÁVEL sobre ele numa pasta `.helena/` dentro do diretório de trabalho — para
poder trabalhar de forma autônoma (nível Claude Code) sem reescanear tudo a cada
sessão. O conteúdo é navegável em JSON para ela buscar SÓ o pedaço que precisa,
sem despejar o projeto inteiro no contexto (economia de tokens).

Layout (dentro de <working_dir>/.helena/):
  .gitignore     -> contém "*" (a memória é local, nunca entra no git do projeto)
  project.json   -> visão geral: nome, linguagens, frameworks, comandos, git, árvore
  files.json     -> contexto por arquivo: { "caminho": {purpose, symbols, notes, ...} }
  notes.json     -> notas livres/decisões: { "chave": {text, tags, updated_at} }
  tests/         -> prints de teste e saídas (criada sob demanda)

A tool `projeto` (uma só, com `acao`) expõe isto ao modelo:
  escanear | mapa | ler | buscar | salvar | remover
"""
from __future__ import annotations

import json
import os
import subprocess
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from google.genai import types

from app.agent.shell_tool import resolve_workdir, shell_level

# Pastas/artefatos que nunca interessam na árvore/detecção
_IGNORE_DIRS = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env", "dist", "build", ".helena", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".idea", ".vscode", "target", ".next", ".nuxt", ".cache",
    "coverage", ".gradle", "vendor", ".tox", "__snapshots__",
}
_TREE_MAX_ENTRIES = 400   # teto de itens varridos na árvore
_TREE_MAX_DEPTH = 4
_MAPA_TREE_LINES = 50     # árvore truncada no `mapa` (compacto)

_SECOES = {"projeto": "project.json", "arquivos": "files.json", "notas": "notes.json"}


# --------------------------------------------------------------------------- #
# Caminhos e IO
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _root(user_id: int) -> Path:
    return Path(resolve_workdir(user_id))


def _helena_dir(user_id: int) -> Path:
    return _root(user_id) / ".helena"


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _exists(user_id: int) -> bool:
    return (_helena_dir(user_id) / "project.json").is_file()


def _norm(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", str(text).lower())
    return "".join(c for c in nfkd if not unicodedata.combining(c))


# --------------------------------------------------------------------------- #
# Detecção (linguagem, framework, gerenciador, git, comandos, árvore)
# --------------------------------------------------------------------------- #

_LANG_BY_EXT = {
    ".py": "Python", ".js": "JavaScript", ".ts": "TypeScript", ".jsx": "JavaScript (React)",
    ".tsx": "TypeScript (React)", ".go": "Go", ".rs": "Rust", ".java": "Java",
    ".kt": "Kotlin", ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C/C++ header",
    ".cpp": "C++", ".cc": "C++", ".cs": "C#", ".swift": "Swift", ".dart": "Dart",
    ".sh": "Shell", ".sql": "SQL", ".html": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".vue": "Vue", ".svelte": "Svelte", ".lua": "Lua", ".r": "R", ".ex": "Elixir",
}


def _scan_tree(root: Path) -> tuple[list[str], dict[str, int]]:
    """Varre a árvore (com tetos) e conta extensões. Devolve (linhas, ext_count)."""
    lines: list[str] = []
    ext_count: dict[str, int] = {}
    count = 0

    def walk(d: Path, depth: int):
        nonlocal count
        if depth > _TREE_MAX_DEPTH or count >= _TREE_MAX_ENTRIES:
            return
        try:
            entries = sorted(
                d.iterdir(), key=lambda p: (p.is_file(), p.name.lower())
            )
        except OSError:
            return
        for p in entries:
            if count >= _TREE_MAX_ENTRIES:
                lines.append("  " * depth + "… (truncado)")
                return
            name = p.name
            if name in _IGNORE_DIRS or (name.startswith(".") and p.is_dir() and name != "."):
                continue
            count += 1
            if p.is_dir():
                lines.append("  " * depth + name + "/")
                walk(p, depth + 1)
            else:
                lines.append("  " * depth + name)
                ext = p.suffix.lower()
                if ext:
                    ext_count[ext] = ext_count.get(ext, 0) + 1

    walk(root, 0)
    return lines, ext_count


def _languages(ext_count: dict[str, int]) -> dict[str, int]:
    langs: dict[str, int] = {}
    for ext, n in ext_count.items():
        lang = _LANG_BY_EXT.get(ext)
        if lang:
            langs[lang] = langs.get(lang, 0) + n
    return dict(sorted(langs.items(), key=lambda kv: kv[1], reverse=True))


def _read_text(path: Path, limit: int = 20000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")[:limit]
    except OSError:
        return ""


def _detect_stack(root: Path) -> tuple[list[str], list[str], dict]:
    """Frameworks, gerenciadores de pacote e comandos inferidos."""
    frameworks: list[str] = []
    managers: list[str] = []
    commands: dict[str, str] = {}

    def has(name: str) -> bool:
        return (root / name).exists()

    # ---- Python ----
    if has("pyproject.toml") or has("requirements.txt") or has("setup.py"):
        py_deps = ""
        for f in ("pyproject.toml", "requirements.txt", "setup.py", "Pipfile"):
            if has(f):
                py_deps += "\n" + _read_text(root / f)
        low = _norm(py_deps)
        for key, fw in (("flask", "Flask"), ("django", "Django"), ("fastapi", "FastAPI"),
                        ("streamlit", "Streamlit"), ("pytest", None)):
            if fw and key in low:
                frameworks.append(fw)
        if has("uv.lock"):
            managers.append("uv")
            commands.update(install="uv sync", test="uv run pytest")
        elif has("poetry.lock"):
            managers.append("poetry")
            commands.update(install="poetry install", test="poetry run pytest")
        else:
            managers.append("pip")
            commands.setdefault("install", "pip install -r requirements.txt")
            commands.setdefault("test", "pytest")

    # ---- Node ----
    if has("package.json"):
        pkg = {}
        try:
            pkg = json.loads(_read_text(root / "package.json"))
        except json.JSONDecodeError:
            pkg = {}
        deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}
        low = " ".join(deps).lower()
        for key, fw in (("next", "Next.js"), ("react", "React"), ("vue", "Vue"),
                        ("svelte", "Svelte"), ("@angular/core", "Angular"),
                        ("express", "Express"), ("nestjs", "NestJS"), ("nuxt", "Nuxt")):
            if key in low:
                frameworks.append(fw)
        if has("pnpm-lock.yaml"):
            managers.append("pnpm"); pm = "pnpm"
        elif has("yarn.lock"):
            managers.append("yarn"); pm = "yarn"
        else:
            managers.append("npm"); pm = "npm"
        commands.setdefault("install", f"{pm} install")
        scripts = pkg.get("scripts", {})
        if "dev" in scripts:
            commands.setdefault("run", f"{pm} run dev")
        elif "start" in scripts:
            commands.setdefault("run", f"{pm} start")
        if "build" in scripts:
            commands.setdefault("build", f"{pm} run build")
        if "test" in scripts:
            commands.setdefault("test", f"{pm} test")

    # ---- outros sinais rápidos ----
    if has("go.mod"):
        managers.append("go modules"); commands.setdefault("build", "go build ./...")
        commands.setdefault("test", "go test ./...")
    if has("Cargo.toml"):
        managers.append("cargo"); commands.setdefault("build", "cargo build")
        commands.setdefault("run", "cargo run"); commands.setdefault("test", "cargo test")
    if has("Dockerfile") or has("docker-compose.yml") or has("compose.yaml"):
        frameworks.append("Docker")
    if has("Makefile"):
        commands.setdefault("build", "make")

    # dedup preservando ordem
    frameworks = list(dict.fromkeys(frameworks))
    managers = list(dict.fromkeys(managers))
    return frameworks, managers, commands


def _git_info(root: Path) -> dict:
    def g(*args) -> str | None:
        try:
            r = subprocess.run(
                ["git", *args], cwd=str(root), capture_output=True,
                text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else None
        except (OSError, subprocess.SubprocessError):
            return None

    inside = g("rev-parse", "--is-inside-work-tree")
    if inside != "true":
        return {"is_repo": False}
    return {
        "is_repo": True,
        "branch": g("rev-parse", "--abbrev-ref", "HEAD"),
        "remote": g("remote", "get-url", "origin"),
        "last_commit": g("log", "-1", "--oneline"),
    }


# --------------------------------------------------------------------------- #
# Ações
# --------------------------------------------------------------------------- #

def escanear(user_id: int) -> dict:
    """(Re)detecta o projeto no diretório de trabalho e grava project.json.
    Preserva files.json/notes.json e a `description` já existentes (merge)."""
    root = _root(user_id)
    hd = _helena_dir(user_id)
    hd.mkdir(parents=True, exist_ok=True)
    # memória local: nunca vai pro git do projeto
    (hd / ".gitignore").write_text("*\n", encoding="utf-8")

    tree, ext_count = _scan_tree(root)
    frameworks, managers, commands = _detect_stack(root)

    prev = _load(hd / "project.json")
    project = {
        "name": root.name,
        "root": str(root),
        "languages": _languages(ext_count),
        "frameworks": frameworks,
        "package_managers": managers,
        "commands": commands,
        "git": _git_info(root),
        "estrutura": tree,
        "description": prev.get("description", ""),
        "updated_at": _now(),
    }
    _save(hd / "project.json", project)
    # garante os outros documentos (sem apagar o que já houver)
    for name in ("files.json", "notes.json"):
        p = hd / name
        if not p.is_file():
            _save(p, {})

    files = _load(hd / "files.json")
    return {
        "ok": True,
        "info": f"projeto escaneado em {root}",
        "name": project["name"],
        "languages": list(project["languages"]),
        "frameworks": frameworks,
        "package_managers": managers,
        "commands": commands,
        "git": project["git"],
        "arquivos_documentados": len(files),
        "itens_na_arvore": len(tree),
        "dica": "use acao=mapa para o índice, acao=ler para detalhes, acao=salvar para documentar.",
    }


def mapa(user_id: int) -> dict:
    """Índice compacto: visão geral + lista de arquivos documentados (só o resumo)
    + chaves de notas. Árvore truncada. É por aqui que a Helena começa."""
    if not _exists(user_id):
        return {
            "ok": True, "existe": False,
            "info": "Este projeto ainda não tem memória (.helena/). Use acao=escanear para criar.",
        }
    hd = _helena_dir(user_id)
    project = _load(hd / "project.json")
    files = _load(hd / "files.json")
    notes = _load(hd / "notes.json")

    tree = project.get("estrutura", [])
    tree_view = tree[:_MAPA_TREE_LINES]
    if len(tree) > _MAPA_TREE_LINES:
        tree_view = tree_view + [f"… (+{len(tree) - _MAPA_TREE_LINES} linhas — use acao=ler secao=projeto)"]

    indice = {
        path: (info.get("purpose") or "(sem resumo)")
        for path, info in files.items()
    }
    return {
        "ok": True, "existe": True,
        "name": project.get("name"),
        "root": project.get("root"),
        "languages": project.get("languages"),
        "frameworks": project.get("frameworks"),
        "package_managers": project.get("package_managers"),
        "commands": project.get("commands"),
        "git": project.get("git"),
        "description": project.get("description"),
        "estrutura": tree_view,
        "arquivos_documentados": indice,
        "notas": list(notes.keys()),
        "atualizado_em": project.get("updated_at"),
    }


def ler(user_id: int, secao: str, caminho: str | None) -> dict:
    """Lê uma fatia. `projeto` inteiro; ou uma chave de `arquivos`/`notas`."""
    if secao not in _SECOES:
        return {"ok": False, "error": f"seção inválida: {secao} (use {list(_SECOES)})"}
    if not _exists(user_id):
        return {"ok": False, "error": "sem memória (.helena/) aqui; rode acao=escanear."}
    data = _load(_helena_dir(user_id) / _SECOES[secao])
    if secao == "projeto":
        return {"ok": True, "secao": secao, "conteudo": data}
    if not caminho:
        return {"ok": True, "secao": secao, "chaves": list(data.keys())}
    if caminho not in data:
        return {
            "ok": False,
            "error": f"'{caminho}' não está documentado em {secao}.",
            "chaves_disponiveis": list(data.keys())[:50],
        }
    return {"ok": True, "secao": secao, "caminho": caminho, "conteudo": data[caminho]}


def buscar(user_id: int, consulta: str) -> dict:
    """Busca por palavra-chave em arquivos + notas + visão do projeto. Devolve
    as chaves que casam com um trecho, sem despejar tudo."""
    if not _exists(user_id):
        return {"ok": False, "error": "sem memória (.helena/) aqui; rode acao=escanear."}
    terms = [t for t in _norm(consulta).split() if len(t) > 1]
    if not terms:
        return {"ok": False, "error": "consulta vazia"}
    hd = _helena_dir(user_id)
    files = _load(hd / "files.json")
    notes = _load(hd / "notes.json")

    def score(blob: str) -> int:
        n = _norm(blob)
        return sum(n.count(t) for t in terms)

    hits: list[dict] = []
    for path, info in files.items():
        s = score(path + " " + json.dumps(info, ensure_ascii=False))
        if s:
            hits.append({"tipo": "arquivo", "chave": path,
                         "resumo": info.get("purpose") or "", "score": s})
    for key, info in notes.items():
        s = score(key + " " + json.dumps(info, ensure_ascii=False))
        if s:
            texto = info.get("text", "") if isinstance(info, dict) else str(info)
            hits.append({"tipo": "nota", "chave": key,
                         "resumo": texto[:160], "score": s})

    hits.sort(key=lambda h: h["score"], reverse=True)
    for h in hits:
        h.pop("score", None)
    if not hits:
        return {"ok": True, "encontrados": 0,
                "info": "nada casou; tente outros termos ou acao=mapa para o índice."}
    return {"ok": True, "encontrados": len(hits), "resultados": hits[:12]}


def salvar(user_id: int, secao: str, caminho: str | None, dados) -> dict:
    """Grava/atualiza um pedaço da memória. `projeto`: mescla `dados` no
    project.json (ex.: {"description": "..."}). `arquivos`/`notas`: grava sob
    a chave `caminho` (upsert). Sempre carimba updated_at."""
    if secao not in _SECOES:
        return {"ok": False, "error": f"seção inválida: {secao} (use {list(_SECOES)})"}
    hd = _helena_dir(user_id)
    if not (hd / _SECOES[secao]).is_file() and not _exists(user_id):
        # primeira gravação sem escanear: bootstrap mínimo
        escanear(user_id)
    path = hd / _SECOES[secao]
    data = _load(path)

    if secao == "projeto":
        if not isinstance(dados, dict):
            return {"ok": False, "error": "para secao=projeto, dados deve ser um objeto."}
        data.update(dados)
        data["updated_at"] = _now()
        _save(path, data)
        return {"ok": True, "secao": secao, "info": "project.json atualizado"}

    if not caminho:
        return {"ok": False, "error": f"secao={secao} exige 'caminho' (a chave)."}
    entry = data.get(caminho) if isinstance(data.get(caminho), dict) else {}
    if isinstance(dados, dict):
        entry.update(dados)
    else:
        entry["text"] = str(dados)
    entry["updated_at"] = _now()
    data[caminho] = entry
    _save(path, data)
    return {"ok": True, "secao": secao, "caminho": caminho, "info": "salvo"}


def remover(user_id: int, secao: str, caminho: str) -> dict:
    if secao not in ("arquivos", "notas"):
        return {"ok": False, "error": "só dá pra remover chaves de 'arquivos' ou 'notas'."}
    if not _exists(user_id):
        return {"ok": False, "error": "sem memória (.helena/) aqui."}
    path = _helena_dir(user_id) / _SECOES[secao]
    data = _load(path)
    if caminho not in data:
        return {"ok": False, "error": f"'{caminho}' não existe em {secao}."}
    del data[caminho]
    _save(path, data)
    return {"ok": True, "info": f"removido {caminho} de {secao}"}


# --------------------------------------------------------------------------- #
# Contexto (dica injetada quando existe memória) + Tool
# --------------------------------------------------------------------------- #

def context_hint(user_id: int) -> str | None:
    """Linha curtinha p/ o system_instruction quando o projeto atual tem memória.
    Não despeja conteúdo — só lembra a Helena de consultar a tool."""
    if not _exists(user_id):
        return None
    p = _load(_helena_dir(user_id) / "project.json")
    langs = ", ".join(list(p.get("languages", {}))[:3]) or "?"
    fw = ", ".join(p.get("frameworks", [])[:3])
    linha = f"'{p.get('name')}' — {langs}"
    if fw:
        linha += f" ({fw})"
    return (
        "## Memória deste projeto (.helena/)\n"
        f"Este diretório tem memória de projeto: {linha}. ANTES de mexer no "
        "código, chame a tool `projeto` (acao=mapa) para carregar o índice, e vá "
        "documentando o que aprender (acao=salvar). Não releia tudo à toa."
    )


PROJETO_DECL = types.FunctionDeclaration(
    name="projeto",
    description=(
        "Memória persistente do projeto de programação no seu diretório de "
        "trabalho atual (pasta .helena/), navegável em JSON para você consultar "
        "só o que precisa. Use SEMPRE que trabalhar em código, para agir de forma "
        "autônoma sem reescanear tudo. Fluxo: `mapa` (índice: visão geral, "
        "árvore, lista de arquivos documentados e notas) → `ler` (detalhe de um "
        "arquivo/nota, ou o project.json inteiro) → `buscar` (por palavra-chave) "
        "→ `salvar` (documentar contexto de um arquivo, decisões, comandos, ou a "
        "descrição do projeto conforme aprende). Se ainda não existir memória, "
        "rode `escanear` uma vez para detectar linguagem, framework, comandos, "
        "git e a estrutura. Mantenha a memória atualizada ao longo do trabalho."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "acao": types.Schema(
                type=types.Type.STRING,
                enum=["escanear", "mapa", "ler", "buscar", "salvar", "remover"],
                description="A operação a executar.",
            ),
            "secao": types.Schema(
                type=types.Type.STRING,
                enum=["projeto", "arquivos", "notas"],
                description="Para ler/salvar/remover: qual documento.",
            ),
            "caminho": types.Schema(
                type=types.Type.STRING,
                description="A chave dentro da seção: o caminho do arquivo (ex.: 'app/agent/tools.py') ou o nome da nota.",
            ),
            "consulta": types.Schema(
                type=types.Type.STRING,
                description="Para acao=buscar: palavras-chave.",
            ),
            "dados": types.Schema(
                type=types.Type.OBJECT,
                description=(
                    "Para acao=salvar: o objeto a gravar. Em 'arquivos' use ex.: "
                    '{"purpose": "...", "symbols": ["..."], "notes": "..."}; '
                    'em \'notas\' use {"text": "...", "tags": ["..."]}; '
                    'em \'projeto\' use {"description": "..."}.'
                ),
            ),
        },
        required=["acao"],
    ),
)


def projeto(user_id: int, args: dict) -> dict:
    """Handler único da tool `projeto` — despacha por `acao`."""
    if shell_level(user_id) is None:
        return {
            "ok": False,
            "error": (
                "A memória de projeto exige permissão para trabalhar na máquina "
                "(usuário principal). Explique e não tente."
            ),
        }
    acao = (args.get("acao") or "").strip()
    secao = args.get("secao")
    caminho = args.get("caminho")
    if acao == "escanear":
        return escanear(user_id)
    if acao == "mapa":
        return mapa(user_id)
    if acao == "ler":
        return ler(user_id, secao, caminho)
    if acao == "buscar":
        return buscar(user_id, (args.get("consulta") or "").strip())
    if acao == "salvar":
        return salvar(user_id, secao, caminho, args.get("dados"))
    if acao == "remover":
        return remover(user_id, secao, caminho)
    return {"ok": False, "error": f"ação desconhecida: {acao}"}
