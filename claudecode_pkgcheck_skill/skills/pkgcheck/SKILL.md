---
name: pkgcheck
description: Evidence-based security audit for Python/Node packages. Prevents typosquatting and supply chain risks.
argument-hint: "[package-names...]"
allowed-tools:
  - Bash(curl -s *pypi.org*)
  - Bash(curl -s *api.github.com*)
  - Bash(curl -s *osv.dev*)
  - Bash(curl -s -X POST *osv.dev*)
  - Bash(curl -s *registry.npmjs.org*)
  - Bash(python pkgcheck_tmp/pkgcheck_*.py*)
  - Bash(rm -f pkgcheck_tmp/pkgcheck_*)
---

# pkgcheck

## Runtime Policy

- `pip install` is prohibited
- `npm install` is prohibited
- Use Python standard library only
- External network access must use `curl` only
- Do not write files outside the temporary directory

## Temporary File Policy

```
TMP_DIR="./pkgcheck_tmp"
mkdir -p "$TMP_DIR"
```

Forbidden paths:
- C:\Windows
- C:\Windows\System32
- user home directory root

---


## INPUT HANDLING

Packages to check: $ARGUMENTS

If `$ARGUMENTS` is empty, infer package candidates from recent conversation context and suggest them instead of running checks.

Inference scope (priority order):
1. latest user message
2. previous 1-3 user messages
3. recent assistant messages only when they quote user-provided package names

Use strong signals only:
- package names in backticks/quotes
- install commands (`pip install`, `uv add`, `npm i`, `npm install`, `pnpm add`, `yarn add`)
- dependency snippets from `requirements.txt`, `pyproject.toml`, `package.json`

Filtering rules:
- remove version suffixes (`==1.2.3`, `@1.2.3`) and CLI flags (`-U`, `--dev`)
- ignore file paths, URLs, and generic nouns
- deduplicate while preserving recency order
- suggest up to 5 package names

If candidates exist, output in Japanese:

pkgcheck コマンドが引数なしで実行されました。
直近の会話文脈から候補を推測しました:
- <candidate1>
- <candidate2>

候補が正しければ、次を実行してください:
`/pkgcheck <candidate1> <candidate2>`

候補が違う場合は、確認したいパッケージ名を指定して再実行してください。

After this suggestion response, STOP. Do not fetch registry/security data.

If no reliable candidate exists, output EXACTLY:

pkgcheck コマンドが引数なしで実行されましたが、会話文脈から候補を特定できませんでした。
パッケージ名を指定してください。

---

## ECOSYSTEM DETECTION

Determine ecosystem:

1. --npm flag → npm
2. --pip flag → PyPI
3. obvious Node → npm
4. obvious Python → PyPI
5. otherwise → assume PyPI (must state)

---

## FETCH MODEL

Use the method most likely to return complete, structured data.
Priority order (highest first):

1. **Bash (curl + Python script file)** — for registry JSON and OSV API
2. **MCP HTTP / Tavily Extract** — fallback if Bash is unavailable
3. **WebFetch / search** — last resort

Avoid piping large JSON directly into `python -c` — shell quoting and
encoding issues on Windows can corrupt the input. Use the file-based
pattern below instead.

## COMMAND EXECUTION STYLE

To reduce security confirmation prompts, execute **one shell command per tool call**.

- Do NOT chain commands with `&&`, `||`, `;`, or pipes
- Do NOT use command substitution like `$()`
- Do NOT use inline shell conditionals like `if ... fi`
- Do NOT add debug separators such as `echo "---OSV---"`
- Read the previous command output, then run the next command

The tool MUST NOT affect logic.

---

## FETCH RULES

Success = required fields extracted from reliable source

Failure types:

- 404 → HIGH RISK
- network / parse failure → UNKNOWN
- partial data → use "NOT_CONFIRMED"

---

## REQUIRED SOURCES

### SETUP — write helper scripts (once per pkgcheck invocation)

