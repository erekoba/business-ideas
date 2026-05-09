# スモールビジネス アイデア管理

副業・スモールビジネスのアイデアをブラッシュアップするためのプロジェクト。

## ディレクトリ構成

```
ideas/
  _template/          # 新しいアイデアを追加するときのテンプレート
    overview.md       # アイデア概要・評価スコア
    needs.md          # ニーズ調査（市場・競合・差別化）
    revenue.md        # 収益性調査（収益モデル・シミュレーション）
    feasibility.md    # 実現性調査（副業・技術・リスク）
  001_アイデア名/
    overview.md
    needs.md
    revenue.md
    feasibility.md
```

## 新しいアイデアを追加する手順

1. `ideas/_template/` をコピーして `ideas/NNN_アイデア名/` を作成
2. `overview.md` にアイデアの概要を記入
3. Claude に「このアイデアをブラッシュアップして」と依頼
