# pkgcheck — Evidence-based パッケージセキュリティ監査コマンド

`pkgcheck` は、**`pip install` / `npm install` を実行する直前 (Point-of-Use)** に、対象パッケージの出自・メンテナンス状況・既知脆弱性を **証拠ベース** で並べるAIコーディングツール向けのスラッシュコマンドです。**AIが安全かどうかを判断するものではなく、人間が判断するための材料を集めて整形します。**

---

## 目次

1. [pkgcheck で何ができるか](#1-pkgcheck-で何ができるか)
2. [なぜ必要か — Point-of-Use リスク](#2-なぜ必要か--point-of-use-リスク)
3. [Quick Start](#3-quick-start)
4. [対応ランタイム](#4-対応ランタイム)
5. [設計原則](#5-設計原則)
6. [引数の渡し方](#6-引数の渡し方)
7. [リスク分類の概要](#7-リスク分類の概要)
8. [動作の仕組み](#8-動作の仕組み)
9. [制約](#9-制約)
10. [ライセンス](#10-ライセンス)
11. [関連リンク](#11-関連リンク)
12. [詳細情報](#12-詳細情報)

---

## 1. pkgcheck で何ができるか

入力: パッケージ名 (1 つ以上) またはパッケージ管理ファイル (`requirements.txt`, `package.json`, `pyproject.toml`, `Pipfile`)。

出力: パッケージごとに以下を集約した日本語レポート + 末尾に FINAL SUMMARY。

- **名前照合**: 入力名と公式レジストリ名の完全一致確認 (タイポスクワット検知の基本)
- **公式レジストリ情報**: 著者 / 現 maintainer / 最新バージョン / 初回・最終リリース日
- **ソースリポジトリ**: GitHub オーナー種別、スター数、最終 push日、archive 状態
- **メンテナンス状況**: ACTIVE / LOW / ABANDONED
- **採用実績**: 週間ダウンロード数
- **セキュリティ**: OSV の CVE/GHSA、各 advisory の "最新版で修正済みか" 判定、Claude Mythos CVD Ledger との照合
- **リスクレベル**: `LOW SIGNAL` / `CAUTION` / `HIGH RISK` / `UNKNOWN`
- **推奨**: 人間が次に取るべきアクション

---

## 2. なぜ必要か — Point-of-Use リスク

AI コーディングエージェントの普及で、開発者は AI が提案した `pip install` / `npm install` を深く検証せずに実行しがちです。AI の「親切な推論」が次のような脅威に直結します。

1. **タイポスクワッティング** — AI が typo を「気を利かせて」補正し、悪意ある類似パッケージを正規扱いしてしまう
2. **Shai-Hulud 型攻撃 (AI ハルシネーションの悪用)** — AI が存在しないパッケージ名を提案し、それを先回りで登録した攻撃者の悪性パッケージをインストールさせる
3. **正規パッケージへのサプライチェーン攻撃** — 「有名だから安全」という推論を逆手に取る攻撃

`npm audit` / `pip-audit` は **インストール後** の事後監査ツールです。しかし悪性パッケージは install 時の post-install スクリプトで侵害を完了させ得るため、**「インストールする直前」**に検証する必要があります。`pkgcheck` はその一点に特化しています。

---

## 3. Quick Start

### 3.1 動作要件

- **Python 3.11+** が PATH 上にあること (`tomllib` の都合。`tomli` を別途入れれば 3.10 以下でも可)
- 標準ライブラリのみで動作 (追加 `pip install` 不要)
- 以下のホストへ HTTPS で到達できること:
  - `pypi.org`, `registry.npmjs.org`, `api.npmjs.org`, `pypistats.org`
  - `api.osv.dev`, `api.github.com`, `red.anthropic.com`
- (任意) `GITHUB_TOKEN` 環境変数があれば GitHub API レート制限が 60 → 5,000 req/h に拡張

### 3.2 セットアップ

リポジトリをクローンまたはダウンロードして、`pkgcheck.md` と `pkgcheck_lib/` を LLM ランタイムが認識する slash command ディレクトリに配置するだけです (例: `.bob/commands/`、`.claude/commands/`)。インストール手順はありません。

```
<your-project>/
└── .bob/commands/                     ← ランタイムによってパスは変わる
    ├── pkgcheck.md                    ← LLM が読む仕様書
    ├── pkgcheck.README.md             ← このファイル
    └── pkgcheck_lib/
        └── runner.py                  ← 実処理 (Python)
```

### 3.3 最短実行例

スラッシュコマンドとして:

```
/pkgcheck fastapi uvicorn
```



### 3.4 出力サンプル (抜粋)

実際のレポート (`fastapi` + `uvicorn` を監査した結果の冒頭・末尾):

```
------------------------------
個別パッケージ調査結果 [1/2] fastapi
------------------------------

パッケージ (入力): fastapi

名前照合検証:
 入力名: fastapi
 公式名: fastapi
 照合結果: MATCH

公式レジストリ:
 URL: https://pypi.org/project/fastapi/
 Registry author (歴史的、現 publisher と異なる場合あり): Sebastián Ramírez <tiangolo@gmail.com>
 現 maintainers: NOT_CONFIRMED
 最新バージョン: 0.136.3
 初回リリース: 2018-12-08
 最終リリース: 2026-05-23

ソースリポジトリ:
 URL: https://github.com/fastapi/fastapi
 オーナー: fastapi（Organization）
 スター数: 98527 (取得日: 2026-05-27、出典: GitHub API)
 最終push日: 2026-05-26
 アーカイブ済み: false

メンテナンス状況:
 ACTIVE

採用実績:
 週間ダウンロード: 110,565,558 (取得日: 2026-05-27、出典: pypistats.org)
 Dependents: NOT_CONFIRMED (本ランナーでは未取得)

セキュリティ:
 OSV: https://osv.dev/list?ecosystem=PyPI&q=fastapi
 CVEs: 3
   - GHSA-8h2j-cgx8-6xv7 | Cross-Site Request Forgery (CSRF) in FastAPI | ...
     状態: 修正済み (latest 適用) (latest 0.136.3 は全 range 外)
   ... (省略)
   → 評価: 全 3 件が patched / withdrawn (latest 0.136.3 は影響外)
 Claude Mythos: なし

リスクレベル:
 LOW SIGNAL (現時点で強いリスクシグナルは未検出。安全保証ではない)

推奨:
 利用前に人間レビュー必須（LOW SIGNAL は安全保証ではない）

... (uvicorn のブロックが続く) ...

------------------------------
FINAL SUMMARY
------------------------------

【リスク分類】
LOW SIGNAL (現時点で強いリスクシグナルは未検出。安全保証ではない):
- fastapi
- uvicorn

CAUTION:
- (なし)

HIGH RISK:
- (なし)

UNKNOWN:
- (なし)

【推奨アクション】
REVIEW THEN ALLOW (人間レビュー後に利用可。ただし安全保証ではない):
- fastapi
- uvicorn

BLOCK (インストール禁止または要強い確認):
- (なし)
```

同じ内容が `./pkgcheck-reports/pkgcheck-YYYYMMDD-HHMMSS.md` にも保存されます (IBM Bob のようなチャットUI上等でのチャット表示切り詰め対策)。古いレポートの削除は各自で行ってください。

---

## 4. 対応ランタイム

### 4.1 動作確認済み環境

- **OS**: Windows
- **ランタイム**: IBM Bob (IDE 拡張)
- **配置**: `.bob/commands/pkgcheck.md`


### 4.2 動作すると思われる環境 (未検証)

`runner.py` は Python 標準ライブラリのみで構成され、OS / ランタイム固有の API には依存していません。したがって以下の環境でも動作すると思われますが、**実機での確認は行っていません**。動作報告・不具合報告は歓迎します。

| ランタイム | 配置例 | 呼び出し方 | 補足 |
|---|---|---|---|
| **Claude Code** | `.claude/commands/pkgcheck.md` | `/pkgcheck <args>` | `$ARGUMENTS` がそのまま展開される想定 |
| **素の CLI / 検証用** | 任意 | `python .bob/commands/pkgcheck_lib/runner.py <args>` | LLM を介さず動作確認したいとき。bash の場合 `"$@"` を使う |

OS については、Windows 以外 (macOS / Linux) でも Python 3.11+ が入っていれば同一コードで動くはずですが、こちらも未検証です。

---

## 5. 設計原則

「AI に判断させない」「安全を保証しない (証拠を並べるだけ)」という方針です。

- **原則 1: Evidence-based** — 推論を排し、レジストリ (PyPI / npm)、GitHub、OSV、ダウンロード統計から取得した事実だけを判断材料にする
- **原則 2: Exact Name Match** — 入力名と公式名に 1 文字でも差異があれば問答無用で `HIGH RISK`
- **原則 3: Fail-Closed** — 証拠が 1 つでも取得できない場合は `UNKNOWN`。「危険という証拠がないこと」を「安全」とはみなさない

---

## 6. 引数の渡し方

| パターン | 例 | 処理 |
|---|---|---|
| 引数なし | `/pkgcheck` | LLM が直近の会話から候補を推論して実行 |
| 単一/複数のパッケージ名 | `/pkgcheck requests numpy` | 直接監査 |
| エコシステム指定 | `/pkgcheck --npm react`<br>`/pkgcheck --pip requests` | npm / PyPI を明示 |
| マニフェストファイル | `/pkgcheck requirements.txt`<br>`/pkgcheck package.json` | 中身をパースして直接依存を一括監査 |

**制限**:
- ロックファイル (`package-lock.json` 等) は設計上は非対応。現状は明示的な拒否はせず、内容スニファでフォールバック処理されるため誤動作する可能性あり (transitive deps が爆発するため)
- ファイルとパッケージ名の同時指定、または 2ファイル以上の指定はエラー
- 大量件数 (30 件以上) は未検証。 API レート制限・タイムアウトの追加対応が必要な可能性があります。

---

## 7. リスク分類の概要

| ラベル | 意味 | 主なトリガー |
|---|---|---|
| **HIGH RISK** | 強いシグナル検出。インストールしない | 名前不一致、レジストリ不在、OSV summary に `malware`/`backdoor` 等、npm `security-holder`、タイポスクワット疑い (距離 1) + 弱い証拠 |
| **CAUTION** | 要慎重判断 | OSV に既知脆弱性 + 最新版で**未修正**または判定不能、Mythos に critical/high の未パッチヒット、archived / ABANDONED、`0.0.x` + メンテ LOW |
| **UNKNOWN** | 判定不能 | OSV 取得失敗・パース失敗 (= 安全とはみなさない) |
| **LOW SIGNAL** | 強いリスクシグナル未検出 | 上記いずれにも該当しない (= 安全保証ではない) |

「LOW RISK」ではなく **`LOW SIGNAL`** という名前にしているのは「LOW RISK = 安全」と誤読されるのを避けるためです。最終判断は人間が行います。

詳細な判定ロジック (`malware_signals` / `evaluate_patched_status` / `detect_typosquat`) は §12.2 を参照。

---

## 8. 動作の仕組み

### 8.1 単一プロセス・オーケストレータ

すべての処理を単一の Python プロセス (`pkgcheck_lib/runner.py`) に集約しています。LLM からの呼び出しは 1 回で済むため、IBM Bob のような承認制ランタイムでも UX が破綻しません。

- シェルコマンドの連鎖がないため、OS コマンドインジェクションやクォーティング問題が排除される
- 一時ファイルを使わず、プロセス内でデータの受け渡しが完結
- ネットワーク通信はハードコードされた allowlist (§3.1 のホスト) への HTTPS 通信のみ
- パッケージコードは取得・実行しない (メタデータ JSON のみ)

### 8.2 参照ソース

| ソース | 用途 |
|---|---|
| PyPI / npm Registry | バージョン・著者・リリース日 |
| OSV REST API | CVE / GHSA |
| GitHub API | スター数・最終 push・archive 状態・owner |
| api.npmjs.org / pypistats.org | 週間ダウンロード数 |
| Claude Mythos CVD Ledger (`red.anthropic.com`) | Anthropic 発見の脆弱性 |

### 8.3 出力チャネル

| チャネル | 内容 | 用途 |
|---|---|---|
| stdout | フルレポート | LLM / Bob が転記する本体 |
| stderr | `[N/total] <name>` の進捗 | 待ち時間中の状態可視化 |
| ファイル | フルレポート (stdout 同一内容) を `./pkgcheck-reports/pkgcheck-YYYYMMDD-HHMMSS.md` へ | stdout が切り詰められても全件確認できる |

---

## 9. 制約

- **Dependents 数は取得しない** (`NOT_CONFIRMED` 表示)。週間ダウンロード数で補完
- ロックファイル非対応 (直接依存のみ)
- `setup.py` / `setup.cfg` / `Cargo.toml` / `go.mod` 等は非対応
- runner のタイポスクワット fuzzy match は **curated list 限定** (人気 ~50 パッケージのみ)。リスト外のタイポは LLM 側の POST-RUNNER ANNOTATION で部分的に補完
- semver 比較は `X.Y.Z` の数値比較のみ。`-rc1` / `-beta` 等の prerelease サフィックスは捨てて近似評価。判定不能なら fail-closed で `CAUTION`
- maintainer takeover の自動判定はしない (`_npmUser` と `maintainers[]` の不一致は表示するが分類には使わない — 合法な代行 publish もあるため)
- `red.anthropic.com` の Mythos Ledger はスキーマや URL が変わる可能性がある (§12.1 参照)

---

## 10. ライセンス

このプロジェクトは MIT License の下で公開されています。

---

## 11. 関連リンク

- 開発背景の解説記事 (Zenn): <https://zenn.dev/dxclab/articles/342b2c0872c7f3>
- Anthropic CVD ダッシュボード: <https://red.anthropic.com/2026/cvd>
- OSV: <https://osv.dev/>

---

## 12. 詳細情報

(以下は本コマンドのメンテナーが内部判定ロジックや更新手順を確認する際の参照情報。一般利用者は読み飛ばして可。)

### 12.1 Claude Mythos 照合の設計詳細

- **データソース**: <https://red.anthropic.com/2026/cvd/data/ledger.json>
- 複数パッケージ監査時もモジュールレベルキャッシュで取得は 1 回
- `project` フィールドが `owner/repo` 形式と bare 名で混在しているため、「完全一致」と「末尾セグメント一致」の二段階照合
- 別の同名パッケージにヒットする偽陽性リスクあり → ヒット時は人間が一致確認すること
- リスク分類への反映: ヒット時は情報表示のみ。**未パッチかつ severity=critical/high** のときだけ `CAUTION` に自動昇格

### 12.2 主要判定ルール

- **`malware_signals()`**: OSV summary の `malware` / `malicious` / `backdoor` / `trojan` / `typosquat` / `credential steal` / `data exfil` 等のキーワード、または npm `security-holder` 痕跡を検知 → 即 HIGH RISK
- **`evaluate_patched_status()`**: 単純な fixed バージョン比較ではなく `affected[].ranges[].events[]` をウォークして半開区間を組み立て、最新版が脆弱区間に含まれるか判定。複数 range・複数 introduced/fixed・open range をすべて処理。`withdrawn` 付き advisory はスキップ。判定不能時は `has_unknown` を返し fail-closed で `CAUTION`
- **`detect_typosquat()`**: Damerau-Levenshtein 距離 1 (1 文字編集または隣接文字入れ替え) で curated list と一致したものを検知。誤検知抑制のため「リポジトリが空 / ABANDONED / version 0.0.x」のいずれかの弱い証拠と組み合わさったときのみ HIGH RISK 昇格

### 12.3 Publisher 表示の方針 (npm)

| フィールド | 意味 | 鮮度 |
|---|---|---|
| `author` | 最初に publish した author | **歴史的**。長年更新されないことが多い |
| `maintainers[]` | 現在 publish 権限を持つ npm アカウント一覧 | **現役**。supply-chain 観点で重要 |
| `versions[<latest>]._npmUser` | 最新版を実際に push した npm アカウント | **最も新しい**。takeover 検出に有用 |

3 フィールドを別行に分けて表示。`_npmUser` が `maintainers[]` に含まれない場合は `⚠ maintainers 一覧に含まれず (要確認)` を併記 (maintainer takeover や token leak の早期警告)。PyPI JSON API は per-release publisher を露出しないため `最新版 publisher: NOT_CONFIRMED`。

### 12.4 動作テスト (as-of: 2026-05-27)

代表的な 4 ケースで全リスク経路を網羅:

| 入力 | 期待 | 実測 | 検出された主シグナル |
|---|---|---|---|
| `express` | LOW SIGNAL | LOW SIGNAL ✓ | OSV CVE × 5 件すべて latest で patched (range 評価) |
| `react` | LOW SIGNAL | LOW SIGNAL ✓ | 最新版で修正済み。`registry_author=NOT_CONFIRMED` |
| `crossenv` | HIGH RISK | HIGH RISK ✓ | OSV summary に `malware`。typosquat 検出 |
| `recat` | HIGH RISK | HIGH RISK ✓ | typosquat (距離 1 → `react`) + 弱い証拠で HIGH RISK 昇格 |

### 12.5 更新ガイドライン

| トリガー | 対応 |
|---|---|
| Mythos の URL / スキーマ変更 | `runner.py` の `get_mythos_ledger()` と本書 §12.1 を更新 |
| 新しいエコシステムやマニフェスト形式の追加 | `runner.py` の `extract_deps()` を更新 |
| リスク判定ルール変更 | `runner.py` の `classify_risk()` および `pkgcheck.md` を更新 |
| curated list の更新 | `runner.py` の `_POPULAR_NPM` / `_POPULAR_PYPI` を更新 (LLM の knowledge cutoff に依存しないため、メンテで追従推奨) |

大きな変更を加える前は、git で履歴管理されていることを確認してから編集すること。

### 12.6 関連ファイル

- `pkgcheck.md` — LLM が読む slash command 仕様書
- `pkgcheck_lib/runner.py` — オーケストレータ本体 (Python 標準ライブラリのみ)
- `pkgcheck.README.md` — 本書 (人間メンテナー・利用検討者向け)