Use the **Write tool** to create these helper scripts before fetching packages.
Place them in `pkgcheck_tmp/` under the current working directory.

**pkgcheck_tmp/pkgcheck_registry.py**
```python
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
with open(sys.argv[1], encoding='utf-8') as f:
    d = json.load(f)
ecosystem = sys.argv[2] if len(sys.argv) > 2 else 'pypi'
if ecosystem == 'npm':
    time = d.get('time', {})
    latest = d.get('dist-tags', {}).get('latest', '')
    repo = d.get('repository', {})
    repo_url = repo.get('url', '') if isinstance(repo, dict) else str(repo)
    author = d.get('author', '')
    if isinstance(author, dict):
        author = author.get('name', '') or author.get('email', '')
    print('official_name:', d.get('name', ''))
    print('latest_version:', latest)
    print('author:', str(author))
    print('repository:', repo_url)
    print('first_release:', time.get('created', '')[:10])
    print('last_release:', time.get(latest, '')[:10])
else:
    i = d['info']
    releases = d.get('releases', {})
    dates = sorted(f2['upload_time'] for v in releases.values() for f2 in v if f2)
    urls = i.get('project_urls') or {}
    repo = i.get('home_page') or urls.get('Homepage') or urls.get('Source') or ''
    print('official_name:', i['name'])
    print('latest_version:', i['version'])
    print('author:', (i.get('author') or '').strip() or i.get('author_email', ''))
    print('repository:', repo)
    print('first_release:', dates[0][:10] if dates else 'unknown')
    print('last_release:', dates[-1][:10] if dates else 'unknown')
```

**pkgcheck_tmp/pkgcheck_osv.py**
```python
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
with open(sys.argv[1], encoding='utf-8') as f:
    d = json.load(f)
vulns = d.get('vulns', [])
print('vuln_count:', len(vulns))
for v in vulns:
    sev = next((s.get('score', '') for s in v.get('severity', [])), '')
    aliases = ', '.join(v.get('aliases', []))
    print(' -', v['id'], '|', v.get('summary', '')[:80], '| severity:', sev, '| aliases:', aliases)
```

**pkgcheck_tmp/pkgcheck_github.py**
```python
import json, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
with open(sys.argv[1], encoding='utf-8') as f:
    d = json.load(f)
if 'message' in d:
    print('github_error:', d['message'])
else:
    print('stars:', d.get('stargazers_count', ''))
    print('pushed_at:', (d.get('pushed_at') or '')[:10])
    print('archived:', d.get('archived', False))
    print('owner_login:', d.get('owner', {}).get('login', ''))
    print('owner_type:', d.get('owner', {}).get('type', ''))
```

**pkgcheck_tmp/pkgcheck_gh_path.py**
```python
import json, re, sys
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
try:
    d = json.load(open(sys.argv[1], encoding='utf-8'))
    raw = d.get('repository', {})
    url = raw.get('url', '') if isinstance(raw, dict) else str(raw)
    if not url:
        info = d.get('info', {})
        urls = info.get('project_urls') or {}
        url = (info.get('home_page') or urls.get('Source') or urls.get('Homepage') or '')
    m = re.search(r'github\.com[:/]([^/\s]+/[^/\s]+?)(?:\.git)?\s*$', url)
    print(m.group(1) if m else '')
except Exception:
    pass
```

### PyPI — Registry

PyPI JSON responses include all historical release metadata and can exceed
several MB for active packages. Do NOT pass the URL to HTML extraction
tools — they will truncate the response.

```bash
curl -s "https://pypi.org/pypi/<name>/json" -o pkgcheck_tmp/pkgcheck_pkg.json
python pkgcheck_tmp/pkgcheck_registry.py pkgcheck_tmp/pkgcheck_pkg.json pypi
```

Fallback (Bash unavailable): fetch https://pypi.org/pypi/<name>/json via
Tavily Extract — accept partial data; mark truncated fields as NOT_CONFIRMED.

### PyPI — Security (OSV REST API)

