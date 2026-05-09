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
TEMPLATE_DIR = "ideas/_template"
IDEAS_DIR = "ideas"


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
                score[k] = int(v.strip())

    return {
        "slug": slug,
        "overview": extract_tag("overview", overview_raw),
        "needs": needs,
        "revenue": revenue,
        "feasibility": feasibility,
        "competitors": competitors,
        "score": score,
    }


def create_files(data: dict, idea_num: int) -> str:
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

    return folder


def git_commit_and_push(folder: str):
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", folder], check=True)
    subprocess.run(["git", "commit", "-m", f"feat: add analysis for {ISSUE_TITLE}"], check=True)
    subprocess.run(["git", "push"], check=True)


def post_comment(folder: str, score: dict):
    total = sum(score.values())
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
    folder = create_files(data, idea_num)
    git_commit_and_push(folder)
    post_comment(folder, data["score"])
    print(f"Done: {folder}")


if __name__ == "__main__":
    main()
