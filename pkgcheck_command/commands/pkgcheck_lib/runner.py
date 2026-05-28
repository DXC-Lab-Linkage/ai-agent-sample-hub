#!/usr/bin/env python3
# pkgcheck runner — single-process orchestrator for the /pkgcheck command.
#
# Performs all fetching, parsing, classification, and report formatting
# in one Python process. The agent's job is reduced to:
#   1. invoke this script,
#   2. emit its stdout verbatim to the user.
#
# Bundling the work in a single process minimizes shell-quoting issues
# and reduces per-tool-call approval prompts in agent runtimes that
# require confirmation for each external command.
#
# Safety properties:
#   - Network access restricted to a hardcoded host allowlist.
#   - Redirects rejected if they leave the allowlist.
#   - Package names validated against a strict character set.
#   - HTTPS only; TLS verification on (urllib default).
#   - Per-request timeout; total work bounded by package count.
#   - Never executes package code; only fetches metadata JSON.
#
# Portability: standard-library only. Tested on Windows; expected to
# work on macOS / Linux without modification. Requires Python 3.11+
# (for `tomllib`); falls back to `tomli` if installed on older Pythons.
#
# Spec source of truth: ../pkgcheck.md.

from __future__ import annotations

import json
import os
import re
import socket
import ssl
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError

ALLOWED_HOSTS = frozenset({
    "pypi.org",
    "registry.npmjs.org",
    "api.npmjs.org",      # npm download counts
    "pypistats.org",      # PyPI download counts
    "api.osv.dev",
    "api.github.com",
    "red.anthropic.com",
})

USER_AGENT = "pkgcheck-runner/1.0 (+ai-workspace/p002-test)"
TIMEOUT_S = 15
PACKAGE_NAME_RE = re.compile(r"^@?[A-Za-z0-9][A-Za-z0-9._/-]*$")


# ----- HTTP --------------------------------------------------------------

class _AllowlistRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        host = urllib.parse.urlparse(new.full_url).hostname
        if host not in ALLOWED_HOSTS:
            raise URLError(f"redirect to disallowed host: {host}")
        return new


_OPENER = urllib.request.build_opener(_AllowlistRedirect(),
                                      urllib.request.HTTPSHandler(
                                          context=ssl.create_default_context()))


