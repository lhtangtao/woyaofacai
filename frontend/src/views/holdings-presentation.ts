export type HoldingColumn={key:string;label:string;kind?:'money'|'percent'|'quantity'|'count'|'pnl'|'rate'};
export const openHoldingColumns:HoldingColumn[]=[
 {key:'person_name',label:'归属人员'},{key:'channel',label:'券商渠道'},{key:'ticker',label:'Ticker'},
 {key:'quantity',label:'数量',kind:'quantity'},{key:'cost_price',label:'成本价',kind:'money'},{key:'cost_amount',label:'成本总额',kind:'money'},
 {key:'current_price',label:'当前最新价',kind:'money'},{key:'market_value',label:'市值',kind:'money'},{key:'pnl_rate',label:'收益率',kind:'rate'},
 {key:'total_pnl',label:'累计盈亏',kind:'pnl'},{key:'buy_count',label:'买入次数',kind:'count'},{key:'sell_count',label:'卖出次数',kind:'count'},{key:'weight',label:'持仓占比',kind:'percent'}];
export const closedHoldingColumns:HoldingColumn[]=[
 {key:'person_name',label:'归属人员'},{key:'channel',label:'券商渠道'},{key:'ticker',label:'Ticker'},{key:'first_buy_date',label:'首次买入日期'},{key:'last_close_date',label:'最终清仓时间'},
 {key:'closed_quantity',label:'历史总交易数量',kind:'quantity'},{key:'avg_buy_price',label:'买入均价',kind:'money'},{key:'avg_sell_price',label:'卖出均价',kind:'money'},
 {key:'close_price',label:'最后清仓价',kind:'money'},{key:'current_price',label:'当前价',kind:'money'},{key:'post_close_change',label:'清仓后涨跌幅',kind:'rate'},
 {key:'total_buy_amount',label:'买入总额',kind:'money'},{key:'total_sell_amount',label:'卖出总额',kind:'money'},{key:'realized_pnl',label:'已实现盈亏',kind:'pnl'},
 {key:'realized_pnl_rate',label:'已实现收益率',kind:'rate'},{key:'buy_count',label:'买入次数',kind:'count'},{key:'sell_count',label:'卖出次数',kind:'count'}];
export const holdingNumber=(value:any,digits=2)=>value==null||value===''||!Number.isFinite(Number(value))?'—':Number(value).toLocaleString('en-US',{minimumFractionDigits:digits,maximumFractionDigits:digits});
export const holdingQuantity=(value:any)=>value==null?'—':Number(value).toLocaleString('en-US',{maximumFractionDigits:8});
export const holdingTone=(value:any)=>value==null?'':Number(value)>0?'hp-profit':Number(value)<0?'hp-loss':'';
export function holdingCell(row:any,col:HoldingColumn):string{
 const value=row[col.key];if(value==null||value==='')return '—';
 if(col.kind==='quantity')return holdingQuantity(value);
 if(col.kind==='count')return holdingNumber(value,0);
 if(col.kind==='percent'||col.kind==='rate')return (col.kind==='rate'&&Number(value)>0?'+':'')+holdingNumber(Number(value)*100)+'%';
 if(col.kind==='money'||col.kind==='pnl')return (col.kind==='pnl'&&Number(value)>0?'+':'')+holdingNumber(value)+(col.key==='close_price'&&row.is_split_adjusted?'*':'');
 return String(value);
}
