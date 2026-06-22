"""海外移住 資産寿命シミュレーター（簡易版）の計算ロジック。

このモジュールは UI（app.py）から切り離した「計算の中身」を担当します。
金額の単位はすべて「万円」、年率はすべて小数（例: 2% = 0.02）で扱います。

主な役割:
- 年金などの収入を各年齢の年額に展開する
- 1 試行ぶんの資産推移を計算する（参考シナリオ・モンテカルロ共通）
- モンテカルロ（1000 試行固定）を回して統計値を求める
- 参考シナリオ（利回り・インフレが毎年そのまま続く前提）を計算する
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

END_AGE = 100  # 計算は必ず 100 歳まで行う
MC_TRIALS = 1000  # モンテカルロ試行回数（仕様で固定）
RETURN_STD = 0.08  # 各年の運用利回りに乗せる正規分布の標準偏差（8%）
RETURN_CLIP = (-0.5, 0.5)  # 利回りが極端にならないよう上下限でクリップ
RANDOM_SEED = 42  # 同じ入力なら毎回同じ結果になるようシード固定

# 公的年金の受給開始年齢ごとの補正係数（65 歳時点年額を 100% とする）
PENSION_COEFFICIENTS = {
    60: 0.760,
    61: 0.808,
    62: 0.856,
    63: 0.904,
    64: 0.952,
    65: 1.000,
    66: 1.084,
    67: 1.168,
    68: 1.252,
    69: 1.336,
    70: 1.420,
}


# ---------------------------------------------------------------------------
# 入力データ
# ---------------------------------------------------------------------------

@dataclass
class IncomeStream:
    """企業年金・個人年金・その他収入など、期間のある年額収入を表す。

    end_age が None の場合は「終身（100 歳まで）」を意味する。
    """

    annual_amount: float  # 年額（万円）
    start_age: int
    end_age: int | None = None  # None = 終身


@dataclass
class SimulationInput:
    """シミュレーションに必要な入力一式（すべて日本円・万円ベース）。"""

    # 基本
    start_age: int  # 海外生活を始める年齢
    initial_hand_assets: float  # 海外生活開始時の手元資産（万円）

    # 日本の持ち家売却
    sell_japan_home: bool = False
    japan_home_proceeds: float = 0.0  # 売却後の手取り額（万円）

    # 生活費
    overseas_monthly_cost: float = 30.0  # 海外生活中の月額生活費（万円）

    # 日本帰国
    plan_to_return: bool = False
    return_age: int | None = None
    japan_monthly_cost: float = 30.0  # 日本帰国後の月額生活費（万円）

    # 海外の住まい売却（帰国時）
    sell_overseas_home: bool = False
    overseas_home_proceeds: float = 0.0  # 売却後の手取り額（万円）

    # 目標
    target_age: int = 90  # 資産を持たせたい年齢

    # 公的年金
    public_pension_at_65: float = 200.0  # 65 歳時点の年額（万円）
    public_pension_start_age: int = 65

    # 追加の年金・収入
    corporate_pensions: list[IncomeStream] = field(default_factory=list)
    other_incomes: list[IncomeStream] = field(default_factory=list)

    # 経済前提
    return_rate: float = 0.02  # 想定運用利回り（年率）
    inflation_rate: float = 0.02  # インフレ率（年率）


# ---------------------------------------------------------------------------
# 補助関数
# ---------------------------------------------------------------------------

def get_initial_assets(inp: SimulationInput) -> float:
    """初期資産 = 手元資産 + 日本の持ち家売却の手取り額。"""
    assets = inp.initial_hand_assets
    if inp.sell_japan_home:
        assets += inp.japan_home_proceeds
    return assets


def get_public_pension_annual(inp: SimulationInput) -> float:
    """受給開始年齢の補正係数を反映した公的年金の年額（万円）。"""
    coeff = PENSION_COEFFICIENTS.get(inp.public_pension_start_age, 1.0)
    return inp.public_pension_at_65 * coeff


def build_income_streams(inp: SimulationInput) -> list[IncomeStream]:
    """公的年金・企業年金・その他収入をまとめた収入ストリームの一覧を返す。"""
    streams: list[IncomeStream] = [
        IncomeStream(
            annual_amount=get_public_pension_annual(inp),
            start_age=inp.public_pension_start_age,
            end_age=None,  # 公的年金は終身
        )
    ]
    streams.extend(inp.corporate_pensions)
    streams.extend(inp.other_incomes)
    return streams


def income_at_age(streams: list[IncomeStream], age: int) -> float:
    """指定年齢で受け取れる収入の合計（万円）。終身は 100 歳まで継続。"""
    total = 0.0
    for s in streams:
        end = s.end_age if s.end_age is not None else END_AGE
        if s.start_age <= age <= end:
            total += s.annual_amount
    return total


def monthly_cost_at_age(inp: SimulationInput, age: int) -> float:
    """指定年齢の「月額」生活費（万円、インフレ反映前）。"""
    if inp.plan_to_return and inp.return_age is not None and age >= inp.return_age:
        return inp.japan_monthly_cost
    return inp.overseas_monthly_cost


# ---------------------------------------------------------------------------
# 1 試行ぶんの資産推移
# ---------------------------------------------------------------------------

def simulate_path(
    inp: SimulationInput,
    yearly_returns: np.ndarray,
) -> np.ndarray:
    """1 試行ぶんの資産推移を計算し、各年齢の年末残高（万円）を返す。

    yearly_returns は start_age から END_AGE までの各年の運用利回り（小数）。
    参考シナリオでは毎年同じ値、モンテカルロでは年ごとに変動する値を渡す。

    計算順序（コード全体で一貫させる）:
        1. 帰国時の海外住まい売却額をその年齢の年初に加算する
        2. 年初資産に利回りをかける
        3. そのあと収入を足し、生活費（インフレ反映後）を引く
    """
    ages = list(range(inp.start_age, END_AGE + 1))
    streams = build_income_streams(inp)

    balance = get_initial_assets(inp)
    path = np.empty(len(ages), dtype=float)

    for i, age in enumerate(ages):
        # 1. 帰国時の海外の住まい売却（その年齢の年初に現金化して運用対象に含める）
        if (
            inp.plan_to_return
            and inp.sell_overseas_home
            and inp.return_age is not None
            and age == inp.return_age
        ):
            balance += inp.overseas_home_proceeds

        # 2. 年初資産に利回りを反映
        balance *= (1.0 + yearly_returns[i])

        # 3. 収入を加算し、生活費（インフレ反映後）を減算
        annual_income = income_at_age(streams, age)

        # 生活費はインフレを毎年反映（start_age を基準年とし、その後複利で増加）
        inflation_factor = (1.0 + inp.inflation_rate) ** (age - inp.start_age)
        annual_expense = monthly_cost_at_age(inp, age) * 12.0 * inflation_factor

        balance += annual_income - annual_expense

        # 資産はマイナスでも止めずにそのまま記録（不足額を見せるため）
        path[i] = balance

    return path


# ---------------------------------------------------------------------------
# 参考シナリオ
# ---------------------------------------------------------------------------

def reference_scenario(inp: SimulationInput) -> np.ndarray:
    """想定運用利回り・インフレ率が毎年そのまま続く前提の資産推移。"""
    n_years = END_AGE - inp.start_age + 1
    fixed_returns = np.full(n_years, inp.return_rate, dtype=float)
    return simulate_path(inp, fixed_returns)


# ---------------------------------------------------------------------------
# モンテカルロ
# ---------------------------------------------------------------------------

@dataclass
class SimulationResult:
    """モンテカルロと参考シナリオの計算結果一式。"""

    ages: list[int]
    median_path: np.ndarray  # 標準ケース（中央値）
    low_path: np.ndarray  # 厳しめケース（下位 10%）
    high_path: np.ndarray  # 良好ケース（上位 10%）
    reference_path: np.ndarray  # 参考シナリオ

    survival_probability: float  # 資産が残る可能性（target_age で残高 >= 0 の試行割合）
    target_age: int
    balance_at_target: float  # target_age 時点の標準ケース残高（万円）

    depletion_age: int | None  # 資産が尽きる目安（標準ケースで初めてマイナスになる年齢）


def run_simulation(inp: SimulationInput) -> SimulationResult:
    """モンテカルロ（1000 試行固定）と参考シナリオを計算してまとめて返す。"""
    ages = list(range(inp.start_age, END_AGE + 1))
    n_years = len(ages)

    rng = np.random.default_rng(RANDOM_SEED)

    # 各試行・各年の運用利回り = 想定利回り + 正規分布の変動（クリップ付き）
    noise = rng.normal(loc=0.0, scale=RETURN_STD, size=(MC_TRIALS, n_years))
    yearly_returns = inp.return_rate + noise
    yearly_returns = np.clip(yearly_returns, RETURN_CLIP[0], RETURN_CLIP[1])

    paths = np.empty((MC_TRIALS, n_years), dtype=float)
    for t in range(MC_TRIALS):
        paths[t] = simulate_path(inp, yearly_returns[t])

    # 年齢ごとのパーセンタイル（ファンチャート）
    median_path = np.percentile(paths, 50, axis=0)
    low_path = np.percentile(paths, 10, axis=0)
    high_path = np.percentile(paths, 90, axis=0)

    # 資産が残る可能性: target_age で残高 >= 0 の試行割合
    target_idx = inp.target_age - inp.start_age
    target_idx = max(0, min(target_idx, n_years - 1))
    survival_probability = float(np.mean(paths[:, target_idx] >= 0.0))

    balance_at_target = float(median_path[target_idx])

    # 資産が尽きる目安: 標準ケース（中央値）で初めてマイナスになる年齢
    depletion_age: int | None = None
    for i, age in enumerate(ages):
        if median_path[i] < 0:
            depletion_age = age
            break

    reference_path = reference_scenario(inp)

    return SimulationResult(
        ages=ages,
        median_path=median_path,
        low_path=low_path,
        high_path=high_path,
        reference_path=reference_path,
        survival_probability=survival_probability,
        target_age=inp.target_age,
        balance_at_target=balance_at_target,
        depletion_age=depletion_age,
    )