def http_request(url, *, headers=None, post_body=None, content_type=None):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"non-https url rejected: {url}")
    if parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError(f"host not in allowlist: {parsed.hostname}")
    h = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if headers:
        h.update(headers)
    if content_type:
        h["Content-Type"] = content_type
    req = urllib.request.Request(url, data=post_body, headers=h,
                                 method="POST" if post_body else "GET")
    try:
        with _OPENER.open(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read()
    except HTTPError as e:
        return e.code, (e.read() if e.fp else b"")


# ----- Helpers -----------------------------------------------------------

def today_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def short(s, n=80):
    s = s or ""
    return s if len(s) <= n else s[:n] + "..."


def is_valid_name(name):
    return bool(name and PACKAGE_NAME_RE.match(name))


def parse_version(v):
    """Parse a numeric version string. Returns tuple of ints, or None.
    Strips leading 'v', drops prerelease/build suffixes."""
    v = (v or "").strip().lstrip("vV")
    for sep in ("-", "+"):
        if sep in v:
            v = v.split(sep, 1)[0]
    parts = []
    for p in v.split("."):
        try:
            parts.append(int(p))
        except ValueError:
            return None
    return tuple(parts) if parts else None


def _ver_pad(a, b):
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def _ver_ge(a, b):
    a, b = _ver_pad(a, b); return a >= b


def _ver_lt(a, b):
    a, b = _ver_pad(a, b); return a < b


def _ver_le(a, b):
    a, b = _ver_pad(a, b); return a <= b


def is_version_in_vulnerable_range(version, ranges):
    """Decide if `version` falls inside ANY vulnerable interval defined by
    OSV `ranges[]`. Returns True (vulnerable), False (not vulnerable), or
    None (cannot decide from the data — non-numeric prerelease, GIT-type
    range, malformed events). Per OSV spec, `events` within a range are
    sorted ascending and alternate between `introduced` and `fixed`/
    `last_affected`, defining one or more half-open intervals
    [introduced, fixed). An `introduced` without a subsequent `fixed`
    means the range is open (vulnerable forever forward).
    """
    pv = parse_version(version)
    if pv is None:
        return None
    any_evaluated = False
    for r in ranges:
        rtype = r.get("type")
        if rtype not in ("SEMVER", "ECOSYSTEM"):
            continue  # GIT not supported
        any_evaluated = True
        events = r.get("events", []) or []
        intro = None
        for ev in events:
            if "introduced" in ev:
                istr = ev["introduced"]
                intro = (0,) if istr == "0" else parse_version(istr)
                if intro is None:
                    return None  # can't parse introduced version
            elif "fixed" in ev:
                fixed_v = parse_version(ev["fixed"])
                if fixed_v is None:
                    return None
                if intro is not None:
                    if _ver_ge(pv, intro) and _ver_lt(pv, fixed_v):
                        return True
                    intro = None
            elif "last_affected" in ev:
                la = parse_version(ev["last_affected"])
                if la is None:
                    return None
                if intro is not None:
                    if _ver_ge(pv, intro) and _ver_le(pv, la):
                        return True
                    intro = None
        # Open range: introduced without subsequent fixed → vulnerable forever
        if intro is not None and _ver_ge(pv, intro):
            return True
    return False if any_evaluated else None


def damerau_levenshtein(s1, s2):
    """Edit distance with adjacent-character transposition.
    Catches 1-char swap typos like 'recat' <-> 'react'."""
    n1, n2 = len(s1), len(s2)
    if n1 == 0: return n2
    if n2 == 0: return n1
    d = [[0] * (n2 + 1) for _ in range(n1 + 1)]
    for i in range(n1 + 1): d[i][0] = i
    for j in range(n2 + 1): d[0][j] = j
    for i in range(1, n1 + 1):
        for j in range(1, n2 + 1):
            cost = 0 if s1[i-1] == s2[j-1] else 1
            d[i][j] = min(d[i-1][j] + 1, d[i][j-1] + 1, d[i-1][j-1] + cost)
            if i > 1 and j > 1 and s1[i-1] == s2[j-2] and s1[i-2] == s2[j-1]:
                d[i][j] = min(d[i][j], d[i-2][j-2] + cost)
    return d[n1][n2]


_POPULAR_NPM = frozenset({
    "react", "vue", "angular", "express", "next", "lodash", "axios",
    "typescript", "webpack", "babel", "jquery", "moment", "underscore",
    "redux", "rxjs", "yargs", "chalk", "commander", "dotenv",
    "cross-env", "request", "node-fetch", "uuid", "jest", "mocha",
    "eslint", "prettier", "rollup", "vite", "tailwindcss",
    "fastify", "koa", "socket.io", "ws", "cors", "body-parser",
    "passport", "bcrypt", "jsonwebtoken", "mongoose", "sequelize",
    "nodemon", "pm2", "sharp", "minimist",
})

_POPULAR_PYPI = frozenset({
    "requests", "numpy", "pandas", "django", "flask", "boto3", "scipy",
    "matplotlib", "pytest", "fastapi", "sqlalchemy", "celery", "redis",
    "pillow", "lxml", "beautifulsoup4", "scrapy", "tornado",
    "tensorflow", "torch", "scikit-learn", "transformers", "langchain",
    "openai", "anthropic", "pydantic", "typer", "click", "rich",
    "httpx", "aiohttp", "uvicorn", "gunicorn", "alembic", "psycopg2",
    "pymongo", "pyyaml", "tomli", "cryptography", "urllib3",
    "pip", "setuptools", "wheel", "virtualenv",
})


def detect_typosquat(name, ecosystem):
    """If name is Damerau-Levenshtein distance 1 from a popular package
    (and not the popular package itself), return that package name.
    Otherwise None."""
    pop = _POPULAR_NPM if ecosystem == "npm" else _POPULAR_PYPI
    n = name.lower()
    if n in pop:
        return None
    for p in pop:
        if abs(len(p) - len(n)) > 2:
            continue
        if damerau_levenshtein(n, p) == 1:
            return p
    return None


def normalize_token(token):
    """Strip quotes and version specifiers from a single CLI token."""
    token = token.strip().strip('"').strip("'")
    if token.startswith("@"):
        # scoped npm: @scope/name@1.2.3 -> @scope/name
        slash = token.find("/")
        at = token.find("@", 1)
        if at != -1 and (slash == -1 or at > slash):
            token = token[:at]
    else:
        for sep in ("==", ">=", "<=", "~=", "@", "^", ">", "<"):
            i = token.find(sep)
            if i > 0:
                token = token[:i]
                break
    return token


# ----- Argument classification -------------------------------------------

def tokenize(s):
    return [t for t in re.split(r"[\s,;]+", s.strip()) if t]


def classify_args(tokens):
    """Returns (mode, payload, ecosystem_hint, error_msg)."""
    eco = None
    files, pkgs = [], []
    for t in tokens:
        if t == "--npm":
            eco = "npm"; continue
        if t == "--pip":
            eco = "pypi"; continue
        if os.path.isfile(t):
            files.append(t); continue
        if t.startswith("-") or "://" in t:
            continue
        n = normalize_token(t)
        if is_valid_name(n):
            pkgs.append(n)
    if len(files) >= 2:
        return ("error", None, eco, "エラー: 依存ファイルは1つまでしか指定できません。")
    if files and pkgs:
        return ("error", None, eco,
                "エラー: 依存ファイルとパッケージ名は同時指定できません。"
                "どちらか一方を指定してください。")
    if files:
        return ("file", files[0], eco, None)
    if pkgs:
        seen, uniq = set(), []
        for p in pkgs:
            k = p.lower()
            if k not in seen:
                seen.add(k); uniq.append(p)
        return ("name", uniq, eco, None)
    return ("empty", None, eco, None)


# ----- Manifest extraction ------------------------------------------------

def _parse_package_json(path):
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    deps = []
    for sec in ("dependencies", "devDependencies",
                "peerDependencies", "optionalDependencies"):
        for n in (d.get(sec) or {}).keys():
            deps.append(n)
    return "npm", deps


def _parse_requirements(path):
    deps = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            m = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*)", line)
            if m:
                deps.append(m.group(1))
    return "pypi", deps


def _load_toml(path):
    try:
        import tomllib
    except ImportError:
        import tomli as tomllib  # type: ignore
    with open(path, "rb") as f:
        return tomllib.load(f)


