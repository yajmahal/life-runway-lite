"""海外移住 資産寿命シミュレーター（簡易版）のルールベースのコメント生成。

AI API は使わず、入力条件と計算結果から固定ルールでコメントを組み立てます。
コメントは「総合コメント」「要因コメント」「次に試す変更案」の 3 部構成です。

設計の方針:
- このアプリの肝は、数字をどう読み解き、次にどの前提を変えて確認すればよいかを示すこと。
- 「資産が残る可能性」と、目標年齢時点の中央値残高の両方を見てコメントを出し分ける。
- 要因コメントと「次に試す変更案」をできるだけ連動させる。
- 結果がギリギリ／不足寄りのときは、悪化方向だけでなく改善方向の確認を優先して提案する。

トーンの方針:
- 金融助言や断定にならないようにする。
  避ける表現:「移住できます」「退職して大丈夫です」「安心です（断定）」「問題ありません」
  「投資を増やすべきです」「年金を繰り下げましょう」「生活費を下げるべきです」「危険です」「破綻します」
- 使う表現:「確認してみてください」「比較してみてください」「前提を変えると違いが見えます」
  「余裕を見ておくと安心です」「見直す余地があります」「影響が大きい前提です」
  「不足が出る可能性があります」「改善する可能性があります」「保守的に見た場合の影響を確認できます」
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator import SimulationInput, SimulationResult

# コメント用の簡易しきい値（万円）。あくまで読み解きの目安で、安全/危険の断定には使わない。
SLIGHT_SURPLUS = 500  # これ未満（0以上）なら「かなりギリギリ」
LIMITED_SURPLUS = 1000  # これ未満なら「余裕は限定的」


@dataclass
class CommentSet:
    """生成したコメント一式。"""

    overall: str  # 総合コメント
    factors: list[str]  # 要因コメント（最大 2 文程度）
    next_tries: list[str]  # 次に試す変更案（最大 3 件）


# ---------------------------------------------------------------------------
# 1. 総合コメント（資産が残る可能性 + 中央値残高で出し分け）
# ---------------------------------------------------------------------------

def _balance_note(balance_at_target: float) -> str:
    """目標年齢時点の中央値残高に応じた補足（ギリギリ判定）。

    残高がマイナスのケースは確率帯の本文側で扱うため、ここでは空文字を返す。
    """
    if balance_at_target < 0:
        return ""
    if balance_at_target < SLIGHT_SURPLUS:
        return (
            "中央値では目標年齢時点で資産がわずかに残りますが、余裕は大きくありません。"
            "生活費の数か月分程度の残高にとどまる場合は、少し前提が変わるだけで"
            "不足に転じやすい点に注意が必要です。"
        )
    if balance_at_target < LIMITED_SURPLUS:
        return (
            "中央値では目標年齢時点で資産が残りますが、余裕は限定的です。"
            "前提が変わった場合の影響もあわせて確認しておくと、見通しを持ちやすくなります。"
        )
    return ""


def _overall_comment(
    probability: float, target_age: int, balance_at_target: float
) -> str:
    """資産が残る可能性（0.0〜1.0）と中央値残高に応じた総合コメントを返す。"""
    pct = probability * 100.0

    if pct >= 90:
        base = (
            f"この前提では、{target_age}歳まで資産が残る可能性はかなり高めです。"
            "ただし、将来の運用や物価は変動するため、生活費やインフレ率を少し厳しめにした"
            "ケースも確認しておくと安心です。"
        )
    elif pct >= 80:
        base = (
            f"この前提では、{target_age}歳まで資産が残る可能性は比較的高めです。"
            "一方で、運用や物価が想定より厳しくなった場合にどの程度余裕が残るかも"
            "確認しておくと、より見通しを持ちやすくなります。"
        )
    elif pct >= 60:
        base = (
            f"この前提では、{target_age}歳まで資産が残る可能性は一定程度ありますが、"
            "大きな余裕があるとは言い切れません。生活費や年金開始までの期間など、"
            "影響が大きい前提を少し変えて比較してみると、改善余地が見えやすくなります。"
        )
    elif pct >= 40:
        base = (
            f"この前提では、{target_age}歳まで資産が残る可能性は50%前後です。"
            "運用や物価が下振れした場合には不足が出やすい前提のため、"
            "改善方向の前提変更を試す価値があります。"
        )
    elif pct >= 20:
        base = (
            f"この前提では、{target_age}歳までに資産不足が出る可能性が高めです。"
            "特に、生活費、海外生活を始める年齢、公的年金開始までの期間が"
            "結果に大きく影響している可能性があります。"
        )
    else:
        base = (
            f"この前提では、{target_age}歳まで資産を持たせるにはかなり厳しい試算結果です。"
            "生活費、開始年齢、年金開始前の収入、手元資産など、"
            "複数の前提を見直したケースを確認する必要があります。"
        )

    note = _balance_note(balance_at_target)
    return base + (note if note else "")


# ---------------------------------------------------------------------------
# 2. 要因コメント（入力条件に応じて検出。キー付きで次の提案と連動させる）
# ---------------------------------------------------------------------------

def _detect_factors(inp: SimulationInput) -> list[tuple[str, str]]:
    """該当する要因を (キー, 表示文) のリストで返す（影響が大きい順）。"""
    factors: list[tuple[str, str]] = []

    # 公的年金の受給開始までの取り崩し期間が長い
    if inp.public_pension_start_age - inp.start_age >= 3:
        factors.append((
            "pension_gap",
            "公的年金の受給開始までの期間をどうつなぐかが大きなポイントです。"
            "受給開始までの取り崩し期間が、資産寿命に大きく影響します。",
        ))

    # 海外生活費が高め
    if inp.overseas_monthly_cost >= 35:
        factors.append((
            "high_living",
            "海外生活中の月額生活費が高めに設定されているため、"
            "前半の資産取り崩しが大きくなりやすい前提です。",
        ))

    # インフレ率が高め
    if inp.inflation_rate >= 0.03:
        factors.append((
            "high_inflation",
            "インフレ率を高めに設定しているため、"
            "後半になるほど生活費の負担が大きくなります。",
        ))

    # 日本に戻る予定がある
    if inp.plan_to_return:
        factors.append((
            "return_plan",
            "日本に戻る時期と帰国後の生活費は、後半の資産残高に大きく影響します。",
        ))

    # 日本の持ち家売却
    if inp.sell_japan_home and inp.japan_home_proceeds > 0:
        factors.append((
            "japan_home_sale",
            "海外生活を始める時点で日本の持ち家を売却するため、"
            "初期の手元資産は厚くなる前提です。",
        ))

    # 海外の住まい売却（帰国時）
    if inp.plan_to_return and inp.sell_overseas_home and inp.overseas_home_proceeds > 0:
        factors.append((
            "overseas_home_sale",
            "日本に戻るときに海外の住まいを売却する設定になっているため、"
            "帰国時点で資産残高が回復する前提です。",
        ))

    # その他収入あり
    if any(s.annual_amount > 0 for s in inp.other_incomes):
        factors.append((
            "other_income",
            "その他収入があるため、公的年金開始前の資産取り崩しを"
            "一部抑える前提です。",
        ))

    if not factors:
        factors.append((
            "generic",
            "現在の前提では、生活費と公的年金のバランスが資産寿命に大きく影響します。"
            "前提を変えると違いが見えてきます。",
        ))

    return factors


# ---------------------------------------------------------------------------
# 3. 次に試す変更案（確率帯 + 検出した要因に連動。最大 3 件）
# ---------------------------------------------------------------------------

# 要因キーごとの「改善方向」の提案文
_IMPROVEMENT_BY_FACTOR = {
    "high_living": (
        "海外生活中の月額生活費を1〜3万円下げた場合を試してみてください。"
        "資産が残る可能性がどの程度改善するかを確認できます。"
    ),
    "pension_gap": (
        "海外生活を始める年齢を1〜2年遅らせた場合も確認してみてください。"
        "公的年金の受給開始までの取り崩し期間が短くなり、結果が改善する可能性があります。"
    ),
}

# 要因キーごとの「保守的な確認」の提案文
_CONSERVATIVE_BY_FACTOR = {
    "high_inflation": (
        "インフレ率を変えた場合に、後半の資産残高がどの程度変わるかを確認してみてください。"
    ),
    "return_plan": (
        "日本に戻る年齢や帰国後の月額生活費を変えた場合も試してみてください。"
        "後半の資産残高への影響を確認できます。"
    ),
    "japan_home_sale": (
        "日本の持ち家の売却後の手取り額を少し保守的に見た場合も確認してみてください。"
        "売却価格が想定より低かった場合の影響を把握しやすくなります。"
    ),
    "overseas_home_sale": (
        "海外の住まいの売却額を少し低めにした場合も確認してみてください。"
        "売却額の前提が結果にどの程度影響するかを確認できます。"
    ),
}


def _next_try_comments(
    inp: SimulationInput, result: SimulationResult, factor_keys: list[str]
) -> list[str]:
    """確率帯と検出要因に応じて、改善方向／保守的確認の提案を最大 3 件返す。"""
    pct = result.survival_probability * 100.0

    improvement: list[str] = []  # 改善方向
    conservative: list[str] = []  # 保守的な確認（ストレステスト）

    # --- 改善方向の提案 ---
    # 生活費の引き下げは最も普遍的な改善レバーなので常に候補に入れる
    improvement.append(
        "海外生活中の月額生活費を1〜3万円下げた場合を試してみてください。"
        "資産が残る可能性がどの程度改善するかを確認できます。"
    )
    # 公的年金の受給開始まで期間がある場合の改善案
    if inp.public_pension_start_age > inp.start_age:
        improvement.append(
            "海外生活を始める年齢を1〜2年遅らせた場合も確認してみてください。"
            "公的年金の受給開始までの取り崩し期間が短くなり、結果が改善する可能性があります。"
        )
        improvement.append(
            "年金開始前の収入を追加した場合も試してみてください。"
            "公的年金の受給開始までの取り崩しを抑えられるか確認できます。"
        )

    # --- 保守的な確認（ストレステスト）の提案 ---
    conservative.append(
        "インフレ率を少し高めにした場合も確認してみてください。"
        "後半の生活費負担が結果にどの程度影響するかを把握できます。"
    )
    conservative.append(
        "想定運用利回りを少し低めにした場合も確認してみてください。"
        "運用が想定より振るわなかった場合の影響を確認できます。"
    )

    # 検出した要因に連動した提案を優先的に前へ差し込む
    for key in factor_keys:
        if key in _IMPROVEMENT_BY_FACTOR:
            improvement.insert(0, _IMPROVEMENT_BY_FACTOR[key])
        if key in _CONSERVATIVE_BY_FACTOR:
            conservative.insert(0, _CONSERVATIVE_BY_FACTOR[key])

    # --- 確率帯に応じて方向を出し分け ---
    if pct >= 80:
        # かなり良い: 保守的な確認を中心に 1〜2 件
        ordered = conservative
        cap = 2
    elif pct >= 60:
        # 一定程度: 改善案と保守的確認の両方をバランスよく
        ordered = []
        if improvement:
            ordered.append(improvement[0])
        if conservative:
            ordered.append(conservative[0])
        ordered += improvement[1:] + conservative[1:]
        cap = 3
    else:
        # ギリギリ〜不足寄り: まず改善方向を優先
        ordered = improvement + conservative
        cap = 3

    # 重複を除きつつ上限まで採用
    seen: set[str] = set()
    out: list[str] = []
    for s in ordered:
        if s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= cap:
            break
    return out


# ---------------------------------------------------------------------------
# まとめ
# ---------------------------------------------------------------------------

def generate_comments(inp: SimulationInput, result: SimulationResult) -> CommentSet:
    """総合・要因・次に試す変更案をまとめて生成する。"""
    detected = _detect_factors(inp)
    factor_keys = [k for k, _ in detected]
    factor_texts = [t for _, t in detected][:2]  # 表示は最大 2 件

    return CommentSet(
        overall=_overall_comment(
            result.survival_probability, result.target_age, result.balance_at_target
        ),
        factors=factor_texts,
        next_tries=_next_try_comments(inp, result, factor_keys),
    )
