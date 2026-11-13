#!/usr/bin/env python3
"""Refresh repository, KiCad, and reproducibility metadata in data/projects.json.

The score measures public repository evidence, not electrical engineering quality.
Photo and hardware-test points require reviewed evidence in each project's
"evidence" object; keyword matches never earn those points automatically.

Examples:
    python scripts/audit_projects.py --added-on 2026-11-13
    python scripts/audit_projects.py --url https://github.com/rbaron/b-parasite
    python scripts/audit_projects.py --all --dry-run

Set GITHUB_TOKEN to raise GitHub API limits. No third-party packages are needed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = ROOT / "data" / "projects.json"
EVIDENCE_FILE = ROOT / "data" / "evidence.json"
API_ROOT = "https://api.github.com"
USER_AGENT = "awesome-kicad-projects-auditor/1.0"
SCORE_VERSION = "1.0"
OPEN_LICENSES = {
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "BSL-1.0",
    "CC-BY-4.0", "CC-BY-SA-4.0", "CC0-1.0", "CERN-OHL-1.2",
    "CERN-OHL-P-2.0", "CERN-OHL-S-2.0", "CERN-OHL-W-2.0", "GPL-2.0",
    "GPL-3.0", "LGPL-2.1", "LGPL-3.0", "MIT", "MPL-2.0", "Unlicense",
}
LICENSE_PATTERNS = (
    (r"CERN[ -]+OHL[ -]+S(?:[ -](?:v(?:ersion)?\s*)?2(?:\.0)?)?", "CERN-OHL-S-2.0"),
    (r"CERN[ -]+OHL[ -]+P(?:[ -](?:v(?:ersion)?\s*)?2(?:\.0)?)?", "CERN-OHL-P-2.0"),
    (r"CERN[ -]+OHL[ -]+W(?:[ -](?:v(?:ersion)?\s*)?2(?:\.0)?)?", "CERN-OHL-W-2.0"),
    (r"CERN[ -]+OHL(?:\s+(?:v(?:ersion)?\s*)?1\.2)", "CERN-OHL-1.2"),
    (r"CC[ -]BY[ -]SA[ -]4\.0|Attribution-ShareAlike 4\.0", "CC-BY-SA-4.0"),
    (r"CC[ -]BY[ -]4\.0|Attribution 4\.0", "CC-BY-4.0"),
)
VERSION_RE = re.compile(r"\bversion\s+(\d{6,})\b")
GENERATOR_VERSION_RE = re.compile(r"\(generator_version\s+\"([^\"]+)\"\)")
README_VERSION_RE = re.compile(r"\bkicad\s*(?:version|v)?\s*(\d+(?:\.\d+){0,2})", re.IGNORECASE)


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def github_repo(url: str) -> tuple[str, str] | None:
    match = re.match(r"https?://github\.com/([^/]+)/([^/#?]+)", url)
    if not match:
        return None
    return match.group(1), match.group(2).removesuffix(".git")


def request(url: str, raw: bool = False):
    headers = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token and url.startswith(API_ROOT):
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        remaining = exc.headers.get("X-RateLimit-Remaining")
        reset = exc.headers.get("X-RateLimit-Reset")
        hint = f"; rate remaining={remaining}, reset={reset}" if remaining is not None else ""
        raise RuntimeError(f"GET {url}: HTTP {exc.code}{hint}") from exc
    return body if raw else json.loads(body)


def raw_url(full_name: str, branch: str, path: str) -> str:
    encoded = urllib.parse.quote(path, safe="/")
    return f"https://raw.githubusercontent.com/{full_name}/{urllib.parse.quote(branch)}/{encoded}"


def blob_url(url: str, branch: str, path: str) -> str:
    return f"{url.rstrip('/')}/blob/{urllib.parse.quote(branch)}/{urllib.parse.quote(path, safe='/')}"


def read_raw(full_name: str, branch: str, path: str, limit: int = 2_000_000) -> str:
    body = request(raw_url(full_name, branch, path), raw=True)
    return body[:limit].decode("utf-8", errors="replace")


def any_path(paths: list[str], predicate) -> bool:
    return any(predicate(path) for path in paths)


def detect_license(readme: str) -> str | None:
    for pattern, license_id in LICENSE_PATTERNS:
        if re.search(pattern, readme, re.IGNORECASE):
            return license_id
    return None


def detect_signals(paths: list[str], readme: str, workflows: str) -> dict[str, bool]:
    lower = [path.lower() for path in paths]
    basenames = [path.rsplit("/", 1)[-1] for path in lower]
    gerber_ext = (".gbr", ".ger", ".gtl", ".gbl", ".gts", ".gbs", ".gto", ".gbo", ".gm1", ".drl")
    return {
        "pcb": any_path(lower, lambda p: p.endswith(".kicad_pcb")),
        "schematic": any_path(lower, lambda p: p.endswith((".kicad_sch", ".sch"))),
        "project": any_path(lower, lambda p: p.endswith((".kicad_pro", ".pro"))),
        "local_libraries": any_path(lower, lambda p: p.endswith((".kicad_sym", ".kicad_mod", ".lib")) or ".pretty/" in p),
        "bom": any(
            ("bom" in name or "bill.of.material" in name or "bill-of-material" in name)
            and name.endswith((".csv", ".tsv", ".xlsx", ".xls", ".json", ".xml", ".html", ".md", ".txt"))
            for name in basenames
        ),
        "gerbers": any_path(lower, lambda p: p.endswith(gerber_ext) or ("gerber" in p and p.endswith(".zip"))),
        "placement": any(
            name.endswith((".cpl", ".pos"))
            or (("pick" in name and "place" in name) or "placement" in name or "positions" in name)
            and name.endswith((".csv", ".txt", ".xlsx"))
            for name in basenames
        ),
        "pdf_schematic": any_path(lower, lambda p: p.endswith(".pdf") and ("schem" in p or p.endswith("_sch.pdf"))),
        "build_docs": any_path(lower, lambda p: p.endswith((".md", ".pdf")) and re.search(
            r"assembl|fabricat|manufactur|getting.started|bring.?up|build|order", p
        )) or bool(re.search(r"\b(assemble|assembly|fabricat|manufactur|order (?:the )?pcb)\b", readme, re.IGNORECASE)),
        "ci": any_path(lower, lambda p: p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))) or ".gitlab-ci.yml" in lower,
        "kicad_validation": bool(re.search(r"kicad-cli|kibot", workflows, re.IGNORECASE))
        and bool(re.search(r"\b(drc|erc)\b", workflows, re.IGNORECASE)),
        "readme": bool(readme.strip()),
        "docs": any_path(lower, lambda p: p.startswith(("docs/", "doc/")) and p.endswith((".md", ".rst", ".pdf"))),
    }


def evidence_points(project: dict, key: str) -> tuple[int, str]:
    evidence = project.get("evidence", {}).get(key, {})
    status = evidence.get("status", "unknown")
    url = evidence.get("url")
    return (10 if status == "verified" and url else 0), status


def calculate_quality(project: dict, signals: dict[str, bool], repo: dict, checked_at: str) -> dict:
    photo_points, photo_status = evidence_points(project, "real_build_photo")
    test_points, test_status = evidence_points(project, "hardware_tested")
    license_id = project.get("license")
    pushed = repo.get("pushed_at")
    activity = 0
    if pushed:
        try:
            pushed_date = dt.datetime.fromisoformat(pushed.replace("Z", "+00:00"))
            age_days = (dt.datetime.fromisoformat(checked_at) - pushed_date).days
            activity = 6 if age_days <= 365 else 3 if age_days <= 1095 else 0
        except ValueError:
            pass
    breakdown = {
        "design_sources": (
            8 * signals["pcb"] + 6 * signals["schematic"] + 3 * signals["project"]
            + 3 * signals["local_libraries"]
        ),
        "reproducibility": (
            7 * signals["bom"] + 6 * signals["gerbers"] + 4 * signals["placement"]
            + 5 * signals["build_docs"] + 3 * signals["pdf_schematic"]
        ),
        "verification": photo_points + test_points + 5 * signals["kicad_validation"],
        "maintenance": (
            activity + 3 * (not repo.get("archived", False)) + 3 * signals["ci"]
            + 3 * repo.get("has_issues", False)
        ),
        "documentation_openness": (
            6 * (license_id in OPEN_LICENSES) + 4 * signals["readme"]
            + 2 * bool(project.get("description")) + 3 * (signals["docs"] or repo.get("has_wiki", False))
        ),
    }
    return {
        "score": sum(breakdown.values()),
        "max_score": 100,
        "score_version": SCORE_VERSION,
        "calculated_at": checked_at,
        "basis": "Public repository evidence only; not a rating of electrical design quality.",
        "breakdown": breakdown,
        "signals": {**signals, "real_build_photo": photo_status, "hardware_tested": test_status},
    }


def audit(project: dict) -> dict:
    parsed = github_repo(project["url"])
    if not parsed:
        print(f"SKIP {project['name']}: only GitHub repositories are currently supported")
        return project
    owner, name = parsed
    full_name = f"{owner}/{name}"
    repo = request(f"{API_ROOT}/repos/{full_name}")
    branch = repo["default_branch"]
    tree = request(f"{API_ROOT}/repos/{full_name}/git/trees/{urllib.parse.quote(branch)}?recursive=1")
    paths = [item["path"] for item in tree.get("tree", []) if item.get("type") == "blob"]
    readme_paths = sorted(
        (p for p in paths if p.rsplit("/", 1)[-1].lower().startswith(("readme.",)) or p.rsplit("/", 1)[-1].lower() == "readme"),
        key=lambda p: (p.count("/"), len(p)),
    )
    readme = read_raw(full_name, branch, readme_paths[0]) if readme_paths else ""
    workflow_paths = [p for p in paths if p.startswith(".github/workflows/") and p.endswith((".yml", ".yaml"))][:5]
    workflows = "\n".join(read_raw(full_name, branch, p, 300_000) for p in workflow_paths)
    signals = detect_signals(paths, readme, workflows)

    pcb_paths = sorted((p for p in paths if p.lower().endswith(".kicad_pcb")), key=lambda p: (p.count("/"), len(p)))
    pcb_text = read_raw(full_name, branch, pcb_paths[0], 100_000) if pcb_paths else ""
    format_match = VERSION_RE.search(pcb_text[:20_000])
    generator_match = GENERATOR_VERSION_RE.search(pcb_text[:20_000])
    readme_match = README_VERSION_RE.search(readme)
    if generator_match:
        version = generator_match.group(1)
        version_source = "kicad_pcb_generator_version"
        version_url = blob_url(project["url"], branch, pcb_paths[0])
    elif readme_match:
        version = readme_match.group(1)
        version_source = "readme"
        version_url = blob_url(project["url"], branch, readme_paths[0])
    else:
        version = None
        version_source = "unavailable"
        version_url = None

    api_license = repo.get("license", {}) and repo["license"].get("spdx_id")
    readme_license = detect_license(readme) if api_license not in OPEN_LICENSES else None
    license_id = readme_license or api_license
    license_source = "readme" if readme_license else "github_api"

    checked_at = now_iso()
    project.update({
        "author": repo["owner"]["login"],
        "license": license_id,
        "license_source": license_source,
        "stars": repo["stargazers_count"],
        "forks": repo["forks_count"],
        "open_issues": repo["open_issues_count"],
        "archived": repo["archived"],
        "created_at": repo["created_at"][:10],
        "pushed_at": repo["pushed_at"][:10] if repo.get("pushed_at") else None,
        "default_branch": branch,
        "kicad": {
            "version": version,
            "version_source": version_source,
            "version_evidence": version_url,
            "file_format_version": format_match.group(1) if format_match else None,
            "source_files": {
                "pcb": sum(p.lower().endswith(".kicad_pcb") for p in paths),
                "schematic": sum(p.lower().endswith((".kicad_sch", ".sch")) for p in paths),
                "project": sum(p.lower().endswith((".kicad_pro", ".pro")) for p in paths),
            },
        },
        "repository_checked_at": checked_at,
    })
    project["quality"] = calculate_quality(project, signals, repo, checked_at)
    if not tree.get("truncated", False):
        project["audit_status"] = "complete"
    else:
        project["audit_status"] = "partial_tree"
    print(f"OK   {project['name']}: KiCad {version or 'unknown'}, score {project['quality']['score']}/100")
    return project


def refresh_from_readme(project: dict) -> None:
    """Recover explicit versions and licenses without spending GitHub core API calls."""
    parsed = github_repo(project["url"])
    if not parsed or not project.get("default_branch"):
        return
    full_name = "/".join(parsed)
    branch = project["default_branch"]
    readme_path = None
    readme = ""
    for candidate in ("README.md", "readme.md", "Readme.md"):
        try:
            readme = read_raw(full_name, branch, candidate)
            readme_path = candidate
            break
        except RuntimeError as exc:
            if "HTTP 404" not in str(exc):
                raise
    if readme_path is None:
        print(f"OK   {project['name']}: no root README")
        return
    changes = []
    kicad = project.get("kicad")
    if kicad and not kicad.get("version"):
        match = README_VERSION_RE.search(readme)
        if match:
            kicad.update({
                "version": match.group(1),
                "version_source": "readme",
                "version_evidence": blob_url(project["url"], branch, readme_path),
            })
            changes.append(f"KiCad {match.group(1)}")
    if project.get("license") not in OPEN_LICENSES:
        license_id = detect_license(readme)
        if license_id:
            old_open = project.get("license") in OPEN_LICENSES
            project["license"] = license_id
            project["license_source"] = "readme"
            quality = project.get("quality")
            if quality and not old_open:
                quality["score"] += 6
                quality["breakdown"]["documentation_openness"] += 6
            changes.append(f"license {license_id}")
    print(f"OK   {project['name']}: {', '.join(changes) if changes else 'no README corrections'}")


def validate(data: dict) -> list[str]:
    errors = []
    seen = set()
    for index, project in enumerate(data.get("projects", [])):
        for key in ("name", "url", "category", "description", "status"):
            if not project.get(key):
                errors.append(f"projects[{index}]: missing {key}")
        normalized = project.get("url", "").rstrip("/").lower()
        if normalized in seen:
            errors.append(f"projects[{index}]: duplicate URL {normalized}")
        seen.add(normalized)
        quality = project.get("quality")
        if quality:
            total = sum(quality.get("breakdown", {}).values())
            if total != quality.get("score"):
                errors.append(f"projects[{index}]: score does not match breakdown")
            if not 0 <= total <= 100:
                errors.append(f"projects[{index}]: score outside 0..100")
            if not quality.get("signals", {}).get("pcb", False):
                errors.append(f"projects[{index}]: audited project lacks a .kicad_pcb source")
        for evidence_name in ("real_build_photo", "hardware_tested"):
            evidence = project.get("evidence", {}).get(evidence_name, {})
            if evidence.get("status") == "verified" and not evidence.get("url"):
                errors.append(f"projects[{index}]: verified {evidence_name} lacks URL")
    return errors


def apply_reviewed_evidence(data: dict) -> int:
    """Merge human-reviewed evidence and adjust existing scores without network access."""
    if not EVIDENCE_FILE.exists():
        return 0
    reviewed = json.loads(EVIDENCE_FILE.read_text(encoding="utf-8"))["projects"]
    applied = 0
    for project in data["projects"]:
        evidence = reviewed.get(project["url"].rstrip("/"))
        if not evidence:
            continue
        project["evidence"] = evidence
        quality = project.get("quality")
        if quality:
            photo_points, photo_status = evidence_points(project, "real_build_photo")
            test_points, test_status = evidence_points(project, "hardware_tested")
            verification = (
                photo_points + test_points
                + 5 * quality["signals"].get("kicad_validation", False)
            )
            quality["score"] += verification - quality["breakdown"]["verification"]
            quality["breakdown"]["verification"] = verification
            quality["signals"]["real_build_photo"] = photo_status
            quality["signals"]["hardware_tested"] = test_status
        applied += 1
    return applied


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--all", action="store_true", help="Audit every GitHub project")
    parser.add_argument("--url", action="append", default=[], help="Audit only this repository URL (repeatable)")
    parser.add_argument("--added-on", help="Audit only projects added on YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="Print results without writing projects.json")
    parser.add_argument("--validate-only", action="store_true", help="Validate data without network access")
    parser.add_argument("--evidence-only", action="store_true", help="Merge reviewed evidence without network access")
    parser.add_argument("--readme-only", action="store_true", help="Recover explicit versions/licenses from root READMEs")
    args = parser.parse_args()
    data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    if args.validate_only:
        errors = validate(data)
        print("\n".join(errors) if errors else f"Valid: {len(data['projects'])} projects")
        return bool(errors)
    applied = apply_reviewed_evidence(data)
    if args.evidence_only:
        errors = validate(data)
        if errors:
            print("\n".join(errors), file=sys.stderr)
            return 1
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Merged reviewed evidence for {applied} projects")
        return 0
    selected = set(args.url and (u.rstrip("/").lower() for u in args.url) or [])
    projects = []
    for project in data["projects"]:
        wanted = args.all or project["url"].rstrip("/").lower() in selected or (
            args.added_on and project.get("added_to_list") == args.added_on
        )
        if wanted:
            projects.append(project)
    if not projects:
        parser.error("no projects selected; use --all, --url, or --added-on")
    if not args.all and not selected and not args.added_on:
        parser.error("select projects with --all, --url, or --added-on")
    operation = refresh_from_readme if args.readme_only else audit
    for index, project in enumerate(projects):
        try:
            operation(project)
        except Exception as exc:
            print(f"FAIL {project['name']}: {exc}", file=sys.stderr)
            return 1
        if index + 1 < len(projects):
            time.sleep(0.15)
    errors = validate(data)
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    if not args.readme_only:
        data["last_refresh"] = now_iso()
    if not args.dry_run:
        DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"Updated {DATA_FILE.relative_to(ROOT)} ({len(projects)} processed; evidence for {applied})")
    else:
        print(f"Dry run: {len(projects)} processed; no file written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
