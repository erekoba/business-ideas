import os
import json
import re
import subprocess
import urllib.request

import anthropic

ISSUE_TITLE = os.environ["ISSUE_TITLE"]
ISSUE_BODY = os.environ.get("ISSUE_BODY", "")
ISSUE_NUMBER = os.environ["ISSUE_NUMBER"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
FORMSPREE_FORM_ID = os.environ.get("FORMSPREE_FORM_ID", "")
TEMPLATE_DIR = "ideas/_template"
IDEAS_DIR = "ideas"
DOCS_DIR = "docs/lp"

_repo_owner, _repo_name = GITHUB_REPO.split("/", 1)
LP_BASE_URL = f"https://{_repo_owner}.github.io/{_repo_name}/lp"


def load_templates():
    templates = {}
    for fname in ["overview.md", "needs.md", "revenue.md", "feasibility.md", "competitors.md"]:
        with open(f"{TEMPLATE_DIR}/{fname}") as f:
            templates[fname] = f.read()
    return templates


def next_idea_number():
    entries = [
        d for d in os.listdir(IDEAS_DIR)
        if d != "_template" and os.path.isdir(f"{IDEAS_DIR}/{d}")
    ]
    numbers = [int(m.group(1)) for d in entries if (m := re.match(r"^(\d+)_", d))]
    return max(numbers) + 1 if numbers else 1


def extract_tag(tag: str, text: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return m.group(1).strip() if m else ""


def call_claude(templates: dict) -> dict:
    client = anthropic.Anthropic()

    base_context = f"""あなたはビジネスアイデアアナリストです。

## アイデア
タイトル: {ISSUE_TITLE}
説明: {ISSUE_BODY}

## 分析の指針
- 副業前提（週数時間で動かせるか）で評価する
- 市場規模は日本市場で具体的な数字を推定する
- 競合は実在するサービスを挙げる
- 収益シミュレーションは保守的・目標・上振れの3シナリオを試算する
- リスクは楽観的にならず率直に指摘する
- 「次のアクション」は今すぐ実行できる具体的なステップにする"""

    def ask(prompt: str) -> str:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()

    # slug
    slug_raw = ask(
        f"{base_context}\n\nこのアイデアのフォルダ名用の短い英語スラッグを1行だけ返してください（例: subscription-cooking-class）。"
    )
    slug = re.sub(r"[^\w-]", "-", slug_raw.lower()).strip("-")

    # 1. 競合調査（他の分析の土台になるため最初）
    competitors = ask(
        f"{base_context}\n\n以下のテンプレートを埋めてください。テンプレートの内容のみ返してください。\n\n{templates['competitors.md']}"
    )

    # 2. ニーズ調査（競合調査を踏まえて差別化ポイントを反映）
    needs = ask(
        f"{base_context}\n\n【競合調査の結果】\n{competitors}\n\n"
        f"上記の競合調査を踏まえて、以下のテンプレートを埋めてください。テンプレートの内容のみ返してください。\n\n{templates['needs.md']}"
    )

    # 3. 収益性調査（市場・ニーズを踏まえた価格設定）
    revenue = ask(
        f"{base_context}\n\n【ニーズ調査の結果】\n{needs}\n\n"
        f"上記のニーズ調査を踏まえて、以下のテンプレートを埋めてください。テンプレートの内容のみ返してください。\n\n{templates['revenue.md']}"
    )

    # 4. 実現性調査（収益性を踏まえた工数・リスク評価）
    feasibility = ask(
        f"{base_context}\n\n【収益性調査の結果】\n{revenue}\n\n"
        f"上記の収益性調査を踏まえて、以下のテンプレートを埋めてください。テンプレートの内容のみ返してください。\n\n{templates['feasibility.md']}"
    )

    # 5. Overview + スコア（全調査を踏まえて最後に生成）
    overview_raw = ask(
        f"{base_context}\n\n"
        f"【競合調査の結果】\n{competitors}\n\n"
        f"【ニーズ調査の結果】\n{needs}\n\n"
        f"【収益性調査の結果】\n{revenue}\n\n"
        f"【実現性調査の結果】\n{feasibility}\n\n"
        f"上記すべての調査を踏まえて、overview.mdとスコアを以下の形式で返してください。\n\n"
        f"<overview>\n{templates['overview.md']}\n</overview>\n\n"
        f"<score>\nmarket=4\ncompetition=3\nrevenue=3\nfeasibility=4\ntechnical=5\n</score>"
    )

    score_keys = ["market", "competition", "revenue", "feasibility", "technical"]
    score = {k: 0 for k in score_keys}
    for line in extract_tag("score", overview_raw).splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            k = k.strip()
            if k in score:
                score[k] = round(float(v.strip()))

    overview = extract_tag("overview", overview_raw)

    # 6. LP コピー生成
    lp_raw = ask(
        f"{base_context}\n\n"
        f"【overview】\n{overview}\n\n"
        f"【ニーズ調査】\n{needs}\n\n"
        f"【実現性調査】\n{feasibility}\n\n"
        "上記の分析をもとに、このサービスの検証用ランディングページのコピーを日本語で作成してください。\n"
        "以下のJSON形式のみ返してください（説明文や```は不要）:\n\n"
        '{\n'
        '  "service_name": "サービス名（短く覚えやすい名前）",\n'
        '  "headline": "キャッチコピー（課題や価値を一言で、20字以内）",\n'
        '  "subheadline": "補足説明（2〜3文、具体的なベネフィット）",\n'
        '  "problem_title": "課題セクションの見出し",\n'
        '  "problem_body": "ターゲットが抱える課題（2〜3文）",\n'
        '  "solution_title": "解決策セクションの見出し",\n'
        '  "solution_body": "このサービスが提供する解決策（2〜3文）",\n'
        '  "cta_text": "メール登録CTAボタンのテキスト（例: リリースを通知してほしい）"\n'
        '}'
    )
    lp_raw = re.sub(r"^```[a-z]*\n?", "", lp_raw.strip()).rstrip("`").strip()
    lp_content = json.loads(lp_raw)

    return {
        "slug": slug,
        "overview": overview,
        "needs": needs,
        "revenue": revenue,
        "feasibility": feasibility,
        "competitors": competitors,
        "score": score,
        "lp": lp_content,
    }


def _esc(s: str) -> str:
    """Escape curly braces for safe use in f-strings / HTML."""
    return s.replace("{", "&#123;").replace("}", "&#125;")


def build_lp_html(lp: dict, slug: str) -> str:
    sn = _esc(lp["service_name"])
    hl = _esc(lp["headline"])
    sub = _esc(lp["subheadline"])
    pt = _esc(lp["problem_title"])
    pb = _esc(lp["problem_body"])
    st = _esc(lp["solution_title"])
    sb = _esc(lp["solution_body"])
    cta = _esc(lp["cta_text"])

    if FORMSPREE_FORM_ID:
        form_html = (
            f'<form action="https://formspree.io/f/{FORMSPREE_FORM_ID}" method="POST" '
            'class="flex flex-col sm:flex-row gap-3 max-w-md mx-auto">'
            '<input type="email" name="email" required placeholder="your@email.com" '
            'class="flex-1 px-4 py-3 rounded-lg border border-gray-300 focus:outline-none '
            'focus:ring-2 focus:ring-indigo-500 text-gray-900" />'
            f'<button type="submit" class="px-6 py-3 bg-indigo-600 text-white font-semibold '
            f'rounded-lg hover:bg-indigo-700 transition whitespace-nowrap">{cta}</button>'
            '</form>'
        )
    else:
        form_html = '<p class="text-gray-500 text-sm">近日公開予定です。</p>'

    return f"""<!DOCTYPE html>
<html lang="ja">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{sn}</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-white text-gray-800 font-sans">

  <nav class="px-6 py-4 border-b border-gray-100">
    <span class="font-bold text-lg text-indigo-600">{sn}</span>
  </nav>

  <section class="bg-gradient-to-br from-indigo-50 to-white py-20 px-6 text-center">
    <h1 class="text-4xl sm:text-5xl font-extrabold text-gray-900 leading-tight mb-6">
      {hl}
    </h1>
    <p class="text-lg sm:text-xl text-gray-600 max-w-2xl mx-auto mb-10 leading-relaxed">
      {sub}
    </p>
    <a href="#register"
      class="inline-block bg-indigo-600 text-white font-semibold px-8 py-4 rounded-full text-lg hover:bg-indigo-700 transition shadow-md">
      {cta}
    </a>
  </section>

  <section class="py-16 px-6 max-w-3xl mx-auto">
    <h2 class="text-2xl sm:text-3xl font-bold text-gray-900 mb-6">{pt}</h2>
    <p class="text-gray-600 leading-relaxed text-lg whitespace-pre-line">{pb}</p>
  </section>

  <section class="py-16 px-6 bg-indigo-50">
    <div class="max-w-3xl mx-auto">
      <h2 class="text-2xl sm:text-3xl font-bold text-gray-900 mb-6">{st}</h2>
      <p class="text-gray-600 leading-relaxed text-lg whitespace-pre-line">{sb}</p>
    </div>
  </section>

  <section id="register" class="py-20 px-6 text-center">
    <h2 class="text-2xl font-bold text-gray-900 mb-3">リリースを通知する</h2>
    <p class="text-gray-500 mb-8">メールアドレスを登録しておくと、リリース時にお知らせします。</p>
    {form_html}
  </section>

  <footer class="py-8 px-6 border-t border-gray-100 text-center text-sm text-gray-400">
    <p>このサービスは現在開発中です。</p>
    <p class="mt-1">&copy; 2025 {sn}</p>
  </footer>

</body>
</html>"""


def create_files(data: dict, idea_num: int) -> tuple[str, str]:
    slug = re.sub(r"[^\w-]", "-", data["slug"].lower()).strip("-")
    folder = f"{IDEAS_DIR}/{idea_num:03d}_{slug}"
    os.makedirs(folder, exist_ok=True)

    for fname, key in [
        ("overview.md", "overview"),
        ("needs.md", "needs"),
        ("revenue.md", "revenue"),
        ("feasibility.md", "feasibility"),
        ("competitors.md", "competitors"),
    ]:
        with open(f"{folder}/{fname}", "w") as f:
            f.write(data[key])

    lp_dir = f"{DOCS_DIR}/{slug}"
    os.makedirs(lp_dir, exist_ok=True)
    with open(f"{lp_dir}/index.html", "w") as f:
        f.write(build_lp_html(data["lp"], slug))

    # Prevent Jekyll from ignoring files starting with _
    nojekyll = "docs/.nojekyll"
    if not os.path.exists(nojekyll):
        open(nojekyll, "w").close()

    return folder, slug


def git_commit_and_push(folder: str):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", folder, "docs/"], check=True)
    subprocess.run(["git", "commit", "-m", f"feat: add analysis for {ISSUE_TITLE}"], check=True)
    subprocess.run(["git", "push"], check=True)


def post_comment(folder: str, slug: str, score: dict):
    total = sum(score.values())
    lp_url = f"{LP_BASE_URL}/{slug}/"
    body = f"""## 分析完了 ✅

`{folder}` に分析ファイルを作成しました。

| 項目 | スコア |
|------|--------|
| 市場性・ニーズ | {score['market']}/5 |
| 競合優位性 | {score['competition']}/5 |
| 収益性 | {score['revenue']}/5 |
| 副業実現性 | {score['feasibility']}/5 |
| 技術実現性 | {score['technical']}/5 |
| **総合** | **{total}/25** |

### 検証用 LP
{lp_url}

> GitHub Pages が有効であれば上記 URL でアクセスできます。
"""

    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_REPO}/issues/{ISSUE_NUMBER}/comments",
        data=json.dumps({"body": body}).encode(),
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/vnd.github.v3+json",
        },
        method="POST",
    )
    urllib.request.urlopen(req)


def main():
    templates = load_templates()
    idea_num = next_idea_number()
    data = call_claude(templates)
    folder, slug = create_files(data, idea_num)
    git_commit_and_push(folder)
    post_comment(folder, slug, data["score"])
    print(f"Done: {folder}")


if __name__ == "__main__":
    main()
