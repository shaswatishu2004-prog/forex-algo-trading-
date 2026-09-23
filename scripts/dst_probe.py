"""Measure which DST schedule HistData stamps followed, per year.

For each symbol's 1-minute files, take each Friday's last-bar stamp hour and
classify Fridays into the windows where a US-DST schedule and an EU-DST
schedule disagree:

  W1 = (US spring change, EU spring change]   market already on summer hours
  W2 = (EU fall change,   US fall change]     market still on summer hours

If stamps track the market (US schedule), W1/W2 Friday hours equal the same
year's June-Friday hour. If stamps follow the EU schedule (stamps still on
winter offsets while the market is on summer hours), those Fridays read one
hour lower. Baselines: June (summer alignment) and December (winter).

Run from the repository root:  .venv/Scripts/python.exe scripts/dst_probe.py
"""

from __future__ import annotations

import glob
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ("EURUSD", "XAUUSD", "DXY")


def nth_sunday(year: int, month: int, nth: int) -> pd.Timestamp:
    """nth=1 first Sunday, nth=-1 last Sunday."""
    first = pd.Timestamp(year=year, month=month, day=1)
    d = first + pd.Timedelta(days=(6 - first.weekday()) % 7)
    sundays = []
    while d.month == month:
        sundays.append(d)
        d += pd.Timedelta(days=7)
    if nth == -1:
        return sundays[-1].normalize()
    return sundays[nth - 1].normalize()


def friday_hours(symbol: str) -> dict[pd.Timestamp, int]:
    files = sorted(glob.glob(str(ROOT / "data" / "raw" / "histdata" / symbol / "**" / "*.csv")))
    if not files:
        print(f"{symbol}: NO CSV FILES FOUND")
        return {}
    stamps: list[pd.Series] = []
    for f in files:
        col = pd.read_csv(f, sep=";", header=None, usecols=[0], dtype=str).iloc[:, 0]
        ts = pd.to_datetime(col, format="%Y%m%d %H%M%S", errors="coerce")
        if ts.notna().mean() < 0.9:
            ts = pd.to_datetime(col, errors="coerce")
        stamps.append(ts.dropna())
    all_ts = pd.concat(stamps, ignore_index=True)
    fridays = all_ts[all_ts.dt.dayofweek == 4]
    last_by_day = fridays.groupby(fridays.dt.date).max()
    print(f"{symbol}: {len(files)} files, {len(all_ts):,} rows, {len(last_by_day)} Fridays")
    return {pd.Timestamp(d): ts.hour for d, ts in last_by_day.items()}


def modal(hours: list[int]) -> str:
    if not hours:
        return "-"
    s = pd.Series(hours)
    return f"{int(s.mode().iloc[0])}({len(hours)})"


def main() -> None:
    for symbol in SYMBOLS:
        per_friday = friday_hours(symbol)
        if not per_friday:
            continue
        print(f"\n=== {symbol}: modal last-Friday-bar stamp hour (n Fridays) ===")
        print("year | Jun(summer base) | Dec(winter base) | W1 (USspr->EUspr) | W2 (EUFall->USFall)")
        for year in range(2000, 2027):
            jun = [h for d, h in per_friday.items() if d.year == year and d.month == 6]
            dec = [h for d, h in per_friday.items() if d.year == year and d.month == 12]
            if not jun and not dec and not any(d.year == year for d in per_friday):
                continue
            try:
                us_spr = nth_sunday(year, 3, 2)
                eu_spr = nth_sunday(year, 3, -1)
                eu_fall = nth_sunday(year, 10, -1)
                us_fall = nth_sunday(year, 11, 1)
            except IndexError:
                continue
            w1 = [h for d, h in per_friday.items() if d.year == year and us_spr < d <= eu_spr]
            w2 = [h for d, h in per_friday.items() if d.year == year and eu_fall < d <= us_fall]
            print(
                f"{year} | {modal(jun):>15} | {modal(dec):>15} | "
                f"{modal(w1):>17} | {modal(w2):>17}"
            )
        print("rule: W hour == Jun hour => US schedule; == Jun hour - 1 => EU schedule")
    print(
        "\nExpected for US spring/fall dates: e.g. 2026 US spring "
        f"{nth_sunday(2026, 3, 2).date()}, EU spring {nth_sunday(2026, 3, -1).date()}, "
        f"EU fall {nth_sunday(2026, 10, -1).date()}, US fall {nth_sunday(2026, 11, 1).date()}"
    )


if __name__ == "__main__":
    main()