def _parse_toml_manifest(path):
    d = _load_toml(path)
    deps = []
    proj = d.get("project") or {}
    for spec in (proj.get("dependencies") or []):
        m = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*)", spec)
        if m: deps.append(m.group(1))
    for grp in (proj.get("optional-dependencies") or {}).values():
        for spec in grp:
            m = re.match(r"^([A-Za-z][A-Za-z0-9_.-]*)", spec)
            if m: deps.append(m.group(1))
    poetry = (d.get("tool") or {}).get("poetry") or {}
    for n in (poetry.get("dependencies") or {}).keys():
        if n.lower() != "python": deps.append(n)
    for n in (poetry.get("dev-dependencies") or {}).keys():
        if n.lower() != "python": deps.append(n)
    for grp in (poetry.get("group") or {}).values():
        for n in (grp.get("dependencies") or {}).keys():
            if n.lower() != "python": deps.append(n)
    for sec in ("packages", "dev-packages"):
        for n in (d.get(sec) or {}).keys():
            deps.append(n)
    return "pypi", deps


def extract_deps(path):
    base = os.path.basename(path).lower()
    if base == "package.json":
        return _parse_package_json(path)
    if base.startswith("requirements") and base.endswith(".txt"):
        return _parse_requirements(path)
    if base in ("pyproject.toml", "pipfile"):
        return _parse_toml_manifest(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        head = f.read(4096)
    if head.lstrip().startswith("{"):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and any(k in data for k in
                                              ("dependencies", "devDependencies")):
                return _parse_package_json(path)
        except Exception:
            pass
    if any(m in head for m in ("[project]", "[tool.poetry", "[packages]")):
        return _parse_toml_manifest(path)
    return _parse_requirements(path)


# ----- Fetchers -----------------------------------------------------------

def fetch_registry(name, ecosystem):
    if ecosystem == "npm":
        if name.startswith("@") and "/" in name:
            scope, rest = name.split("/", 1)
            url_name = scope + "%2F" + urllib.parse.quote(rest, safe="")
        else:
            url_name = urllib.parse.quote(name, safe="")
        url = f"https://registry.npmjs.org/{url_name}"
    else:
        url = f"https://pypi.org/pypi/{urllib.parse.quote(name, safe='')}/json"
    try:
        status, body = http_request(url)
    except (URLError, socket.timeout, ssl.SSLError, ValueError) as e:
        return {"fetch_error": str(e)}
    if status == 404:
        return {"not_found": True}
    try:
        d = json.loads(body)
    except json.JSONDecodeError:
        return {"not_found": True}  # PyPI returns HTML on 404 / npm error

    if ecosystem == "npm":
        if not isinstance(d, dict) or d.get("error") or not d.get("name"):
            return {"not_found": True}
        time_d = d.get("time", {}) or {}
        latest = (d.get("dist-tags") or {}).get("latest", "")
        repo = d.get("repository", {})
        repo_url = repo.get("url", "") if isinstance(repo, dict) else str(repo)

        # Original (first-publish) author — historical, often stale.
        raw_author = d.get("author", "")
        if isinstance(raw_author, dict):
            registry_author = raw_author.get("name", "") or raw_author.get("email", "")
        elif isinstance(raw_author, str):
            registry_author = raw_author
        else:
            registry_author = ""

        # Current maintainers (publish rights).
        maintainers_list = []
        for m in (d.get("maintainers") or []):
            if isinstance(m, dict):
                n = m.get("name") or m.get("email") or ""
                if n:
                    maintainers_list.append(n)

        # Who actually published the latest version (per-version metadata).
        latest_publisher = ""
        versions = d.get("versions") or {}
        if latest and isinstance(versions, dict) and latest in versions:
            npm_user = versions[latest].get("_npmUser") or {}
            if isinstance(npm_user, dict):
                latest_publisher = (npm_user.get("name", "")
                                    or npm_user.get("email", ""))

        return {
            "official_name": d.get("name", ""),
            "latest_version": latest,
            "registry_author": registry_author,
            "maintainers_list": maintainers_list,
            "latest_publisher": latest_publisher,
            "repository": repo_url,
            "first_release": (time_d.get("created") or "")[:10],
            "last_release": (time_d.get(latest) or "")[:10],
        }
    if not isinstance(d, dict) or "info" not in d:
        return {"not_found": True}
    i = d["info"] or {}
    releases = d.get("releases", {}) or {}
    dates = sorted(f["upload_time"]
                   for v in releases.values()
                   for f in (v or [])
                   if f and f.get("upload_time"))
    urls_d = i.get("project_urls") or {}
    repo_keys = ("Source", "Source Code", "Repository", "Code",
                 "GitHub", "Github", "github", "Homepage")
    repo = i.get("home_page") or ""
    for k in repo_keys:
        if not repo and urls_d.get(k):
            repo = urls_d[k]
    if "github.com" not in (repo or "").lower():
        for v in urls_d.values():
            if v and "github.com" in v.lower():
                repo = v
                break
    py_author = (i.get("author") or "").strip() or i.get("author_email", "")
    py_maint = (i.get("maintainer") or "").strip() or i.get("maintainer_email", "")
    return {
        "official_name": i.get("name", ""),
        "latest_version": i.get("version", ""),
        "registry_author": py_author,
        "maintainers_list": [py_maint] if py_maint and py_maint != py_author else [],
        "latest_publisher": "",  # PyPI does not expose per-release publisher in JSON API
        "repository": repo,
        "first_release": dates[0][:10] if dates else "unknown",
        "last_release": dates[-1][:10] if dates else "unknown",
    }


def fetch_osv(name, ecosystem):
    body = json.dumps({"package": {
        "name": name,
        "ecosystem": "PyPI" if ecosystem == "pypi" else "npm",
    }}).encode("utf-8")
    try:
        status, raw = http_request("https://api.osv.dev/v1/query",
                                   post_body=body,
                                   content_type="application/json")
    except (URLError, socket.timeout, ssl.SSLError, ValueError) as e:
        return {"error": str(e)}
    if status != 200:
        return {"error": f"http {status}"}
    try:
        d = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"json: {e}"}
    out = []
    for v in d.get("vulns", []) or []:
        sev = ""
        for s in v.get("severity", []) or []:
            sev = s.get("score", "") or sev
            if sev:
                break
        if not sev:
            ds = (v.get("database_specific") or {}).get("severity", "")
            sev = ds or "(未公開)"
        out.append({
            "id": v.get("id", ""),
            "summary": short(v.get("summary", ""), 80),
            "severity": sev,
            "aliases": v.get("aliases", []) or [],
            "withdrawn": v.get("withdrawn"),     # ISO datetime if retracted
            "affected": v.get("affected", []) or [],
        })
    return {"vulns": out}


