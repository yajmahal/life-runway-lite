"""海外移住 資産寿命シミュレーター（簡易版）。

Streamlit アプリ本体。入力 UI と結果表示を担当し、
計算は simulator.py、コメント生成は comments.py に委譲する。

すべての金額は日本円・万円ベース。年率は UI 上は % で扱い、内部では小数に変換する。
"""

from __future__ import annotations

import altair as alt
import pandas as pd
import streamlit as st

from comments import generate_comments
from simulator import (
    END_AGE,
    IncomeStream,
    SimulationInput,
    run_simulation,
)

# ---------------------------------------------------------------------------
# スライダー −／＋ ステッパー
# ---------------------------------------------------------------------------


def _step_session_value(key: str, delta: float, lo: float, hi: float, decimals: int):
    """−／＋ボタン用コールバック。step 単位で増減し min/max でクランプする。"""
    new_value = st.session_state[key] + delta
    new_value = max(lo, min(hi, new_value))
    st.session_state[key] = round(new_value, decimals) if decimals else int(round(new_value))


def _clamp_session_value(key: str, lo, hi, default):
    """描画前に session_state の値を min/max に収める。未初期化なら default を使う。"""
    if key not in st.session_state:
        st.session_state[key] = default
    st.session_state[key] = max(lo, min(hi, st.session_state[key]))


def slider_with_steppers(
    label: str,
    *,
    min_value,
    max_value,
    default,
    step,
    key: str,
    decimals: int = 0,
    help: str | None = None,
):
    """スライダー＋直下の −／＋ ボタン。値は st.session_state[key] で一元管理する。"""
    if key not in st.session_state:
        st.session_state[key] = default

    slider_kwargs: dict = {
        "label": label,
        "min_value": min_value,
        "max_value": max_value,
        "step": step,
        "key": key,
    }
    if help is not None:
        slider_kwargs["help"] = help
    st.slider(**slider_kwargs)

    with st.container(key=f"stepper_{key}", horizontal=True):
        st.button(
            "−",
            key=f"{key}__minus",
            width="stretch",
            on_click=_step_session_value,
            args=(key, -step, min_value, max_value, decimals),
            help=f"{step} 下げる",
        )
        st.button(
            "＋",
            key=f"{key}__plus",
            width="stretch",
            on_click=_step_session_value,
            args=(key, step, min_value, max_value, decimals),
            help=f"{step} 上げる",
        )
    return st.session_state[key]


# ---------------------------------------------------------------------------
# ページ設定とスタイル
# ---------------------------------------------------------------------------

st.set_page_config(
    page_title="海外移住 資産寿命シミュレーター（簡易版）",
    page_icon="🌏",
    layout="centered",
)

