from __future__ import annotations

from dataclasses import dataclass, asdict
from math import exp
from typing import Optional


@dataclass
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class TradeIdea:
    bias: str
    action: str
    entry: Optional[float]
    entry_low: Optional[float]
    entry_high: Optional[float]
    sl: Optional[float]
    tp: Optional[float]
    rr_ratio: Optional[float]
    range_high: Optional[float]
    range_low: Optional[float]
    current: float
    reason: str
    trend_strength_pct: float
    breakout_probability_pct: float
    breakdown_probability_pct: float
    bounce_probability_pct: float
    support_level: float
    resistance_level: float
    rsi_pct: float
    structure_position_pct: float
    volume_ratio: float
    confidence_pct: float
    grade: str
    confidence_reasons: list[str]

    def to_dict(self):
        return asdict(self)


@dataclass
class StructureShift:
    broke_up: bool
    broke_down: bool
    support: float
    resistance: float
    prior_support: float
    prior_resistance: float


@dataclass
class ScenarioProbs:
    breakout: float
    breakdown: float
    bounce: float


@dataclass
class OrderBlock:
    entry: float
    sl: float


@dataclass
class SmartMoneySignal:
    event_type: str
    bullish_sweep: bool
    bearish_sweep: bool
    displacement_up: bool
    displacement_down: bool
    retest_hold: bool
    retest_fail: bool
    trigger_high: float
    trigger_low: float


def ema_from_candles(candles: list[Candle], period: int) -> list[float]:
    if not candles:
        return []
    k = 2.0 / (period + 1.0)
    out = [0.0] * len(candles)
    ema = candles[0].close
    out[0] = ema
    for i in range(1, len(candles)):
        ema = candles[i].close * k + ema * (1 - k)
        out[i] = ema
    return out


def atr14(candles: list[Candle]) -> float:
    if len(candles) < 15:
        return 0.0
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]
        p = candles[i - 1]
        tr = max(c.high - c.low, abs(c.high - p.close), abs(c.low - p.close))
        trs.append(tr)
    last14 = trs[-14:] if len(trs) >= 14 else trs
    return sum(last14) / len(last14) if last14 else 0.0


def rsi14_from_candles(candles: list[Candle]) -> float:
    if len(candles) < 15:
        return 50.0
    gains = []
    losses = []
    for i in range(1, len(candles)):
        diff = candles[i].close - candles[i - 1].close
        gains.append(max(diff, 0.0))
        losses.append(abs(min(diff, 0.0)))
    period = 14
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def avg_volume20(candles: list[Candle]) -> float:
    if not candles:
        return 0.0
    sample = candles[-20:] if len(candles) >= 20 else candles
    return sum(c.volume for c in sample) / len(sample)


def recent_swing_lows(candles: list[Candle], lookback: int = 50) -> list[float]:
    out = []
    if len(candles) < 5:
        return out
    start = max(2, len(candles) - lookback)
    end = len(candles) - 3
    for i in range(start, end + 1):
        low = candles[i].low
        if (
            low <= candles[i - 1].low
            and low <= candles[i - 2].low
            and low < candles[i + 1].low
            and low < candles[i + 2].low
        ):
            out.append(low)
    return out


def recent_swing_highs(candles: list[Candle], lookback: int = 50) -> list[float]:
    out = []
    if len(candles) < 5:
        return out
    start = max(2, len(candles) - lookback)
    end = len(candles) - 3
    for i in range(start, end + 1):
        high = candles[i].high
        if (
            high >= candles[i - 1].high
            and high >= candles[i - 2].high
            and high > candles[i + 1].high
            and high > candles[i + 2].high
        ):
            out.append(high)
    return out


def pick_support_level(candles: list[Candle], current: float, safe_atr: float) -> float:
    lows = [v for v in recent_swing_lows(candles, 60) if v <= current + safe_atr * 0.35]
    if lows:
        lows.sort()
        return lows[-1]
    fallback = min(c.low for c in candles[max(0, len(candles) - 30):])
    return min(current, fallback + safe_atr * 0.10)