def evaluate_patched_status(vulns, latest_version, name, ecosystem):
    """For each vuln, decide if `latest_version` is still vulnerable.
    Returns dict:
      - per_vuln: list of (id, status, detail) where status is one of
                  "patched" | "vulnerable" | "withdrawn" | "unknown"
      - overall: "all_patched" | "has_vulnerable" | "has_unknown"
                 has_vulnerable wins over has_unknown wins over all_patched.
    Fail-closed: if we can't decide a vuln's status, the overall is
    "has_unknown" (which the caller maps to CAUTION, not LOW SIGNAL)."""
    osv_eco = "PyPI" if ecosystem == "pypi" else "npm"
    per_vuln = []
    has_vuln = False
    has_unknown = False
    for v in vulns:
        vid = v.get("id", "")
        if v.get("withdrawn"):
            per_vuln.append((vid, "withdrawn", f"advisory withdrawn at {v['withdrawn'][:10]}"))
            continue
        applicable = []
        for aff in v.get("affected", []) or []:
            pkg = aff.get("package") or {}
            eco = (pkg.get("ecosystem") or "").lower()
            pname = (pkg.get("name") or "").lower()
            if eco and eco != osv_eco.lower():
                continue
            if pname and pname != name.lower():
                continue
            applicable.append(aff)
        if not applicable:
            per_vuln.append((vid, "unknown", "no matching affected entry for this package/ecosystem"))
            has_unknown = True
            continue
        in_range = False
        any_undecidable = False
        for aff in applicable:
            r = is_version_in_vulnerable_range(latest_version, aff.get("ranges", []) or [])
            if r is True:
                in_range = True
                break
            if r is None:
                any_undecidable = True
        if in_range:
            per_vuln.append((vid, "vulnerable", f"latest {latest_version} は脆弱範囲内"))
            has_vuln = True
        elif any_undecidable:
            per_vuln.append((vid, "unknown", "semver/range 解析不能"))
            has_unknown = True
        else:
            per_vuln.append((vid, "patched", f"latest {latest_version} は全 range 外"))
    if has_vuln:
        overall = "has_vulnerable"
    elif has_unknown:
        overall = "has_unknown"
    else:
        overall = "all_patched"
    return {"per_vuln": per_vuln, "overall": overall}


_GH_PATH_RE = re.compile(
    r"github\.com[:/]([^/\s]+/[^/\s?#]+?)(?:\.git)?(?:[/\s?#]|$)")


def extract_github_path(reg):
    return (lambda m: m.group(1) if m else "")(
        _GH_PATH_RE.search(reg.get("repository", "") or ""))


def fetch_github(owner_repo):
    if not owner_repo:
        return {"missing": True}
    headers = {}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"token {token}"
    try:
        status, raw = http_request(f"https://api.github.com/repos/{owner_repo}",
                                   headers=headers)
    except (URLError, socket.timeout, ssl.SSLError, ValueError) as e:
        return {"error": str(e)}
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "invalid json"}
    if status == 404:
        return {"error": "Not Found"}
    if status == 403:
        return {"error": "rate limit / forbidden"}
    if isinstance(d, dict) and "message" in d and status >= 400:
        return {"error": d["message"]}
    return {
        "stars": d.get("stargazers_count", ""),
        "pushed_at": (d.get("pushed_at") or "")[:10],
        "archived": bool(d.get("archived")),
        "owner_login": (d.get("owner") or {}).get("login", ""),
        "owner_type": (d.get("owner") or {}).get("type", ""),
    }


def fetch_downloads(name, ecosystem):
    """Returns weekly download count or {'error': str}."""
    if ecosystem == "npm":
        if name.startswith("@") and "/" in name:
            scope, rest = name.split("/", 1)
            url_name = scope + "%2F" + urllib.parse.quote(rest, safe="")
        else:
            url_name = urllib.parse.quote(name, safe="")
        url = f"https://api.npmjs.org/downloads/point/last-week/{url_name}"
    else:
        url = (f"https://pypistats.org/api/packages/"
               f"{urllib.parse.quote(name, safe='')}/recent?period=week")
    backoffs = (1.5, 4.0)
    status, raw = 0, b""
    for attempt in range(len(backoffs) + 1):
        try:
            status, raw = http_request(url)
        except (URLError, socket.timeout, ssl.SSLError, ValueError) as e:
            return {"error": str(e)}
        if status != 429 or attempt == len(backoffs):
            break
        time.sleep(backoffs[attempt])
    if status != 200:
        return {"error": f"http {status}"}
    try:
        d = json.loads(raw)
    except json.JSONDecodeError:
        return {"error": "invalid json"}
    if ecosystem == "npm":
        return {"weekly": d.get("downloads", 0)}
    data = d.get("data") or {}
    return {"weekly": data.get("last_week", 0)}


_MYTHOS = {"loaded": False, "data": None, "error": None}


