"""海外移住 資産寿命シミュレーター（簡易版）のルールベースのコメント生成。

AI API は使わず、入力条件と計算結果から固定ルールでコメントを組み立てます。
コメントは「ひとことで結果」「総合コメント」「要因コメント」「次に試す変更案」の 4 部構成です。

設計の方針:
- モンテカルロの成功率・中央値残高・下位10%を踏まえ、結果と整合した文言にする。
- 要因コメントは一般論ではなく、今回の結果を押し上げ／悪化させている条件を優先する。
- 「次に試す変更案」は入力を変えて比較すべき項目を提案する。
"""

from __future__ import annotations

from dataclasses import dataclass

from simulator import SimulationInput, SimulationResult, get_initial_assets

# コメント用の簡易しきい値（万円）。あくまで読み解きの目安。
SLIGHT_SURPLUS = 500
LIMITED_SURPLUS = 1000


@dataclass
class CommentSet:
    """生成したコメント一式。"""

    summary: str  # ひとことで結果
    overall: str  # 総合コメント
    factors: list[str]  # 要因コメント（最大 2 件）
    next_tries: list[str]  # 次に試す変更案（最大 3 件）


def _pct(probability: float) -> int:
    return round(probability * 100)


def _target_idx(inp: SimulationInput, result: SimulationResult) -> int:
    return max(0, min(inp.target_age - inp.start_age, len(result.ages) - 1))


def _has_corporate_pension(inp: SimulationInput) -> bool:
    return any(s.annual_amount > 0 for s in inp.corporate_pensions)


def _has_other_income(inp: SimulationInput) -> bool:
    return any(s.annual_amount > 0 for s in inp.other_incomes)


# ---------------------------------------------------------------------------
# 1. ひとことで結果
# ---------------------------------------------------------------------------

def _summary_comment(
    probability: float, target_age: int, balance_at_target: float
) -> str:
    """成功率と中央値残高を端的に伝える。"""
    pct = _pct(probability)

    if balance_at_target < 0:
        if pct < 30:
            return (
                f"この前提では、{target_age}歳まで資産が残る可能性は低めです。"
            )
        return (
            f"この前提では、{target_age}歳まで資産が残る可能性はやや不安があります。"
        )

    if pct >= 100:
        return f"この前提では、{target_age}歳まで資産が残る可能性は100%です。"
    if pct >= 80:
        return f"この前提では、{target_age}歳まで資産が残る可能性は高めです。"
    if pct >= 60:
        return (
            f"この前提では、{target_age}歳まで資産が残る可能性は一定程度あります。"
        )
    if pct >= 30:
        return (
            f"この前提では、{target_age}歳まで資産が残る可能性はやや不安があります。"
        )
    return f"この前提では、{target_age}歳まで資産が残る可能性は低めです。"


# ---------------------------------------------------------------------------
# 2. 総合コメント
# ---------------------------------------------------------------------------

def _balance_note(balance_at_target: float) -> str:
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


def _low_case_note(low_at_target: float, target_age: int) -> str:
    """厳しめケース（下位10%）の補足。"""
    if low_at_target >= 0:
        return ""
    return (
        f"ただし、厳しめケース（下位10%）では{target_age}歳時点で不足が出る試行もあり、"
        "運用が下振れした場合の余裕は限定的です。"
    )


def _overall_comment(
    probability: float,
    target_age: int,
    balance_at_target: float,
    low_at_target: float,
) -> str:
    pct = _pct(probability)

    if pct >= 100:
        base = (
            f"このシミュレーション結果では、{target_age}歳まで資産が残る試行が100%でした。"
            "ただし、これは現在の入力条件にもとづく結果であり、"
            "生活費・インフレ率・運用利回りを変えた場合の確認も有用です。"
        )
    elif pct >= 95:
        base = (
            f"この前提では、{target_age}歳まで資産が残る可能性はかなり高めです。"
            "ただし、将来の運用や物価は変動するため、生活費やインフレ率を少し厳しめにした"
            "ケースも確認しておくと見通しを持ちやすくなります。"
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
            "成立は見えていますが、下振れには注意が必要です。"
            "生活費や年金開始までの期間など、影響が大きい前提を少し変えて"
            "比較してみると、改善余地が見えやすくなります。"
        )
    elif pct >= 30:
        base = (
            f"この前提では、{target_age}歳まで資産が残る可能性は{ pct }%です。"
            "資産寿命には不安が残る結果です。"
            "改善方向の前提変更を試す価値があります。"
        )
    else:
        base = (
            f"この前提では、{target_age}歳まで資産が残る可能性は{ pct }%です。"
            "資産が不足するリスクが高めです。"
            "生活費、開始年齢、年金、手元資産など、複数の前提を見直した"
            "ケースを確認する必要があります。"
        )

    note = _balance_note(balance_at_target)
    low_note = _low_case_note(low_at_target, target_age) if pct >= 95 else ""
    return base + note + low_note