def pick_resistance_level(candles: list[Candle], current: float, safe_atr: float) -> float:
    highs = [v for v in recent_swing_highs(candles, 60) if v >= current - safe_atr * 0.35]
    if highs:
        highs.sort()
        return highs[0]
    fallback = max(c.high for c in candles[max(0, len(candles) - 30):])
    return max(current, fallback - safe_atr * 0.10)


def next_resistance_above(candles: list[Candle], threshold: float, safe_atr: float) -> float:
    highs = [v for v in recent_swing_highs(candles, 120) if v > threshold + safe_atr * 0.15]
    if highs:
        highs.sort()
        return highs[0]
    recent_high = max(c.high for c in candles[max(0, len(candles) - 45):])
    return max(threshold + safe_atr * 1.4, recent_high + safe_atr * 0.8)


def next_support_below(candles: list[Candle], threshold: float, safe_atr: float) -> float:
    lows = [v for v in recent_swing_lows(candles, 120) if v < threshold - safe_atr * 0.15]
    if lows:
        lows.sort()
        return lows[-1]
    recent_low = min(c.low for c in candles[max(0, len(candles) - 45):])
    return min(threshold - safe_atr * 1.4, recent_low - safe_atr * 0.8)


def resolve_structure_shift(candles: list[Candle], current: float, safe_atr: float) -> StructureShift:
    raw_support = pick_support_level(candles, current, safe_atr)
    raw_resistance = pick_resistance_level(candles, current, safe_atr)

    broke_up = current > raw_resistance + safe_atr * 0.20
    broke_down = current < raw_support - safe_atr * 0.20

    support = raw_support
    resistance = raw_resistance

    if broke_up:
        support = max(raw_resistance - safe_atr * 0.20, raw_support)
        resistance = next_resistance_above(candles, current, safe_atr)
    elif broke_down:
        resistance = min(raw_support + safe_atr * 0.20, raw_resistance)
        support = next_support_below(candles, current, safe_atr)

    if resistance <= support:
        support = current - safe_atr * 1.2
        resistance = current + safe_atr * 1.2

    return StructureShift(
        broke_up=broke_up,
        broke_down=broke_down,
        support=support,
        resistance=resistance,
        prior_support=raw_support,
        prior_resistance=raw_resistance,
    )


def softmax_score(v: float) -> float:
    v = max(-6.0, min(6.0, v))
    return exp(v)


def normalize_scenario_scores(breakout_score: float, breakdown_score: float, bounce_score: float) -> ScenarioProbs:
    a = softmax_score(breakout_score)
    b = softmax_score(breakdown_score)
    c = softmax_score(bounce_score)
    total = max(1e-6, a + b + c)
    return ScenarioProbs(
        breakout=(a / total) * 100.0,
        breakdown=(b / total) * 100.0,
        bounce=(c / total) * 100.0,
    )