def get_mythos_ledger():
    if _MYTHOS["loaded"]:
        return _MYTHOS["data"], _MYTHOS["error"]
    try:
        status, raw = http_request(
            "https://red.anthropic.com/2026/cvd/data/ledger.json")
    except (URLError, socket.timeout, ssl.SSLError, ValueError) as e:
        _MYTHOS.update(loaded=True, error=f"fetch failed: {e}")
        return None, _MYTHOS["error"]
    if status != 200:
        _MYTHOS.update(loaded=True, error=f"http {status}")
        return None, _MYTHOS["error"]
    try:
        _MYTHOS.update(loaded=True, data=json.loads(raw))
    except json.JSONDecodeError as e:
        _MYTHOS.update(loaded=True, error=f"parse: {e}")
    return _MYTHOS["data"], _MYTHOS["error"]


def match_mythos(name, owner_repo, ledger):
    if not ledger:
        return []
    needles = {n.lower() for n in (name, owner_repo) if n}
    entries = ledger if isinstance(ledger, list) else ledger.get("ledger", [])
    matches = []
    for e in entries:
        proj = e.get("project") or ""
        if not proj:
            continue
        pl = str(proj).lower()
        if pl in needles or pl.rsplit("/", 1)[-1] in needles:
            matches.append(e)
    return matches


# ----- Risk classification -----------------------------------------------

def classify_maintenance(last_release, archived):
    if archived:
        return "ABANDONED"
    if not last_release or last_release == "unknown":
        return "UNKNOWN"
    try:
        last = datetime.strptime(last_release, "%Y-%m-%d")
    except ValueError:
        return "UNKNOWN"
    months = (datetime.now() - last).days / 30
    if months < 6: return "ACTIVE"
    if months < 12: return "MODERATE"
    if months < 24: return "LOW"
    return "ABANDONED"


_MALWARE_KEYWORDS = ("malware", "malicious", "backdoor", "trojan",
                     "is malware", "typosquat", "typo-squat", "credential steal",
                     "data exfil", "supply chain attack")


def malware_signals(registry, osv, gh_path):
    """Detect strong indicators of confirmed-malicious packages.
    Returns a list of human-readable reason strings (empty if none)."""
    reasons = []
    for v in osv.get("vulns", []) or []:
        s = (v.get("summary") or "").lower()
        for kw in _MALWARE_KEYWORDS:
            if kw in s:
                reasons.append(f"OSV summary に '{kw}' を含む ({v.get('id', '')})")
                break
    repo = (registry.get("repository") or "").lower()
    if "npm/security-holder" in repo:
        reasons.append("リポジトリが npm/security-holder (npm が押収・保管)")
    if (gh_path or "").lower() == "npm/security-holder":
        reasons.append("GitHub URL が npm/security-holder")
    ver = (registry.get("latest_version") or "").lower()
    if ver.endswith("-security") or "-security" in ver.split("+", 1)[0]:
        reasons.append(f"バージョン '{registry.get('latest_version')}' が "
                       "npm security-holder 命名規約に一致")
    return reasons


_REC_LOW = "利用前に人間レビュー必須（LOW SIGNAL は安全保証ではない）"
_REC_CAUTION = "INSTALL WITH CAUTION"
_REC_HIGH = "DO NOT INSTALL"


def classify_risk(input_name, ecosystem, name_match, registry, osv, gh,
                  mythos_matches, maintenance, gh_path, patch_eval):
    suggestion = detect_typosquat(input_name, ecosystem)
    mw_sigs = malware_signals(registry, osv, gh_path)
    # Soften typosquat language: state observation only, not malice.
    typo_sig = ([f"人気パッケージ '{suggestion}' と Damerau-Levenshtein 距離 1。"
                 f"タイポ・個人実験・abandonware・悪意の区別はこのツールでは不可"]
                if suggestion else [])
    sigs = mw_sigs + typo_sig

    if not name_match or registry.get("not_found"):
        return "HIGH RISK", _REC_HIGH, sigs, suggestion

    if mw_sigs:
        return "HIGH RISK", _REC_HIGH, sigs, suggestion

    weak_evidence = (
        not (registry.get("repository") or "").strip()
        or maintenance == "ABANDONED"
        or (registry.get("latest_version") or "").startswith("0.0.")
    )
    if suggestion and weak_evidence:
        return "HIGH RISK", _REC_HIGH, sigs, suggestion

    if osv.get("error"):
        return "UNKNOWN", _REC_HIGH, sigs, suggestion

    # New: use range-aware patched_status. has_unknown is fail-closed CAUTION.
    if patch_eval is not None:
        overall = patch_eval.get("overall")
        if overall == "has_vulnerable":
            return "CAUTION", _REC_CAUTION, sigs, suggestion
        if overall == "has_unknown":
            return "CAUTION", _REC_CAUTION, sigs, suggestion
        # all_patched → fall through (no security-driven escalation)

    for m in mythos_matches:
        sev = (m.get("claude_severity") or m.get("maintainer_severity")
               or m.get("vendor_severity") or "").lower()
        if not m.get("patched") and sev in ("critical", "high"):
            return "CAUTION", _REC_CAUTION, sigs, suggestion
    if gh.get("archived") or maintenance == "ABANDONED":
        return "CAUTION", _REC_CAUTION, sigs, suggestion
    ver = (registry.get("latest_version") or "").strip()
    if ver.startswith("0.0.") and maintenance in ("LOW", "MODERATE", "ABANDONED"):
        return "CAUTION", _REC_CAUTION, sigs, suggestion
    return "LOW SIGNAL", _REC_LOW, sigs, suggestion


