export const tradeQuery=(values:Record<string,any>)=>{const q=new URLSearchParams();Object.entries(values).forEach(([k,v])=>{if(v!==''&&v!=null&&v!==false)q.set(k,String(v))});return q.toString()};
export const tradeDefaults=()=>({start_date:'2025-01-01',end_date:new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date())});
export const tradeLabels:Record<string,string>={person_id:'人员',broker:'券商',symbol:'Ticker',category:'分类',side:'方向',kind:'交易类型',counterparty:'划转对象',micro_tag:'微操标记',post_net:'成交后净投入',start_date:'开始',end_date:'结束',search:'关键词',quantity_min:'数量≥',quantity_max:'数量≤',price_min:'价格≥',price_max:'价格≤',amount_min:'金额≥',amount_max:'金额≤',cost_min:'每股成本变化≥',cost_max:'每股成本变化≤',source_kind:'数据来源',source_file:'来源文件',link_status:'关联状态',review_only:'仅待核查',affects_only:'仅影响持仓'};

// Display only: retain exact API values for sorting, calculations and exports.
export const tradeMoneyKeys=new Set(['current_price','price','amount','fees','post_cost','pre_cost','post_net','realized_pnl']);
export const tradeMoneyLabels=new Set(['当前价','成交价','成交金额','手续费','成交前成本价','成交后成本价','成交后净投入','本笔已实现盈亏']);
export function tradeMoney(value:any):string {
  if(value==null||value==='')return '—';
  const match=String(value).match(/^([+-]?)(\d+)(?:\.(\d*))?(?:[eE]([+-]?\d+))?$/);
  if(!match)return '—';
  const exponent=Number(match[4]||0),fraction=match[3]||'';
  if(!Number.isSafeInteger(exponent)||Math.abs(exponent)>100)return '—';
  let cents=BigInt(match[2]+fraction);
  const scale=fraction.length-exponent-2;
  if(scale>0){const divisor=10n**BigInt(scale),rest=cents%divisor;cents/=divisor;if(rest*2n>=divisor)cents+=1n;}
  else cents*=10n**BigInt(-scale);
  const digits=cents.toString().padStart(3,'0');
  return (match[1]==='-'&&cents!==0n?'-':'')+digits.slice(0,-2).replace(/\B(?=(\d{3})+(?!\d))/g,',')+'.'+digits.slice(-2);
}
export function tradeCell(item:Record<string,any>,key:string):string {
  if(key==='cost_change'&&item.cost_change==='清仓')return '清仓';
  if(key==='cost_change'&&item.cost_diff!=null){const value=tradeMoney(item.cost_diff);return value==='0.00'?'不变':`${value.startsWith('-')?'降低':'提高'} ${value.replace(/^-/,'')}/股`;}
  return tradeMoneyKeys.has(key)?tradeMoney(item[key]):item[key]==null?'—':String(item[key]);
}

export const tradeBroker=(code:string)=>({LONGBRIDGE:'长桥',SCHWAB:'嘉信',FIRSTRADE:'第一证券'}[code]||code);
export const tradeCount=(value:any)=>value==null?'—':String(value).replace(/\B(?=(\d{3})+(?!\d))/g,',');
export const tradeTone=(value:any)=>value==null?'':String(value).startsWith('-')?'down':Number(value)>0?'up':'';