# ---------------------------------------------------------------------------
# 3. 要因コメント（結果と入力条件に対応）
# ---------------------------------------------------------------------------

def _detect_factors(inp: SimulationInput, result: SimulationResult) -> list[tuple[str, str]]:
    """結果の良し悪しに応じて、優先度付きで要因を返す。"""
    pct = _pct(result.survival_probability)
    balance = result.balance_at_target
    idx = _target_idx(inp, result)
    low_balance = float(result.low_path[idx])

    initial_assets = get_initial_assets(inp)
    annual_living = inp.overseas_monthly_cost * 12
    pension_gap = inp.public_pension_start_age - inp.start_age
    strong_result = pct >= 95 and balance >= LIMITED_SURPLUS
    weak_result = pct < 60 or balance < 0

    positive: list[tuple[str, str, int]] = []
    negative: list[tuple[str, str, int]] = []

    if inp.sell_japan_home and inp.japan_home_proceeds > 0:
        priority = 3 if inp.japan_home_proceeds >= 1000 else 2
        positive.append((
            "japan_home_sale",
            "日本の持ち家売却により、海外生活開始時点の手元資産が厚くなる前提です。",
            priority,
        ))

    if _has_corporate_pension(inp):
        positive.append((
            "corp_pension",
            "企業年金・個人年金などの収入が、資産の取り崩しを抑える要因になっています。",
            3,
        ))

    if inp.return_rate > inp.inflation_rate + 0.005:
        positive.append((
            "return_spread",
            "想定運用利回りがインフレ率を上回っており、"
            "長期の資産減少を抑える前提です。",
            2,
        ))

    if (
        strong_result
        and initial_assets >= annual_living * 10
        and inp.public_pension_at_65 > 0
    ):
        positive.append((
            "stable_balance",
            "生活費に対して、初期資産と年金収入のバランスが比較的安定しています。",
            2,
        ))

    if inp.plan_to_return and inp.sell_overseas_home and inp.overseas_home_proceeds > 0:
        positive.append((
            "overseas_home_sale",
            "日本に戻るときに海外の住まいを売却する設定のため、"
            "帰国時点で資産残高が回復する前提です。",
            2,
        ))

    if _has_other_income(inp):
        positive.append((
            "other_income",
            "その他収入があるため、公的年金開始前の資産取り崩しを"
            "一部抑える前提です。",
            1,
        ))

    if not inp.sell_japan_home and weak_result:
        negative.append((
            "no_japan_home",
            "日本の持ち家売却を見込まないため、初期資産の上乗せがない前提です。",
            3,
        ))

    if pension_gap >= 3 and not strong_result:
        negative.append((
            "pension_gap",
            "公的年金の受給開始までの取り崩し期間が長く、"
            "序盤の資産減少が大きくなりやすい前提です。",
            3 if pension_gap >= 5 else 2,
        ))

    if inp.overseas_monthly_cost >= 35 or (
        annual_living > 0 and initial_assets / annual_living < 12
    ):
        negative.append((
            "high_living",
            "海外生活開始時点の手元資産に対して、月額生活費がやや重い前提です。",
            3 if inp.overseas_monthly_cost >= 35 else 2,
        ))

    if inp.inflation_rate >= inp.return_rate - 0.005:
        negative.append((
            "inflation_pressure",
            "インフレ率が想定運用利回りに近く、"
            "長期では生活費上昇の影響を受けやすい前提です。",
            2,
        ))

    if not _has_corporate_pension(inp) and not _has_other_income(inp) and weak_result:
        negative.append((
            "no_extra_income",
            "企業年金・個人年金やその他収入がないため、"
            "公的年金と手元資産への取り崩し依存が高い前提です。",
            4,
        ))

    if inp.inflation_rate >= 0.03:
        negative.append((
            "high_inflation",
            "インフレ率を高めに設定しているため、"
            "後半になるほど生活費の負担が大きくなります。",
            2,
        ))

    if inp.plan_to_return and weak_result:
        negative.append((
            "return_plan",
            "日本に戻る時期と帰国後の生活費は、後半の資産残高に大きく影響します。",
            1,
        ))

    if strong_result:
        pool = sorted(positive, key=lambda x: -x[2])
    elif weak_result:
        pool = sorted(negative, key=lambda x: -x[2])
        if len(pool) < 2:
            pool += sorted(positive, key=lambda x: -x[2])
    else:
        pool = sorted(negative, key=lambda x: -x[2]) + sorted(
            positive, key=lambda x: -x[2]
        )

    if not pool:
        pool = [(
            "generic",
            "現在の前提では、生活費と年金収入のバランスが資産寿命に大きく影響します。",
            0,
        )]

    seen: set[str] = set()
    factors: list[tuple[str, str]] = []
    for key, text, _ in pool:
        if key not in seen:
            seen.add(key)
            factors.append((key, text))
        if len(factors) >= 2:
            break
    return factors