# ----- Reporting ---------------------------------------------------------

def _registry_url(input_name, ecosystem):
    return (f"https://www.npmjs.com/package/{input_name}" if ecosystem == "npm"
            else f"https://pypi.org/project/{input_name}/")


_RISK_LABEL = {
    "LOW SIGNAL": "LOW SIGNAL (現時点で強いリスクシグナルは未検出。安全保証ではない)",
    "CAUTION": "CAUTION",
    "HIGH RISK": "HIGH RISK",
    "UNKNOWN": "UNKNOWN",
}


def format_block(input_name, ecosystem, registry, osv, gh, gh_path,
                 mythos_matches, mythos_err, name_match, retrieved_on,
                 downloads, patch_eval, idx=None, total=None):
    L = []
    if idx and total:
        header = f"個別パッケージ調査結果 [{idx}/{total}] {input_name}"
    else:
        header = "個別パッケージ調査結果"
    L.append("-" * 30)
    L.append(header)
    L.append("-" * 30)
    L.append("")
    L.append(f"パッケージ (入力): {input_name}")
    L.append("")
    L.append("名前照合検証:")
    L.append(f" 入力名: {input_name}")
    L.append(f" 公式名: {registry.get('official_name', '')}")
    L.append(f" 照合結果: {'MATCH' if name_match else 'NOT MATCH'}")
    L.append("")

    if registry.get("not_found"):
        L.append("公式レジストリ:")
        L.append(f" URL: {_registry_url(input_name, ecosystem)}")
        L.append(" 状態: NOT FOUND（レジストリに存在しない — typosquat の可能性）")
        L.append("")
        L.append("リスクレベル:")
        L.append(" HIGH RISK")
        L.append("")
        L.append("推奨:")
        L.append(" DO NOT INSTALL")
        L.append("")
        return "\n".join(L), "HIGH RISK", "DO NOT INSTALL"

    if registry.get("fetch_error"):
        L.append("公式レジストリ:")
        L.append(f" URL: {_registry_url(input_name, ecosystem)}")
        L.append(f" 状態: FETCH FAILED ({registry['fetch_error']})")
        L.append("")
        L.append("リスクレベル:")
        L.append(" UNKNOWN")
        L.append("")
        L.append("推奨:")
        L.append(" DO NOT INSTALL")
        L.append("")
        return "\n".join(L), "UNKNOWN", "DO NOT INSTALL"

    L.append("公式レジストリ:")
    L.append(f" URL: {_registry_url(input_name, ecosystem)}")
    L.append(f" Registry author (歴史的、現 publisher と異なる場合あり): "
             f"{registry.get('registry_author') or 'NOT_CONFIRMED'}")
    mlist = registry.get("maintainers_list") or []
    if mlist:
        head = ", ".join(mlist[:5])
        more = f" ほか {len(mlist)-5} 名" if len(mlist) > 5 else ""
        L.append(f" 現 maintainers ({len(mlist)}名): {head}{more}")
    else:
        L.append(" 現 maintainers: NOT_CONFIRMED")
    lp = registry.get("latest_publisher")
    if lp:
        in_maint = lp in mlist
        tag = "maintainers と一致" if in_maint else "⚠ maintainers 一覧に含まれず (要確認)"
        L.append(f" 最新版 publisher: {lp} ({tag})")
    elif ecosystem == "npm":
        L.append(" 最新版 publisher: NOT_CONFIRMED")
    L.append(f" 最新バージョン: {registry.get('latest_version') or 'NOT_CONFIRMED'}")
    L.append(f" 初回リリース: {registry.get('first_release') or 'NOT_CONFIRMED'}")
    L.append(f" 最終リリース: {registry.get('last_release') or 'NOT_CONFIRMED'}")
    L.append("")

    L.append("ソースリポジトリ:")
    if not gh_path:
        L.append(" URL: NOT_CONFIRMED (GitHub URL を特定できず)")
        L.append(" オーナー: NOT_CONFIRMED")
        L.append(f" スター数: NOT_CONFIRMED (取得日: {retrieved_on}、出典: GitHub API)")
        L.append(" 最終push日: NOT_CONFIRMED")
        L.append(" アーカイブ済み: false")
    elif gh.get("error"):
        L.append(f" URL: https://github.com/{gh_path}")
        L.append(f" オーナー: NOT_CONFIRMED ({gh['error']})")
        L.append(f" スター数: NOT_CONFIRMED (取得日: {retrieved_on}、出典: GitHub API、{gh['error']})")
        L.append(" 最終push日: NOT_CONFIRMED")
        L.append(" アーカイブ済み: false")
    else:
        L.append(f" URL: https://github.com/{gh_path}")
        L.append(f" オーナー: {gh.get('owner_login', '')}（{gh.get('owner_type', '')}）")
        L.append(f" スター数: {gh.get('stars', '')} (取得日: {retrieved_on}、出典: GitHub API)")
        L.append(f" 最終push日: {gh.get('pushed_at', '')}")
        L.append(f" アーカイブ済み: {'true' if gh.get('archived') else 'false'}")
    L.append("")

    maint = classify_maintenance(registry.get("last_release", ""),
                                 bool(gh.get("archived")))
    L.append("メンテナンス状況:")
    L.append(f" {maint}")
    L.append("")

    L.append("採用実績:")
    if downloads.get("error"):
        src = "api.npmjs.org" if ecosystem == "npm" else "pypistats.org"
        L.append(f" 週間ダウンロード: NOT_CONFIRMED ({downloads['error']}, 出典: {src})")
    else:
        n = downloads.get("weekly", 0)
        src = "api.npmjs.org" if ecosystem == "npm" else "pypistats.org"
        L.append(f" 週間ダウンロード: {n:,} (取得日: {retrieved_on}、出典: {src})")
    L.append(" Dependents: NOT_CONFIRMED (本ランナーでは未取得)")
    L.append("")

    eco_q = "PyPI" if ecosystem == "pypi" else "npm"
    osv_url = (f"https://osv.dev/list?ecosystem={eco_q}"
               f"&q={urllib.parse.quote(input_name)}")
    L.append("セキュリティ:")
    L.append(f" OSV: {osv_url}")
    if osv.get("error"):
        L.append(f" CVEs: UNKNOWN ({osv['error']})")
    else:
        vulns = osv.get("vulns", [])
        L.append(f" CVEs: {len(vulns)}")
        # Build a lookup from vuln id to (status, detail) for display.
        status_map = {}
        if patch_eval:
            for vid, st, det in patch_eval.get("per_vuln", []):
                status_map[vid] = (st, det)
        n_patched = n_vuln = n_unknown = n_withdrawn = 0
        for v in vulns[:10]:
            tail = (f" | aliases: {', '.join(v['aliases'])}"
                    if v["aliases"] else "")
            st_det = status_map.get(v["id"], ("unknown", "no eval"))
            st = st_det[0]
            label = {"patched": "修正済み (latest 適用)",
                     "vulnerable": "⚠ latest でも脆弱",
                     "withdrawn": "advisory withdrawn",
                     "unknown": "判定不能"}[st]
            L.append(f"   - {v['id']} | {v['summary']} "
                     f"| severity: {v['severity']}{tail}")
            L.append(f"     状態: {label} ({st_det[1]})")
        # Tally over ALL vulns (not just shown 10)
        for vid, st, _ in (patch_eval or {}).get("per_vuln", []):
            if st == "patched": n_patched += 1
            elif st == "vulnerable": n_vuln += 1
            elif st == "withdrawn": n_withdrawn += 1
            elif st == "unknown": n_unknown += 1
        if vulns and patch_eval:
            ov = patch_eval.get("overall")
            tag = {"all_patched": f"全 {len(vulns)} 件が patched / withdrawn (latest "
                                  f"{registry.get('latest_version','')} は影響外)",
                   "has_vulnerable": f"latest でもまだ脆弱: {n_vuln} 件 / "
                                     f"patched: {n_patched} / unknown: {n_unknown} / "
                                     f"withdrawn: {n_withdrawn}",
                   "has_unknown": f"判定不能: {n_unknown} 件 / patched: {n_patched} / "
                                  f"withdrawn: {n_withdrawn} (fail-closed: CAUTION)"}[ov]
            L.append(f"   → 評価: {tag}")

    if mythos_err:
        L.append(f" Claude Mythos: NOT_CONFIRMED ({mythos_err})")
    elif not mythos_matches:
        L.append(" Claude Mythos: なし")
    else:
        L.append(f" Claude Mythos: {len(mythos_matches)} 件 "
                 f"(人間が project 名を必ず確認)")
        for m in mythos_matches:
            sev = (m.get("claude_severity") or m.get("maintainer_severity")
                   or m.get("vendor_severity") or "")
            cves = ",".join(m.get("cve_ids") or [])
            ghsas = ",".join(m.get("ghsa_ids") or [])
            ids = "/".join(x for x in (cves, ghsas) if x) or "-"
            ant = m.get("ant_id", "")
            L.append(f"   - {ant} | {m.get('project','')} "
                     f"| {m.get('bug_class','')} | severity: {sev}")
            L.append(f"     ids: {ids} | patched: {m.get('patched', False)} "
                     f"| status: {m.get('status', '')}")
            if ant:
                L.append(f"     URL: https://red.anthropic.com/2026/cvd/findings/{ant}.html")
    L.append("")

    risk, rec, sigs, suggestion = classify_risk(
        input_name, ecosystem, name_match, registry, osv, gh,
        mythos_matches, maint, gh_path, patch_eval)
    L.append("リスクレベル:")
    L.append(f" {_RISK_LABEL.get(risk, risk)}")
    if sigs:
        L.append(" 検出されたシグナル:")
        for s in sigs:
            L.append(f"   - {s}")
    L.append("")
    L.append("推奨:")
    L.append(f" {rec}")
    if suggestion:
        L.append(f" 類似する人気パッケージ: '{suggestion}'。"
                 f"意図したものがこれであれば '{suggestion}' を使用してください")
    L.append("")
    return "\n".join(L), risk, rec


