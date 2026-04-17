import json

with open("data/backtest_results/small_capital_optimization.json") as f:
    d = json.load(f)

print(f"Safe: {len(d['safe'])}, Risky: {len(d['risky'])}")
for i, r in enumerate(d["risky"]):
    print(f"  #{i+1}: Lev={r['leverage']} risk={r['trend_risk_pct']} stop={r['bb_stop_loss_pct']} "
          f"trend={r['alloc_trend']} bb={r['alloc_bb']} => ret={r['total_return']:.4f} "
          f"dd={r['max_dd']:.4f} trades={r['trades']}")