```bash
curl -s -X POST "https://api.osv.dev/v1/query" -H "Content-Type: application/json" -d "{\"package\": {\"name\": \"<name>\", \"ecosystem\": \"PyPI\"}}" -o pkgcheck_tmp/pkgcheck_osv.json
python pkgcheck_tmp/pkgcheck_osv.py pkgcheck_tmp/pkgcheck_osv.json
```

Fallback: https://osv.dev/list?ecosystem=PyPI&q=<name> via Tavily Extract.

### npm — Registry

```bash
curl -s "https://registry.npmjs.org/<name>" -o pkgcheck_tmp/pkgcheck_pkg.json
python pkgcheck_tmp/pkgcheck_registry.py pkgcheck_tmp/pkgcheck_pkg.json npm
```

### npm — Security (OSV REST API)

```bash
curl -s -X POST "https://api.osv.dev/v1/query" -H "Content-Type: application/json" -d "{\"package\": {\"name\": \"<name>\", \"ecosystem\": \"npm\"}}" -o pkgcheck_tmp/pkgcheck_osv.json
python pkgcheck_tmp/pkgcheck_osv.py pkgcheck_tmp/pkgcheck_osv.json
```

Scoped packages must be URL encoded (@scope%2Fpkg)

### GitHub — Repository Metadata (npm and PyPI)

Run this **after** the registry fetch for each package, while `pkgcheck_tmp/pkgcheck_pkg.json`
is still present. Extracts the GitHub owner/repo from the registry data and
fetches stars, last push date, archived status, and owner identity.

NOTE: Do NOT use inline `python -c` with multiline strings — shell quoting
on Windows corrupts them. Use the `pkgcheck_tmp/pkgcheck_gh_path.py` script file instead.

```bash
python pkgcheck_tmp/pkgcheck_gh_path.py pkgcheck_tmp/pkgcheck_pkg.json
curl -s "https://api.github.com/repos/<owner>/<repo>" -o pkgcheck_tmp/pkgcheck_gh.json
python pkgcheck_tmp/pkgcheck_github.py pkgcheck_tmp/pkgcheck_gh.json
```

If `pkgcheck_tmp/pkgcheck_gh_path.py` outputs an empty string, skip the GitHub API call and set:
- `stars: NOT_CONFIRMED (GitHub repository URL missing)`
- `pushed_at: NOT_CONFIRMED`
- `archived: false`

Error handling:
- `github_error: Not Found` → repository is missing or deleted; record as an additional risk signal
- `github_error: API rate limit exceeded` → set `stars` and `pushed_at` to `NOT_CONFIRMED` (GitHub API rate limit).
  Set `GITHUB_TOKEN` to increase the GitHub API limit to 5,000 req/hour:
  `curl -s -H "Authorization: token $GITHUB_TOKEN" "https://api.github.com/repos/<owner>/<repo>" -o pkgcheck_tmp/pkgcheck_gh.json`

Output fields:
- `stars` — stargazers_count
- `pushed_at` — date of last push (YYYY-MM-DD)
- `archived` — true if the repo is archived (treat as ABANDONED regardless of release date)
- `owner_login` — GitHub username/org (use to verify publisher identity)
- `owner_type` — "Organization" or "User"

### CLEANUP — after all packages are checked

```bash
rm -f pkgcheck_tmp/pkgcheck_pkg.json pkgcheck_tmp/pkgcheck_osv.json pkgcheck_tmp/pkgcheck_gh.json pkgcheck_tmp/pkgcheck_registry.py pkgcheck_tmp/pkgcheck_osv.py pkgcheck_tmp/pkgcheck_github.py pkgcheck_tmp/pkgcheck_gh_path.py
```

---

## SEARCH TOOL RULE

Search-based tools must:

- identify original URL
- not rely only on summaries

Otherwise → UNKNOWN

---

## DISPLAY URL POLICY

ALWAYS output human-friendly URLs:

PyPI:
https://pypi.org/project/<name>/

npm:
https://www.npmjs.com/package/<name>

GitHub:
https://github.com/<owner>/<repo>

OSV:
https://osv.dev/list?ecosystem=<ecosystem>&q=<name>

DO NOT display API endpoints.

---

## NUMERIC RULE

Format:

<value> (retrieved_on: YYYY-MM-DD, source: source)

If unavailable:

NOT_CONFIRMED (reason)

No guessing.

---

## EXACT NAME MATCH (CRITICAL)

Compare input vs registry name.

Allowed:
- case difference

NOT allowed:
- hyphen difference
- underscore difference
- spelling difference

If NOT MATCH:

→ HIGH RISK  
→ STOP processing

---

## CORE EVIDENCE

Collect:

1. registry data
2. repository
3. maintenance
4. adoption evidence (≥1)
5. security evidence

---

## MAINTENANCE

Based on latest release:

ACTIVE <6 months  
MODERATE 6–12  
LOW 12–24  
ABANDONED >24 or archived  

---

## ADOPTION

At least one:

- downloads
- dependents
- widely used
- official docs

If unavailable:

NOT_CONFIRMED

---

## SECURITY

OSV is primary.

If:

- fetch fails → UNKNOWN
- parse fails → UNKNOWN

“None found” only if verified.

---

## SUSPICIOUS SIGNALS

- very recent package (<2 weeks)
- low stars (<50)
- archived: true → treat as ABANDONED regardless of release date
- owner_login mismatch: publisher on registry differs from GitHub org/user

Only include if confirmed.

---

## FAIL-CLOSED

Missing evidence → UNKNOWN

UNKNOWN → DO NOT INSTALL

---

## RISK CLASSIFICATION

LOW RISK:
all conditions satisfied

CAUTION:
legitimate but weak signals

HIGH RISK:
- name mismatch
- 404
- confirmed malicious

UNKNOWN:
insufficient evidence

---

## OUTPUT FORMAT（日本語）

==================================================
パッケージセキュリティ監査レポート
==================================================

パッケージ (入力): <name>

名前照合検証:
 入力名: <input>
 公式名: <official>
 照合結果: MATCH / NOT MATCH

公式レジストリ:
 URL: <表示用URL>
 パブリッシャー: <name>
 最新バージョン: <version>
 初回リリース: <date>
 最終リリース: <date>

ソースリポジトリ:
 URL: <GitHub URL>
 オーナー: <owner_login>（<owner_type>）
 スター数: <value or NOT_CONFIRMED>（取得日: YYYY-MM-DD、出典: GitHub API）
 最終push日: <pushed_at or NOT_CONFIRMED>
 アーカイブ済み: true / false

メンテナンス状況:
 ACTIVE / MODERATE / LOW / ABANDONED

採用実績:
 ダウンロード: <value or NOT_CONFIRMED>
 Dependents: <value or NOT_CONFIRMED>

セキュリティ:
 OSV: <URL>
 CVEs: <count or NOT_CONFIRMED or UNKNOWN>

リスクレベル:
 LOW RISK / CAUTION / HIGH RISK / UNKNOWN

推奨:
 INSTALL（人間レビュー後）
 INSTALL WITH CAUTION
 DO NOT INSTALL

---

## FINAL SUMMARY（日本語）

==================================================
FINAL SUMMARY
==================================================

【リスク分類】
LOW RISK:
- xxx

CAUTION:
- xxx

HIGH RISK:
- xxx

UNKNOWN:
- xxx

【推奨アクション】
ALLOW（レビュー後インストール可）:
- xxx

BLOCK（インストール禁止）:
- xxx

==================================================

---

## PURPOSE

This tool does NOT make decisions.

It:

- collects evidence
- organizes information
- classifies risk

Final judgment must be made by a human.

---

## LIMITATIONS

This tool does NOT guarantee:

- safety
- absence of vulnerabilities
- complete detection

UNKNOWN ≠ SAFE