# ----- Main flow ---------------------------------------------------------

_NPM_HINT = {"react", "express", "lodash", "vue", "next", "axios",
             "left-pad", "jquery", "webpack", "typescript"}
_PY_HINT = {"requests", "numpy", "pandas", "django", "flask", "boto3",
            "scipy", "matplotlib", "pytest", "fastapi"}


def detect_ecosystem(names, hint):
    """Returns (ecosystem, defaulted, source). source is a human string
    describing how the ecosystem was decided."""
    if hint:
        return hint, False, "--pip/--npm フラグ"
    for n in names:
        if n.startswith("@"):
            return "npm", False, f"scoped npm name '{n}'"
        if n.lower() in _NPM_HINT:
            return "npm", False, f"名前ヒント '{n}'"
        if n.lower() in _PY_HINT:
            return "pypi", False, f"名前ヒント '{n}'"
    return "pypi", True, "デフォルト (どちらとも判定できず)"


def run_audit(names, ecosystem, defaulted, eco_source):
    retrieved_on = today_str()
    ledger, mythos_err = get_mythos_ledger()
    blocks = []
    summary = {"LOW SIGNAL": [], "CAUTION": [], "HIGH RISK": [], "UNKNOWN": []}
    recs = {"REVIEW THEN ALLOW": [], "BLOCK": []}

    print(f"[ecosystem: {ecosystem} ({eco_source})]")
    if defaulted:
        print(f"[ヒント: --pip / --npm を明示するか、依存ファイルを渡せば自動判定できます]")
    print("")

    total = len(names)
    for idx, name in enumerate(names, 1):
        print(f"[{idx}/{total}] {name}", file=sys.stderr, flush=True)
        if not is_valid_name(name):
            blocks.append(f"パッケージ: {name}\n  エラー: 不正な文字列。"
                          f"スキップしました。\n  リスクレベル: UNKNOWN\n"
                          f"  推奨: DO NOT INSTALL\n")
            summary["UNKNOWN"].append(name); recs["BLOCK"].append(name)
            continue

        registry = fetch_registry(name, ecosystem)
        official = registry.get("official_name", "")
        name_match = (official.lower() == name.lower()) if official else False

        if registry.get("not_found") or registry.get("fetch_error"):
            block, risk, rec = format_block(
                name, ecosystem, registry, {}, {"missing": True}, "",
                [], None, name_match, retrieved_on, {"error": "skipped"}, None,
                idx=idx, total=total)
            blocks.append(block)
            summary[risk].append(name); recs["BLOCK"].append(name)
            continue

        osv = fetch_osv(name, ecosystem)
        gh_path = extract_github_path(registry)
        gh = fetch_github(gh_path)
        mm = match_mythos(name, gh_path, ledger) if ledger is not None else []
        downloads = fetch_downloads(name, ecosystem)
        patch_eval = None
        if osv.get("vulns"):
            patch_eval = evaluate_patched_status(
                osv["vulns"], registry.get("latest_version", ""),
                official or name, ecosystem)

        block, risk, rec = format_block(
            name, ecosystem, registry, osv, gh, gh_path,
            mm, mythos_err, name_match, retrieved_on, downloads, patch_eval,
            idx=idx, total=total)
        blocks.append(block)
        summary[risk].append(name)
        if risk == "LOW SIGNAL":
            recs["REVIEW THEN ALLOW"].append(name)
        else:
            recs["BLOCK"].append(name)

    out = list(blocks)
    out.append("-" * 30)
    out.append("FINAL SUMMARY")
    out.append("-" * 30)
    out.append("")
    out.append("【リスク分類】")
    for k in ("LOW SIGNAL", "CAUTION", "HIGH RISK", "UNKNOWN"):
        label = _RISK_LABEL.get(k, k)
        out.append(f"{label}:")
        out.extend([f"- {n}" for n in summary[k]] or ["- (なし)"])
        out.append("")
    out.append("【推奨アクション】")
    out.append("REVIEW THEN ALLOW (人間レビュー後に利用可。ただし安全保証ではない):")
    out.extend([f"- {n}" for n in recs["REVIEW THEN ALLOW"]] or ["- (なし)"])
    out.append("")
    out.append("BLOCK (インストール禁止または要強い確認):")
    out.extend([f"- {n}" for n in recs["BLOCK"]] or ["- (なし)"])
    out.append("")
    out.append("=" * 50)
    return "\n".join(out)