# ---------------------------------------------------------------------------
# 4. 次に試す変更案
# ---------------------------------------------------------------------------

_IMPROVEMENT_BY_FACTOR = {
    "high_living": (
        "海外生活中の月額生活費を1〜3万円下げた場合を試してみてください。"
        "資産が残る可能性がどの程度改善するかを確認できます。"
    ),
    "pension_gap": (
        "海外生活を始める年齢を1〜2年遅らせた場合も確認してみてください。"
        "公的年金の受給開始までの取り崩し期間が短くなり、結果が改善する可能性があります。"
    ),
    "no_extra_income": (
        "年金開始前の収入を追加した場合も試してみてください。"
        "公的年金の受給開始までの取り崩しを抑えられるか確認できます。"
    ),
}

_CONSERVATIVE_BY_FACTOR = {
    "high_inflation": (
        "インフレ率を少し高めにした場合も確認してみてください。"
        "後半の生活費負担が結果にどの程度影響するかを把握できます。"
    ),
    "inflation_pressure": (
        "インフレ率を少し高めにした場合も確認してみてください。"
        "後半の生活費負担が結果にどの程度影響するかを把握できます。"
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
    pct = _pct(result.survival_probability)

    improvement: list[str] = []
    conservative: list[str] = []

    if pct < 80:
        improvement.append(
            "海外生活中の月額生活費を1〜3万円下げた場合を試してみてください。"
            "資産が残る可能性がどの程度改善するかを確認できます。"
        )

    if inp.public_pension_start_age > inp.start_age and pct < 80:
        improvement.append(
            "海外生活を始める年齢を1〜2年遅らせた場合も確認してみてください。"
            "公的年金の受給開始までの取り崩し期間が短くなり、結果が改善する可能性があります。"
        )

    conservative.append(
        "インフレ率を少し高めにした場合も確認してみてください。"
        "後半の生活費負担が結果にどの程度影響するかを把握できます。"
    )
    conservative.append(
        "想定運用利回りを少し低めにした場合も確認してみてください。"
        "運用が想定より振るわなかった場合の影響を確認できます。"
    )

    if inp.target_age < 100:
        conservative.append(
            f"目標年齢を{inp.target_age + 5}歳・100歳に上げた場合も確認してみてください。"
            "資産寿命の目標を変えたときの成功率の変化を比較できます。"
        )

    if inp.sell_japan_home:
        conservative.append(
            "日本の持ち家の売却後の手取り額を少し保守的に見た場合も確認してみてください。"
            "売却価格が想定より低かった場合の影響を把握しやすくなります。"
        )

    for key in factor_keys:
        if key in _IMPROVEMENT_BY_FACTOR:
            improvement.insert(0, _IMPROVEMENT_BY_FACTOR[key])
        if key in _CONSERVATIVE_BY_FACTOR:
            conservative.insert(0, _CONSERVATIVE_BY_FACTOR[key])

    if pct >= 95:
        ordered = conservative
        cap = 2
    elif pct >= 60:
        ordered = []
        if improvement:
            ordered.append(improvement[0])
        if conservative:
            ordered.append(conservative[0])
        ordered += improvement[1:] + conservative[1:]
        cap = 3
    else:
        ordered = improvement + conservative
        cap = 3

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
    """ひとこと・総合・要因・次に試す変更案をまとめて生成する。"""
    idx = _target_idx(inp, result)
    low_at_target = float(result.low_path[idx])

    detected = _detect_factors(inp, result)
    factor_keys = [k for k, _ in detected]
    factor_texts = [t for _, t in detected]

    return CommentSet(
        summary=_summary_comment(
            result.survival_probability, result.target_age, result.balance_at_target
        ),
        overall=_overall_comment(
            result.survival_probability,
            result.target_age,
            result.balance_at_target,
            low_at_target,
        ),
        factors=factor_texts,
        next_tries=_next_try_comments(inp, result, factor_keys),
    )
