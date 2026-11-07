export const tradeQuery=(values:Record<string,any>)=>{const q=new URLSearchParams();Object.entries(values).forEach(([k,v])=>{if(v!==''&&v!=null&&v!==false)q.set(k,String(v))});return q.toString()};
export const tradeDefaults=()=>({start_date:'2025-01-01',end_date:new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())});
export const tradeLabels:Record<string,string>={person_id:'归属人员',broker:'券商渠道',symbol:'Ticker',category:'股票分类',side:'交易方向',kind:'交易类型',counterparty:'划转对象',micro_tag:'微操标记',post_net:'成交后净投入',start_date:'开始',end_date:'结束',search:'关键词',quantity_min:'数量≥',quantity_max:'数量≤',price_min:'价格≥',price_max:'价格≤',amount_min:'金额≥',amount_max:'金额≤',cost_min:'每股成本变化≥',cost_max:'每股成本变化≤',source_kind:'数据来源',source_file:'来源文件',link_status:'关联状态',review_only:'仅待核查',affects_only:'仅影响持仓'};

// Display only: retain exact API values for sorting, calculations and exports.
export const tradeMoneyKeys=new Set(['current_price','price','amount','fees','post_cost','pre_cost','post_net','realized_pnl']);
export const tradeMoneyLabels=new Set(['当前价','成交价','成交金额','手续费','成交前成本价','成交后成本价','成交后净投入','本笔已实现盈亏']);
export function tradeMoney(value:any,precision=2):string {
  if(value==null||value==='')return '—';
  const match=String(value).match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if(!match)return '—';
  const exponent=Number(match[4]||0),fraction=match[3]||'';
  if(!Number.isSafeInteger(exponent)||Math.abs(exponent)>100)return '—';
  let cents=BigInt(match[2]+fraction);
  const scale=fraction.length-exponent-precision;
  if(scale>0){const divisor=10n**BigInt(scale),rest=cents%divisor;cents/=divisor;if(rest*2n>=divisor)cents+=1n;}
  else cents*=10n**BigInt(-scale);
  const digits=cents.toString().padStart(precision+1,'0');
  return (match[1]==='-'&&cents!==0n?'-':'')+digits.slice(0,-precision).replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+digits.slice(-precision);
}
export function tradeCell(item:Record<string,any>,key:string):string {
  if(key==='trade_date')return tradeTimeDisplay(item.trade_date);
  if(key==='cost_change'&&['建仓','清仓'].includes(item.cost_change))return item.cost_change;
  if(key==='cost_change'&&item.cost_diff!=null){
    const raw=String(item.cost_diff);
    const sign=raw.startsWith('-') ? '-' : '+';
    const magnitude=raw.replace(/^[+-]/,'');
    let value=tradeMoney(magnitude);
    if(value==='—') return item.cost_change || '—';
    if(Number(magnitude)===0) return '不变';
    if(value==='0.00') value=tradeMoney(magnitude,4);
    value=value==='0.0000' ? magnitude : value.replace(/0+$/,'').replace(/\.$/,'');
    return `${sign}${value}/股`;
  }
  return tradeMoneyKeys.has(key)?tradeMoney(item[key]):item[key]==null||item[key]===''?'—':String(item[key]);
}

export const tradeBroker=(code:string)=>({LONGBRIDGE:'长桥',SCHWAB:'嘉信',FIRSTRADE:'第一证券'}[code]||code);
export const tradeCount=(value:any)=>value==null?'—':String(value).replace(/\B(?=(\d{3})+(?!\d))/g,',');
export const tradeTone=(value:any)=>value==null?'':String(value).startsWith('-')?'down':Number(value)>0?'up':'';

export const tradePresets = {
  '日常查看': ['trade_date','broker','person_name','symbol','category','side','current_price','price','quantity','amount','fees','cost_change'],
  '成本分析': ['trade_date','person_name','broker','symbol','side','quantity','post_quantity','post_cost','cost_change'],
  '完整字段': null,
} as const;
export type TradePreset = keyof typeof tradePresets;

export const tradeNumericKeys = new Set([...tradeMoneyKeys, 'quantity', 'post_quantity', 'cost_change']);
export const tradeSortKey = (key:string) => ({trade_date:'trade_time',cost_change:'cost_diff'}[key] || key);

export const tradeOptionLabel = (value:string) => ({
  BUY:'买入', SELL:'卖出', TRANSFER_IN:'转入', TRANSFER_OUT:'转出',
  MARKET_TRADE:'市场交易', OWNER_TRANSFER:'人员划转', EXTERNAL_TRANSFER:'外部划转', BOOK_SETTLEMENT:'账务结算',
  LINKED:'已关联', LEGACY_UNLINKED:'历史未关联', REVIEW_REQUIRED:'待核查', NOT_APPLICABLE:'不适用',
  EXCEL:'Excel 导入', CSV:'CSV 导入', API:'接口同步', MANUAL:'手工录入',
}[value] || value);

export function tradeTimeDisplay(value:string | null | undefined, explicitUtc?:string | null) {
  if (!value) return '—';
  if (!explicitUtc && /^\d{4}-\d{2}-\d{2}[ T]00:00(?::00(?:\.0+)?)?$/.test(value)) {
    return value.slice(0,10);
  }
  return value;
}

export const tradeCostStatus = (item:Record<string,any>) => {
  const change = tradeCell(item, 'cost_change');
  return change === '—' ? '暂无可信计算结果' : change;
};

export const tradeQuoteInfo = (item:Record<string,any>) =>
  [item.quote_status || '缺少有效行情', item.quote_time || '无报价时间'].join(' · ');

export const tradeSideTone = (code:string,kind:string) =>
  kind === 'MARKET_TRADE' && code === 'BUY' ? 'buy' : kind === 'MARKET_TRADE' && code === 'SELL' ? 'sell' : 'transfer';