def detect_smart_money_signal(candles: list[Candle], safe_atr: float) -> SmartMoneySignal:
    if len(candles) < 8:
        return SmartMoneySignal("none", False, False, False, False, False, False, 0.0, 0.0)

    last = candles[-1]
    prev = candles[-2]

    highs = recent_swing_highs(candles, 40)
    lows = recent_swing_lows(candles, 40)

    last_swing_high = highs[-1] if highs else prev.high
    last_swing_low = lows[-1] if lows else prev.low

    body = abs(last.close - last.open)
    prev_body = abs(prev.close - prev.open)
    rng = max(1e-6, last.high - last.low)

    displacement_up = (
        last.close > last_swing_high
        and body > safe_atr * 0.55
        and body > prev_body * 1.2
        and last.close >= last.high - rng * 0.28
    )

    displacement_down = (
        last.close < last_swing_low
        and body > safe_atr * 0.55
        and body > prev_body * 1.2
        and last.close <= last.low + rng * 0.28
    )

    bullish_sweep = last.low < last_swing_low - safe_atr * 0.08 and last.close > last_swing_low
    bearish_sweep = last.high > last_swing_high + safe_atr * 0.08 and last.close < last_swing_high

    event_type = "none"
    prior_high_broken = prev.close > last_swing_high + safe_atr * 0.05
    prior_low_broken = prev.close < last_swing_low - safe_atr * 0.05

    if displacement_up:
        event_type = "bullish_choch" if prior_low_broken else "bullish_bos"
    elif displacement_down:
        event_type = "bearish_choch" if prior_high_broken else "bearish_bos"

    retest_hold = (
        event_type in ("bullish_bos", "bullish_choch")
        and last.low <= last_swing_high + safe_atr * 0.18
        and last.close >= last_swing_high
    )

    retest_fail = (
        event_type in ("bearish_bos", "bearish_choch")
        and last.high >= last_swing_low - safe_atr * 0.18
        and last.close <= last_swing_low
    )

    return SmartMoneySignal(
        event_type=event_type,
        bullish_sweep=bullish_sweep,
        bearish_sweep=bearish_sweep,
        displacement_up=displacement_up,
        displacement_down=displacement_down,
        retest_hold=retest_hold,
        retest_fail=retest_fail,
        trigger_high=last_swing_high,
        trigger_low=last_swing_low,
    )


def compute_confidence(
    trend_strength: float,
    breakout_prob: float,
    breakdown_prob: float,
    bounce_prob: float,
    rsi: float,
    volume_ratio: float,
    rr_ratio: Optional[float],
    bullish_smc: bool,
    bearish_smc: bool,
) -> tuple[float, str, list[str]]:
    score = 50.0
    reasons: list[str] = []

    dominant_prob = max(breakout_prob, breakdown_prob, bounce_prob)
    score += (trend_strength - 50.0) * 0.25
    score += (dominant_prob - 50.0) * 0.30

    if (rr_ratio or 0) >= 2.0:
        score += 10.0
        reasons.append("Strong reward/risk")
    elif (rr_ratio or 0) >= 1.5:
        score += 6.0
        reasons.append("Good reward/risk")
    elif rr_ratio is not None and rr_ratio < 1.1:
        score -= 10.0
        reasons.append("Weak reward/risk")

    if volume_ratio >= 1.2:
        score += 6.0
        reasons.append("Volume expansion")
    elif volume_ratio < 0.95:
        score -= 4.0
        reasons.append("Weak volume")

    if bullish_smc or bearish_smc:
        score += 8.0
        reasons.append("Smart-money structure")

    if rsi > 70 or rsi < 30:
        score -= 4.0
        reasons.append("Momentum extreme")

    confidence = max(5.0, min(99.0, score))

    if confidence >= 90.0:
        grade = "A+"
    elif confidence >= 80.0:
        grade = "A"
    elif confidence >= 70.0:
        grade = "B"
    elif confidence >= 60.0:
        grade = "C"
    else:
        grade = "Avoid"

    return confidence, grade, reasons


def find_order_block(candles: list[Candle], bullish: bool) -> Optional[OrderBlock]:
    if len(candles) < 8:
        return None

    lookback = candles[-20:] if len(candles) >= 20 else candles
    for i in range(len(lookback) - 3, 1, -1):
        c = lookback[i]
        prev = lookback[i - 1]

        if bullish:
            if prev.close < prev.open and c.close > c.high - (c.high - c.low) * 0.25:
                return OrderBlock(entry=prev.open, sl=prev.low)
        else:
            if prev.close > prev.open and c.close < c.low + (c.high - c.low) * 0.25:
                return OrderBlock(entry=prev.open, sl=prev.high)

    return None