_USAGE = """\
usage: runner.py [--pip|--npm] <package>...
       runner.py <manifest-file>

Examples:
  runner.py react express
  runner.py --pip requests numpy
  runner.py path/to/requirements.txt
"""


def main(argv):
    if argv and argv[0] in ("-h", "--help"):
        print(_USAGE); return 0
    tokens = tokenize(" ".join(argv))
    mode, payload, eco_hint, err = classify_args(tokens)

    if err:
        print(err); return 2

    if mode == "empty":
        print("MODE: empty_args")
        print("引数が指定されていません。会話文脈から候補を推測してください。")
        return 0

    if mode == "file":
        try:
            file_eco, deps = extract_deps(payload)
        except Exception as e:
            print(f'エラー: 依存ファイル "{payload}" を解析できませんでした。 ({e})')
            return 2
        if not deps:
            print(f'エラー: 依存ファイル "{payload}" から依存パッケージを抽出できませんでした。')
            return 2
        seen, names = set(), []
        for n in deps:
            k = n.lower()
            if k not in seen and k != "python":
                seen.add(k); names.append(n)
        ecosystem, defaulted, eco_source = (
            file_eco, False, f"manifest '{os.path.basename(payload)}'")
    else:
        names = payload
        ecosystem, defaulted, eco_source = detect_ecosystem(names, eco_hint)

    report = run_audit(names, ecosystem, defaulted, eco_source)
    print(report)
    save_msg = save_report(report)
    if save_msg:
        print(save_msg)
    return 0


def save_report(report):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir = Path.cwd() / "pkgcheck-reports"
    out_path = out_dir / f"pkgcheck-{ts}.md"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_text(report, encoding="utf-8")
    except OSError as e:
        return f"\n[警告: フルレポートのファイル保存に失敗しました: {e}]"
    return f"\n[フルレポート保存先: {out_path}]"


if __name__ == "__main__":
    # Force UTF-8 stdout. On Windows this prevents mojibake when the
    # console is set to cp932; on macOS/Linux stdout is already UTF-8 so
    # the call is a harmless no-op. Wrapped in try/except for older
    # Pythons that lack reconfigure().
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.exit(main(sys.argv[1:]))
