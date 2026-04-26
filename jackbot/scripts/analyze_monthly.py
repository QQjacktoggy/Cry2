import json
from collections import defaultdict
from pathlib import Path

def analyze_monthly(filepath: str):
    with open(filepath, "r", encoding="utf-8") as f:
        data = json.load(f)

    daily_detail = data.get("daily_detail", {})
    if not daily_detail:
        print("No daily detail found.")
        return

    # Group by YYYY-MM
    monthly_stats = defaultdict(lambda: {
        "days": 0,
        "profit": 0.0,
        "halted_days": 0,
        "start_capital": None,
        "end_capital": None
    })

    sorted_dates = sorted(daily_detail.keys())
    
    for date_str in sorted_dates:
        month_str = date_str[:7] # YYYY-MM
        day_data = daily_detail[date_str]
        
        stats = monthly_stats[month_str]
        stats["days"] += 1
        stats["profit"] += day_data["profit"]
        if day_data["halted"]:
            stats["halted_days"] += 1
            
        cap = day_data.get("capital_end_of_day", 150.0)
        stats["end_capital"] = cap
        if stats["start_capital"] is None:
            # The start capital of the month is approximately end_capital - profit (compounded)
            # Actually, we can just take the end_capital of the PREVIOUS day, but for the first day we assume 150.0
            stats["start_capital"] = 150.0 # will fix below

    # Fix start_capital based on previous month's end_capital
    prev_end = 150.0
    for month_str in sorted(monthly_stats.keys()):
        monthly_stats[month_str]["start_capital"] = prev_end
        prev_end = monthly_stats[month_str]["end_capital"]

    print("\n📅 半年 (180天) 每月績效分析:")
    print("=" * 60)
    
    total_profit = 0
    start_cap = 150.0
    final_cap = 150.0

    for month_str in sorted(monthly_stats.keys()):
        stats = monthly_stats[month_str]
        start_c = stats["start_capital"]
        end_c = stats["end_capital"]
        profit = stats["profit"]
        halted = stats["halted_days"]
        days = stats["days"]
        
        roi = (profit / start_c) * 100 if start_c > 0 else 0
        
        print(f"🔹 {month_str} ({days} 天)")
        print(f"   初始本金: ${start_c:.2f} ➔ 結算本金: ${end_c:.2f}")
        print(f"   本月淨利: ${profit:.2f} (單月 ROI: +{roi:.2f}%)")
        print(f"   停機天數 (Halted): {halted} 天")
        print("-" * 60)
        
        total_profit += profit
        final_cap = end_c

    total_roi = ((final_cap - start_cap + (total_profit * 0.5)) / start_cap) * 100 # Rough estimate
    
    print(f"🏆 總結:")
    print(f"   初始投入: $150.00")
    print(f"   總淨利潤: ${data['pnl']['net_profit']:.2f}")
    print(f"   最終機器人本金: ${data['capital']:.2f}")
    print(f"   總報酬率 (包含已提現): +{data['pnl']['roi_pct']:.2f}%")
    print("=" * 60)


if __name__ == "__main__":
    filepath = Path(__file__).parent.parent / "data" / "half_year_results.json"
    analyze_monthly(str(filepath))