def build_trade_idea(candles: list[Candle]) -> TradeIdea:
    current = candles[-1].close

    lookback_range = min(20, max(0, len(candles) - 2))
    range_high = None
    range_low = None
    if lookback_range >= 2:
        start = max(0, len(candles) - lookback_range - 1)
        end = max(1, len(candles) - 1)
        slc = candles[start:end]
        range_high = max(c.high for c in slc)
        range_low = min(c.low for c in slc)

    ema200 = ema_from_candles(candles, 200)
    ema50 = ema_from_candles(candles, 50)
    e200 = ema200[-1] if ema200 else current
    e50 = ema50[-1] if ema50 else current
    prev_e50 = ema50[-6] if len(ema50) >= 6 else e50
    prev_e200 = ema200[-6] if len(ema200) >= 6 else e200

    atr = atr14(candles)
    safe_atr = atr if atr > 0 else max(1.0, current * 0.001)

    recent = candles[max(0, len(candles) - 21):max(1, len(candles) - 1)]
    recent_high = max(c.high for c in recent)
    recent_low = min(c.low for c in recent)
    recent_mid = (recent_high + recent_low) / 2.0
    recent_width = max(safe_atr, recent_high - recent_low)

    structure = resolve_structure_shift(candles, current, safe_atr)

    support_level = structure.support
    resistance_level = structure.resistance

    if support_level > current:
        support_level = min(structure.prior_support, current - safe_atr * 0.15)
    if resistance_level < current:
        resistance_level = max(structure.prior_resistance, current + safe_atr * 0.15)
    if resistance_level <= support_level:
        support_level = current - safe_atr * 1.2
        resistance_level = current + safe_atr * 1.2

    structure_position_pct = max(
        0.0,
        min(
            100.0,
            ((current - support_level) / max(1e-7, resistance_level - support_level)) * 100.0,
        ),
    )

    rsi = rsi14_from_candles(candles)
    avg_vol20 = avg_volume20(candles)
    vol_ratio = 1.0 if avg_vol20 <= 0 else candles[-1].volume / avg_vol20

    price_vs_ema = max(-2.2, min(2.2, (current - e50) / safe_atr))
    ema_stack = 1.0 if e50 > e200 else (-1.0 if e50 < e200 else 0.0)
    ema50_slope = max(-1.6, min(1.6, (e50 - prev_e50) / safe_atr))
    ema200_slope = max(-1.2, min(1.2, (e200 - prev_e200) / safe_atr))
    rsi_bias = max(-1.5, min(1.5, (rsi - 50.0) / 18.0))
    vol_bias = max(-0.8, min(1.5, (vol_ratio - 1.0) / 1.0))
    structure_bias = max(-1.3, min(1.3, (current - recent_mid) / (recent_width / 2.0)))

    raw_score = (
        price_vs_ema * 0.20
        + ema_stack * 0.24
        + ema50_slope * 0.18
        + ema200_slope * 0.10
        + rsi_bias * 0.12
        + vol_bias * 0.06
        + structure_bias * 0.10
    )

    smc = detect_smart_money_signal(candles, safe_atr)
    bullish_smc = smc.event_type in ("bullish_bos", "bullish_choch")
    bearish_smc = smc.event_type in ("bearish_bos", "bearish_choch")

    hard_bullish_invalidation = current < structure.prior_support - safe_atr * 0.12 or (
        smc.displacement_down and current < structure.prior_support
    )
    hard_bearish_invalidation = current > structure.prior_resistance + safe_atr * 0.12 or (
        smc.displacement_up and current > structure.prior_resistance
    )

    score = max(
        -1.0,
        min(
            1.0,
            (raw_score / 1.05)
            + (0.12 if bullish_smc else 0.0)
            - (0.12 if bearish_smc else 0.0)
            + (0.06 if smc.bullish_sweep else 0.0)
            - (0.06 if smc.bearish_sweep else 0.0),
        ),
    )

    trend_strength_pct = max(
        5.0,
        min(
            100.0,
            abs(ema_stack) * 28
            + (abs(ema50_slope) / 1.6) * 22
            + (abs(ema200_slope) / 1.2) * 14
            + (abs(price_vs_ema) / 2.2) * 16
            + (abs(structure_bias) / 1.3) * 12
            + abs(vol_bias) * 8
            + (6.0 if (smc.displacement_up or smc.displacement_down) else 0.0),
        ),
    )

    bullish_ob = find_order_block(candles, bullish=True)
    bearish_ob = find_order_block(candles, bullish=False)

    close_strength = max(
        -1.0,
        min(1.0, (candles[-1].close - candles[-1].open) / max(1e-7, candles[-1].high - candles[-1].low)),
    )

    dist_to_resistance_atr = max(-3.0, min(3.0, (resistance_level - current) / safe_atr))
    dist_to_support_atr = max(-3.0, min(3.0, (current - support_level) / safe_atr))

    breakout_score = (
        score * 1.55
        + max(0.0, ema50_slope) * 0.70
        + max(0.0, ema200_slope) * 0.28
        + max(0.0, (rsi - 55.0) / 12.0) * 0.55
        + max(0.0, vol_ratio - 1.0) * 0.30
        + max(0.0, -dist_to_resistance_atr) * 0.65
        + max(0.0, close_strength) * 0.35
        + (1.20 if structure.broke_up else 0.0)
        + (0.65 if bullish_smc else 0.0)
        + (0.30 if smc.retest_hold else 0.0)
        + (0.24 if smc.bullish_sweep else 0.0)
        - (0.35 if smc.retest_fail else 0.0)
    )

    breakdown_score = (
        (-score) * 1.55
        + max(0.0, -ema50_slope) * 0.70
        + max(0.0, -ema200_slope) * 0.28
        + max(0.0, (45.0 - rsi) / 12.0) * 0.55
        + max(0.0, vol_ratio - 1.0) * 0.30
        + max(0.0, -dist_to_support_atr) * 0.65
        + max(0.0, -close_strength) * 0.35
        + (1.20 if structure.broke_down else 0.0)
        + (0.65 if bearish_smc else 0.0)
        + (0.30 if smc.retest_fail else 0.0)
        + (0.24 if smc.bearish_sweep else 0.0)
        - (0.35 if smc.retest_hold else 0.0)
    )

    bounce_score = (
        max(0.0, score) * 0.65
        + max(0.0, (48.0 - dist_to_support_atr * 12.0) / 25.0) * 0.45
        + max(0.0, (55.0 - rsi) / 18.0) * 0.18
        + max(0.0, vol_ratio - 1.0) * 0.10
        + (0.18 if bullish_ob and bullish_ob.entry <= current + safe_atr else 0.0)
        + (0.24 if smc.bullish_sweep else 0.0)
        - (0.20 if structure.broke_up else 0.0)
        - (0.28 if structure.broke_down else 0.0)
    )

    probs = normalize_scenario_scores(breakout_score, breakdown_score, bounce_score)
    breakout_probability_pct = max(5.0, min(95.0, probs.breakout))
    breakdown_probability_pct = max(5.0, min(95.0, probs.breakdown))
    bounce_probability_pct = max(5.0, min(95.0, probs.bounce))

    bullish_bias = score >= 0.24 and trend_strength_pct >= 48
    bearish_bias = score <= -0.24 and trend_strength_pct >= 48

    if hard_bullish_invalidation and not hard_bearish_invalidation:
        bullish_bias = False
        bearish_bias = True
    elif hard_bearish_invalidation and not hard_bullish_invalidation:
        bearish_bias = False
        bullish_bias = True
    elif smc.displacement_down and breakdown_probability_pct >= breakout_probability_pct + 8:
        bullish_bias = False
        bearish_bias = True
    elif smc.displacement_up and breakout_probability_pct >= breakdown_probability_pct + 8:
        bearish_bias = False
        bullish_bias = True

    bias = "Bullish Bias" if bullish_bias else ("Bearish Bias" if bearish_bias else "Neutral")

    entry = None
    entry_low = None
    entry_high = None
    tp = None
    sl = None
    rr_ratio = None
    action = "Neutral structure"

    if bullish_bias:
        breakout_confirmed = structure.broke_up or breakout_probability_pct >= 58 or bullish_smc
        preferred_zone_low = (
            max(support_level, bullish_ob.entry - safe_atr * 0.18)
            if bullish_ob
            else max(support_level, e50 - safe_atr * 0.28)
        )
        preferred_zone_high = (
            min(current, bullish_ob.entry + safe_atr * 0.18)
            if bullish_ob
            else min(current, e50 + safe_atr * 0.22)
        )
        entry_low = min(preferred_zone_low, preferred_zone_high)
        entry_high = max(preferred_zone_low, preferred_zone_high)
        too_extended = current > entry_high + safe_atr * 0.55
        entry = current if breakout_confirmed and not too_extended else (entry_low + entry_high) / 2.0
        sl = (min(support_level, bullish_ob.sl) - safe_atr * 0.10) if bullish_ob else support_level - safe_atr * 0.35
        tp = max(resistance_level, current + safe_atr * 1.35)
        risk = max(1e-7, entry - sl)
        reward = max(0.0, tp - entry)
        rr_ratio = reward / risk if risk > 0 else 0.0
        action = "BUY"
        if hard_bullish_invalidation or smc.displacement_down:
            action = "HOLD"

    elif bearish_bias:
        breakdown_confirmed = structure.broke_down or breakdown_probability_pct >= 58 or bearish_smc
        preferred_zone_high = (
            min(resistance_level, bearish_ob.entry + safe_atr * 0.18)
            if bearish_ob
            else min(resistance_level, e50 + safe_atr * 0.28)
        )
        preferred_zone_low = (
            max(current, bearish_ob.entry - safe_atr * 0.18)
            if bearish_ob
            else max(current, e50 - safe_atr * 0.22)
        )
        entry_low = min(preferred_zone_low, preferred_zone_high)
        entry_high = max(preferred_zone_low, preferred_zone_high)
        too_extended = current < entry_low - safe_atr * 0.55
        entry = current if breakdown_confirmed and not too_extended else (entry_low + entry_high) / 2.0
        sl = (max(resistance_level, bearish_ob.sl) + safe_atr * 0.10) if bearish_ob else resistance_level + safe_atr * 0.35
        tp = min(support_level, current - safe_atr * 1.35)
        risk = max(1e-7, sl - entry)
        reward = max(0.0, entry - tp)
        rr_ratio = reward / risk if risk > 0 else 0.0
        action = "SELL"
        if hard_bearish_invalidation or smc.displacement_up:
            action = "HOLD"

    confidence_pct, grade, confidence_reasons = compute_confidence(
        trend_strength=trend_strength_pct,
        breakout_prob=breakout_probability_pct,
        breakdown_prob=breakdown_probability_pct,
        bounce_prob=bounce_probability_pct,
        rsi=rsi,
        volume_ratio=vol_ratio,
        rr_ratio=rr_ratio,
        bullish_smc=bullish_smc,
        bearish_smc=bearish_smc,
    )

    return TradeIdea(
        bias=bias,
        action=action,
        entry=entry,
        entry_low=entry_low,
        entry_high=entry_high,
        sl=sl,
        tp=tp,
        rr_ratio=rr_ratio,
        range_high=range_high,
        range_low=range_low,
        current=current,
        reason="Python advanced engine",
        trend_strength_pct=trend_strength_pct,
        breakout_probability_pct=breakout_probability_pct,
        breakdown_probability_pct=breakdown_probability_pct,
        bounce_probability_pct=bounce_probability_pct,
        support_level=support_level,
        resistance_level=resistance_level,
        rsi_pct=rsi,
        structure_position_pct=structure_position_pct,
        volume_ratio=vol_ratio,
        confidence_pct=confidence_pct,
        grade=grade,
        confidence_reasons=confidence_reasons,
    )