# 「計算する」ボタン（primary）をアクセントカラーで目立たせる
st.markdown(
    """
    <style>
    div.stButton > button[kind="primary"] {
        background-color: #e63946;
        color: #ffffff;
        border: none;
        font-weight: 700;
        padding: 0.6rem 1.2rem;
    }
    div.stButton > button[kind="primary"]:hover {
        background-color: #c92f3c;
        color: #ffffff;
    }
    .deficit-text { color: #e63946; font-weight: 700; }
    .surplus-text { color: #1d7a46; font-weight: 700; }
    .ref-amount-label { font-size: 0.95rem; color: #555555; margin-bottom: 0.1rem; }
    .ref-amount { font-size: 1.6rem; font-weight: 700; margin-bottom: 0.3rem; }
    [class*="st-key-stepper_"] {
        flex-direction: row !important;
        flex-wrap: nowrap !important;
        gap: 0.5rem !important;
    }
    [class*="st-key-stepper_"] > div {
        flex: 1 1 0 !important;
        min-width: 0 !important;
    }
    [class*="st-key-stepper_"] button {
        min-height: 0 !important;
        padding-top: 0.25rem !important;
        padding-bottom: 0.25rem !important;
        line-height: 1.2 !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------------------------------------------------------------------------
# 表示用のヘルパー
# ---------------------------------------------------------------------------

def format_man(value: float) -> str:
    """万円の金額をカンマ区切りの整数文字列にする（例: 1500 -> '1,500'）。"""
    return f"{round(value):,}"


# ---------------------------------------------------------------------------
# ヘッダー
# ---------------------------------------------------------------------------

st.title("海外移住 資産寿命シミュレーター（簡易版）")

st.markdown(
    "海外移住後の生活費、日本帰国、年金、手元資産をもとに、"
    "資産が何歳まで残るかを日本円ベースで簡易試算します。\n\n"
    "入力はすべて日本円ベースです。海外の生活費や不動産売却額も、"
    "日本円に換算して入力してください。\n\n"
    "金額は現在の物価感覚で入力してください。生活費の将来の増加は、"
    "入力したインフレ率をもとに自動で反映します。"
)

st.divider()


# ---------------------------------------------------------------------------
# 入力 UI
# ---------------------------------------------------------------------------

st.header("入力条件")

# 1. 資産寿命の目標年齢（試算の目的を決める項目なので一番上に配置）
target_age = slider_with_steppers(
    "資産寿命の目標年齢",
    min_value=80,
    max_value=100,
    default=90,
    step=1,
    key="target_age",
)

# 2. 海外生活を始める年齢
start_age = slider_with_steppers(
    "海外生活を始める年齢",
    min_value=55,
    max_value=75,
    default=60,
    step=1,
    key="start_age",
)

# 3. 海外生活開始時の手元資産（万円, 0〜2億 = 0〜20000, 100刻み）
initial_hand_assets = slider_with_steppers(
    "海外生活開始時の手元資産（万円）",
    min_value=0,
    max_value=20000,
    default=3000,
    step=100,
    key="initial_hand_assets",
    help="退職金や一時金を含め、海外生活を始める時点で使える金融資産の合計額を入力してください。",
)

# 4. 日本の持ち家を売却するか
sell_japan_home_choice = st.radio(
    "海外生活を始めるとき、日本の持ち家を売却しますか？",
    options=["しない", "する"],
    horizontal=True,
)
sell_japan_home = sell_japan_home_choice == "する"
japan_home_proceeds = 0
if sell_japan_home:
    japan_home_proceeds = slider_with_steppers(
        "売却後の手取り額（万円）／日本の持ち家",
        min_value=0,
        max_value=20000,
        default=0,
        step=100,
        key="japan_home_proceeds",
    )

# 5. 海外生活中の月額生活費
overseas_monthly_cost = slider_with_steppers(
    "海外生活中の月額生活費（万円）",
    min_value=5,
    max_value=100,
    default=30,
    step=1,
    key="overseas_monthly_cost",
)
st.caption(
    "タイ移住を想定している場合は、"
    "[タイ移住 月額生活費シミュレーター](https://life-spending-designer-lite-liferunway.streamlit.app/)"
    "で月額生活費の目安を確認できます。"
)

# 6. 日本に戻る予定
return_choice = st.radio(
    "日本に戻る予定",
    options=["なし", "あり"],
    horizontal=True,
)
plan_to_return = return_choice == "あり"

return_age: int | None = None
japan_monthly_cost = 30
sell_overseas_home = False
overseas_home_proceeds = 0

if plan_to_return:
    # 日本に戻る年齢は「海外生活を始める年齢 + 1」以上に制御する
    return_min = start_age + 1
    return_default = min(max(80, return_min), 100)
    _clamp_session_value("return_age", return_min, 100, return_default)
    return_age = slider_with_steppers(
        "日本に戻る年齢",
        min_value=return_min,
        max_value=100,
        default=return_default,
        step=1,
        key="return_age",
    )

    japan_monthly_cost = slider_with_steppers(
        "日本帰国後の月額生活費（万円）",
        min_value=5,
        max_value=100,
        default=30,
        step=1,
        key="japan_monthly_cost",
    )

    # 海外の住まい売却（帰国予定がある場合のみ表示）
    sell_overseas_choice = st.radio(
        "日本に戻るとき、海外の住まいを売却しますか？",
        options=["しない", "する"],
        horizontal=True,
    )
    sell_overseas_home = sell_overseas_choice == "する"
    if sell_overseas_home:
        overseas_home_proceeds = slider_with_steppers(
            "売却後の手取り額（万円）／海外の住まい",
            min_value=0,
            max_value=20000,
            default=0,
            step=100,
            key="overseas_home_proceeds",
        )

# 公的年金
st.subheader("公的年金")
public_pension_at_65 = slider_with_steppers(
    "65歳時点の公的年金年額（万円）",
    min_value=0,
    max_value=400,
    default=200,
    step=10,
    key="public_pension_at_65",
    help=(
        "65歳から受け取る公的年金の見込年額を入力してください。"
        "ねんきん定期便などに記載されている金額を目安にできます。"
        "この簡易版では、税金や社会保険料は個別に計算していません。"
        "保守的に見たい場合は、実際の受取額に近い金額を入力してください。"
    ),
)
public_pension_start_age = slider_with_steppers(
    "受給開始年齢",
    min_value=60,
    max_value=70,
    default=65,
    step=1,
    key="public_pension_start_age",
)


def render_income_streams(title: str, key_prefix: str) -> list[IncomeStream]:
    """企業年金・その他収入の入力 UI（最大 2 本）を折りたたみで表示する。

    年額が 0 のものは収入として数えない（= 未入力扱い）。
    """
    streams: list[IncomeStream] = []
    with st.expander(title):
        for i in range(2):
            st.markdown(f"**{i + 1} 本目**")
            annual = slider_with_steppers(
                "年額（万円）",
                min_value=0,
                max_value=500,
                default=0,
                step=10,
                key=f"{key_prefix}_amount_{i}",
            )
            s_age = slider_with_steppers(
                "受給開始年齢" if "pension" in key_prefix else "開始年齢",
                min_value=55,
                max_value=100,
                default=65 if "pension" in key_prefix else 60,
                step=1,
                key=f"{key_prefix}_start_{i}",
            )
            end_type = st.radio(
                "受給終了" if "pension" in key_prefix else "終了",
                options=["終身", "年齢指定"],
                horizontal=True,
                key=f"{key_prefix}_endtype_{i}",
            )
            end_age: int | None = None
            if end_type == "年齢指定":
                # 終了年齢は開始年齢以上に制御する
                end_key = f"{key_prefix}_end_{i}"
                _clamp_session_value(end_key, s_age, 100, s_age)
                end_age = slider_with_steppers(
                    "受給終了年齢" if "pension" in key_prefix else "終了年齢",
                    min_value=s_age,
                    max_value=100,
                    default=s_age,
                    step=1,
                    key=end_key,
                )
            if i == 0:
                st.divider()

            # 年額が入力されているものだけ収入として扱う
            if annual > 0:
                streams.append(
                    IncomeStream(annual_amount=annual, start_age=s_age, end_age=end_age)
                )
    return streams


# 9. 企業年金・個人年金
corporate_pensions = render_income_streams("企業年金・個人年金などがある場合", "corp_pension")

# 10. その他収入
other_incomes = render_income_streams("その他収入がある場合", "other_income")

# 11. 想定運用利回り
return_rate_pct = slider_with_steppers(
    "想定運用利回り（年率 %）",
    min_value=0.0,
    max_value=5.0,
    default=2.0,
    step=0.5,
    key="return_rate_pct",
    decimals=1,
    help=(
        "迷う場合は2〜4%程度を目安に試算してください。"
        "あわせて、想定より1%程度低い利回りでも試算すると、"
        "運用が下振れしたときの資産寿命への影響を確認しやすくなります。"
    ),
)

# 12. インフレ率
inflation_rate_pct = slider_with_steppers(
    "インフレ率（年率 %）",
    min_value=0.0,
    max_value=5.0,
    default=2.0,
    step=0.5,
    key="inflation_rate_pct",
    decimals=1,
    help=(
        "迷う場合は2%のままで試算してください。"
        "長期の物価上昇率は正確に予測できるものではないため、"
        "2%、3%、4%など複数の前提で比較すると、"
        "物価上昇が資産寿命に与える影響を確認しやすくなります。"
    ),
)

st.divider()

# ---------------------------------------------------------------------------
# 計算ボタン
# ---------------------------------------------------------------------------

# 入力データの取り扱い（計算前にユーザーが安心できるよう、ボタン直前に表示）
st.caption(
    "入力内容はこの試算のためだけに使用し、保存・外部提供・マーケティング利用は行いません。"
)

calc = st.button("計算する", type="primary", use_container_width=True)

if calc:
    sim_input = SimulationInput(
        start_age=start_age,
        initial_hand_assets=float(initial_hand_assets),
        sell_japan_home=sell_japan_home,
        japan_home_proceeds=float(japan_home_proceeds),
        overseas_monthly_cost=float(overseas_monthly_cost),
        plan_to_return=plan_to_return,
        return_age=return_age,
        japan_monthly_cost=float(japan_monthly_cost),
        sell_overseas_home=sell_overseas_home,
        overseas_home_proceeds=float(overseas_home_proceeds),
        target_age=int(target_age),
        public_pension_at_65=float(public_pension_at_65),
        public_pension_start_age=int(public_pension_start_age),
        corporate_pensions=corporate_pensions,
        other_incomes=other_incomes,
        return_rate=return_rate_pct / 100.0,
        inflation_rate=inflation_rate_pct / 100.0,
    )
    result = run_simulation(sim_input)
    comment_set = generate_comments(sim_input, result)

    # 再描画されても結果が残るようにセッションに保存
    st.session_state["sim_input"] = sim_input
    st.session_state["result"] = result
    st.session_state["comment_set"] = comment_set


# ---------------------------------------------------------------------------
# 結果表示
# ---------------------------------------------------------------------------

def render_results() -> None:
    """セッションに保存された計算結果を仕様の順番で表示する。"""
    sim_input: SimulationInput = st.session_state["sim_input"]
    result = st.session_state["result"]
    comment_set = st.session_state["comment_set"]

    prob_pct = round(result.survival_probability * 100)
    target = result.target_age
    balance_target = result.balance_at_target
    has_deficit = balance_target < 0

    st.divider()
    st.header("試算結果")

    # 試算方法の説明（目立つ場所に表示）
    st.info(
        "**1,000回のモンテカルロ試算で算出しています**\n\n"
        "このシミュレーターでは、運用利回りが毎年上下する前提で、1,000通りの資産推移を"
        "計算しています。モンテカルロ試算とは、将来起こりうる複数のパターンをまとめて計算し、"
        "結果のばらつきを見る方法です。"
    )

    # 1. ひとことで結果
    if has_deficit:
        st.markdown(
            f"### ひとことで結果\n"
            f"この前提では、**{target}歳時点で不足が出る可能性**があります。"
            f"{target}歳まで資産が残る可能性は **{prob_pct}%** です。"
        )
    else:
        st.markdown(
            f"### ひとことで結果\n"
            f"この前提では、**{target}歳まで資産が残る可能性は {prob_pct}%** です。"
            "前提を変えると結果がどう動くかも比較してみてください。"
        )

    # 2. 資産が残る可能性
    st.subheader("資産が残る可能性")
    st.metric(
        label=f"{target}歳まで資産が残る可能性",
        value=f"{prob_pct}%",
    )
    st.caption("1,000回のモンテカルロ試算にもとづく目安です。")

    # 3. 不足がある場合のアラート
    if has_deficit:
        st.error(
            "要確認：この前提では、資産を持たせたい年齢までに"
            "不足が出る可能性があります。"
        )

    # 4. コメント・読み解き
    st.subheader("コメント・読み解き")
    st.markdown(f"**総合コメント**\n\n{comment_set.overall}")
    if comment_set.factors:
        st.markdown("**要因コメント**")
        for f in comment_set.factors:
            st.markdown(f"- {f}")
    if comment_set.next_tries:
        st.markdown("**次に試す変更案**")
        for s in comment_set.next_tries:
            st.markdown(f"- {s}")

    # target_age 時点の残高（不足ならマイナス表示）
    if has_deficit:
        st.markdown(
            f"<p class='deficit-text'>{target}歳時点の不足額："
            f"{format_man(abs(balance_target))}万円</p>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<p class='surplus-text'>{target}歳時点の資産残高："
            f"{format_man(balance_target)}万円</p>",
            unsafe_allow_html=True,
        )

    # 5. 標準ケース / 厳しめケース / 良好ケース
    st.subheader("標準ケース / 厳しめケース / 良好ケース")
    target_idx = max(0, min(target - sim_input.start_age, len(result.ages) - 1))
    cases = [
        ("標準ケース（中央値）", result.median_path[target_idx]),
        ("厳しめケース（下位10%）", result.low_path[target_idx]),
        ("良好ケース（上位10%）", result.high_path[target_idx]),
    ]
    cols = st.columns(3)
    for col, (label, value) in zip(cols, cases):
        with col:
            st.markdown(f"**{label}**")
            if value < 0:
                col.markdown(
                    f"<p class='deficit-text'>不足 {format_man(abs(value))}万円</p>",
                    unsafe_allow_html=True,
                )
            else:
                col.markdown(
                    f"<p class='surplus-text'>{format_man(value)}万円</p>",
                    unsafe_allow_html=True,
                )
    st.caption(f"（いずれも{target}歳時点の資産残高）")

    # 6. 資産が尽きる目安
    st.subheader("資産が尽きる目安")
    if result.depletion_age is None:
        st.write(f"標準ケースでは、{END_AGE}歳まで資産が残る見込みです。")
    else:
        st.write(
            f"標準ケースでは、**{result.depletion_age}歳ごろ**に"
            "資産がマイナスに転じる目安です。"
        )

    # 7. 資産推移グラフ
    st.subheader("資産推移グラフ")
    st.markdown(
        "**グラフの見方：**\n"
        "- 青：標準ケース（中央値）\n"
        "- 赤：厳しめケース（下位10%）\n"
        "- 緑：良好ケース（上位10%）\n"
        "- グレーの破線：利回りとインフレ率が一定だった場合"
    )
    st.caption(
        "想定運用利回りとインフレ率が毎年同じ水準で続いた場合の資産推移です。"
        "モンテカルロ試算のように、年ごとの運用のぶれは含めていません。"
    )
    render_chart(result)

    # 8. 利回りとインフレ率が一定だった場合（補助的な目安）
    # 見出し → 資産残高（少し大きめ）→ 説明文 の順に表示する
    st.subheader("利回りとインフレ率が一定だった場合")
    ref_target = result.reference_path[target_idx]
    if ref_target < 0:
        st.markdown(
            f"<div class='ref-amount-label'>{target}歳時点の不足額</div>"
            f"<div class='ref-amount deficit-text'>不足 {format_man(abs(ref_target))}万円</div>",
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"<div class='ref-amount-label'>{target}歳時点の資産残高</div>"
            f"<div class='ref-amount'>{format_man(ref_target)}万円</div>",
            unsafe_allow_html=True,
        )
    st.caption(
        "想定運用利回りとインフレ率が毎年同じ水準で続いた場合の資産推移です。"
        "モンテカルロ試算のように、年ごとの運用のぶれは含めていません。"
    )


def render_chart(result) -> None:
    """100 歳までの資産推移グラフ（マイナス領域・0 円ライン付き）を描く。"""
    # 画面に出す系列名（内部の reference_path に対応する補助線のラベル）
    ref_label = "利回りとインフレ率が一定だった場合"

    # 4 本の線を long 形式の DataFrame にまとめる
    records = []
    line_defs = [
        ("標準ケース（中央値）", result.median_path),
        ("厳しめケース（下位10%）", result.low_path),
        ("良好ケース（上位10%）", result.high_path),
        (ref_label, result.reference_path),
    ]
    for name, path in line_defs:
        for age, value in zip(result.ages, path):
            records.append({"年齢": age, "資産残高（万円）": float(value), "ケース": name})
    df = pd.DataFrame(records)

    domain = [
        "標準ケース（中央値）",
        "厳しめケース（下位10%）",
        "良好ケース（上位10%）",
        ref_label,
    ]
    color_scale = alt.Scale(
        domain=domain,
        range=["#1f77b4", "#e63946", "#2ca02c", "#999999"],
    )
    # 補助線（利回りとインフレ率が一定だった場合）だけ破線にして補助的に見せる
    dash_scale = alt.Scale(
        domain=domain,
        range=[[1, 0], [1, 0], [1, 0], [6, 4]],
    )

    lines = (
        alt.Chart(df)
        .mark_line()
        .encode(
            x=alt.X("年齢:Q", scale=alt.Scale(domain=[result.ages[0], result.ages[-1]])),
            y=alt.Y("資産残高（万円）:Q"),
            color=alt.Color("ケース:N", scale=color_scale, title="ケース"),
            strokeDash=alt.StrokeDash("ケース:N", scale=dash_scale, legend=None),
        )
    )

    # 0 円ライン
    zero_line = (
        alt.Chart(pd.DataFrame({"y": [0]}))
        .mark_rule(color="#333333", strokeWidth=1.5)
        .encode(y="y:Q")
    )

    chart = (lines + zero_line).properties(height=400).interactive()
    st.altair_chart(chart, use_container_width=True)


if "result" in st.session_state:
    render_results()
else:
    st.info("入力条件を設定して「計算する」を押すと、試算結果が表示されます。")


# ---------------------------------------------------------------------------
# 免責文
# ---------------------------------------------------------------------------

st.sidebar.header("ご利用にあたって")
st.sidebar.info(
    "このシミュレーターは、海外移住後の生活設計を考えるための簡易試算です。"
    "将来の運用成果、物価、年金、税金、医療費などを保証するものではありません。"
)
st.sidebar.caption(
    "入力内容はこの試算のためだけに使用し、保存・外部提供・マーケティング利用は行いません。"
)

st.divider()
st.caption(
    "このシミュレーターは、海外移住後の生活設計を考えるための簡易試算です。"
    "将来の運用成果、物価、年金、税金、医療費などを保証するものではありません。"
)
st.caption(
    "入力内容はこの試算のためだけに使用し、保存・外部提供・マーケティング利用は行いません。"
)
