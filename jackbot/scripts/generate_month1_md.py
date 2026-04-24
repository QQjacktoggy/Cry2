import json

def generate_md():
    with open('data/month1_trades.log', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    grids = []
    matches = []
    
    for line in lines:
        try:
            data = json.loads(line)
        except:
            continue
            
        evt = data.get('event')
        if evt == 'grid_created' and len(grids) < 5:
            grids.append(data)
        elif evt == 'grid_profit_matched':
            matches.append(data)

    with open(r'C:\Users\jack_shih\.gemini\antigravity\brain\51a006e2-a236-4577-bb27-fc7ecba1be7f\month1_trades.md', 'w', encoding='utf-8') as out:
        out.write("# 第一個月 (Month 1) 網格參數與前 20 筆交易明細\n\n")
        out.write("這是在 150 U 本金、0 手續費、50% 每日複利的條件下，第一個月的**網格設定參數**與**真實配對明細**。\n\n")
        
        out.write("## ⚙️ 網格建立參數 (Grid Parameters)\n")
        out.write("機器人根據 `MarketAssessor` 的市場行情（ATR 與 ADX）動態決定的開單參數：\n\n")
        out.write("| 時間 | 交易對 | 網格ID | 方向 | 區間 (下限~上限) | 格數 | 槓桿 | 單格數量 |\n")
        out.write("|---|---|---|---|---|---|---|---|\n")
        for g in grids:
            out.write(f"| {g.get('timestamp', '')} | {g.get('symbol')} | `{g.get('grid_id', '')[:14]}`... | **{g.get('direction', '').upper()}** | ${g.get('lower')} ~ ${g.get('upper')} | {g.get('levels')} 格 | {g.get('leverage')}x | {g.get('per_level_qty')} |\n")
            
        out.write("\n## 💸 前 20 筆配對獲利明細 (First 20 Trades)\n")
        out.write("這是開網格後，價格上下波動所觸發的真實「一買一賣」配對獲利：\n\n")
        out.write("| 達成時間 | 所屬網格 | 買入價 (Buy) | 賣出價 (Sell) | 單次配對獲利 (U) | 該網格累計獲利 (U) |\n")
        out.write("|---|---|---|---|---|---|\n")
        for m in matches[:20]:
            out.write(f"| {m.get('timestamp')} | `{m.get('grid_id', '')[:14]}`... | ${m.get('buy')} | ${m.get('sell')} | **${m.get('profit')}** | ${m.get('total_profit')} |\n")
            
        out.write("\n> 註：完整第一個月共完成了 **上百筆** 這樣的配對交易，這裡僅列出最一開始的 20 筆作為範例。\n")

if __name__ == '__main__':
    generate_md()
