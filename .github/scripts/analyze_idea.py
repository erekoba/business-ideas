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
    template_block = "\n\n".join(
        f"=== {fname} ===\n{content}" for fname, content in templates.items()
    )
    prompt = f"""あなたはビジネスアイデアアナリストです。以下のアイデアを分析し、各テンプレートファイルを具体的な内容で埋めてください。

## アイデア
タイトル: {ISSUE_TITLE}
説明: {ISSUE_BODY}

## 分析の指針
- 副業前提（週数時間で動かせるか）で評価する
- 市場規模は日本市場で具体的な数字を推定する
- 競合は実在するサービスを挙げる
- 収益シミュレーションは保守的・目標・上振れの3シナリオを試算する
- リスクは楽観的にならず率直に指摘する
- 「次のアクション」は今すぐ実行できる具体的なステップにする

## 出力形式
以下のXMLタグ形式で返してください：

<slug>フォルダ名用の短い英語スラッグ（例: subscription-cooking-class）</slug>

<overview>
overview.mdの完成内容
</overview>

<needs>
needs.mdの完成内容
</needs>

<revenue>
revenue.mdの完成内容
</revenue>

<feasibility>
feasibility.mdの完成内容
</feasibility>

<competitors>
competitors.mdの完成内容
</competitors>

<score>
market=4
competition=3
revenue=3
feasibility=4
technical=5
</score>

## テンプレート
{template_block}
"""

    client = anthropic.Anthropic()
    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=8192,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text

    score = {}
    for line in extract_tag("score", raw).splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            score[k.strip()] = int(v.strip())

    return {
        "slug": extract_tag("slug", raw),
        "overview": extract_tag("overview", raw),
        "needs": extract_tag("needs", raw),
        "revenue": extract_tag("revenue", raw),
        "feasibility": extract_tag("feasibility", raw),
        "competitors": extract_tag("competitors", raw),
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
