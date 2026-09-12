// Only nearby image elements and up to six pages of records are retained.
const waterfall={pageSize:24,cache:new Map(),heights:[],shapes:[],ranges:[],pageEnds:[],pending:new Map(),generation:0,abort:null,query:null,width:0,columns:0,total:0,maxId:0,raf:0,error:false,seenPhotos:new Set()};
// Observe only mounted, not-yet-visible tiles; recycling never retains old nodes.
const streamEntrance=new IntersectionObserver(entries=>{
 for(const entry of entries)if(entry.isIntersecting){entry.target.classList.add('stream-entered');streamEntrance.unobserve(entry.target);}
},{threshold:0,rootMargin:'0px 0px 40px 0px'});
function streamMountMotion(card){
 const id=Number(card.dataset.photo);const seen=waterfall.seenPhotos&&waterfall.seenPhotos.has(id);if(!seen){card.classList.add('stream-motion');if(waterfall.seenPhotos)waterfall.seenPhotos.add(id);}else{card.classList.add('stream-image-ready');}
 const img=card.querySelector('img');
 const ready=()=>{if(card.isConnected)card.classList.add('stream-image-ready');};
 const failed=()=>{if(card.isConnected){card.classList.add('stream-image-error');card.querySelector('.photo-frame').setAttribute('data-preview-message','预览暂不可用');}};
 img.addEventListener('load',ready,{once:true});img.addEventListener('error',failed,{once:true});
 if(img.complete){if(img.naturalWidth)ready();else failed();}
 if(!seen) streamEntrance.observe(card);
}
let streamScrollTime=performance.now(),streamScrollY=scrollY,streamScrollTimer;
function streamScroll(){
 const now=performance.now(),speed=Math.abs(scrollY-streamScrollY)/Math.max(16,now-streamScrollTime);
 streamScrollTime=now;streamScrollY=scrollY;
 const grid=$('#photo-grid');
 if(speed>1.5){grid.classList.add('stream-fast');clearTimeout(streamScrollTimer);streamScrollTimer=setTimeout(()=>grid.classList.remove('stream-fast'),180);}
 streamSchedule();
}
function streamMetrics(){const width=$('#photo-grid').clientWidth;const columns=width<480?2:width<760?3:width<1150?4:width<1600?5:6;return {width,columns,gap:width<480?12:18};}
function streamShape(a){const ratio=a&&a.width&&a.height?a.height/a.width:.75;return Math.max(.45,Math.min(1.5,ratio));}
function streamPlace(shapes,startEnds,metrics=streamMetrics()){
 const {width,columns,gap}=metrics,cardWidth=(width-gap*(columns-1))/columns;
 const ends=startEnds&&startEnds.length===columns?startEnds.slice():Array(columns).fill(0);
 const caption=parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--caption-h'))||42;
 const points=shapes.map(ratio=>{const column=ends.indexOf(Math.min(...ends));const picture=Math.round(cardWidth*ratio),height=picture+caption;
  const point={x:column*(cardWidth+gap),y:ends[column],width:cardWidth,picture,height};ends[column]+=height+gap;return point;
 });
 const top=points.length?Math.min(...points.map(p=>p.y)):Math.min(...ends,0);
 const bottom=points.length?Math.max(...points.map(p=>p.y+p.height)):Math.max(...ends,0);
 return {points,ends,range:{top,bottom},height:Math.max(0,Math.max(...ends,0)-gap)};
}
function streamLayout(items,index,metrics=streamMetrics()){
 const shapes=items.map(streamShape);
 waterfall.shapes[index]=shapes;
 const placed=streamPlace(shapes,waterfall.pageEnds[index],metrics);
 waterfall.pageEnds[index+1]=placed.ends.slice();
 waterfall.ranges[index]=placed.range;
 waterfall.heights[index]=Math.max(0,placed.range.bottom-placed.range.top);
 return {layout:placed.points.map((point,i)=>({...point,a:items[i]})),height:placed.height,range:placed.range,ends:placed.ends};
}
function streamReflow(metrics=streamMetrics()){
 let ends=Array(metrics.columns).fill(0);
 waterfall.pageEnds=[ends.slice()];
 for(let index=0;index<waterfall.shapes.length;index++){
  const shapes=waterfall.shapes[index];
  if(!shapes)continue;
  const placed=streamPlace(shapes,ends,metrics);
  waterfall.pageEnds[index]=ends.slice();
  waterfall.pageEnds[index+1]=placed.ends.slice();
  waterfall.ranges[index]=placed.range;
  waterfall.heights[index]=Math.max(0,placed.range.bottom-placed.range.top);
  ends=placed.ends;
  const cached=waterfall.cache.get(index);
  if(cached){
   const items=cached.layout.map(p=>p.a);
   waterfall.cache.set(index,{layout:placed.points.map((point,i)=>({...point,a:items[i]})),height:placed.height,range:placed.range,ends:placed.ends});
  }
 }
}
function streamCard(p,offset){const a=p.a;const date=(a.effective_date||'').replace('T',' ').replace(/:\d\d$/,'')||'时间未知';return `<article class="photo-card ${state.selected.has(a.id)?'selected':''}" data-photo="${a.id}" data-position="${offset}" tabindex="0" role="button" aria-label="查看 ${esc(basename(a.path))}" style="left:${p.x}px;top:${p.y}px;width:${p.width}px"><div class="photo-frame" style="height:${p.picture}px">${state.selecting?`<input class="photo-check" type="checkbox" aria-label="选择照片" ${state.selected.has(a.id)?'checked':''}>`:''}<img loading="lazy" decoding="async" src="/api/thumb/${a.id}?v=${state.thumbRevision}" alt="${esc(basename(a.path))}">${a.copies>1?`<span class="copy-badge">${a.copies} 个位置</span>`:''}</div><div class="card-caption"><div class="card-meta"><b>${esc(a.effective_place||basename(a.path))}</b><span>${esc(date)}</span></div></div></article>`;}
function streamSchedule(){if(!waterfall.raf)waterfall.raf=requestAnimationFrame(()=>{waterfall.raf=0;streamPaint();});}
function streamSelection(){
 for(const card of $$('#photo-grid [data-photo]')){
  const selected=state.selected.has(Number(card.dataset.photo));card.classList.toggle('selected',selected);
  let check=card.querySelector('.photo-check');
  if(state.selecting&&!check){check=document.createElement('input');check.type='checkbox';check.className='photo-check';check.setAttribute('aria-label','选择照片');card.querySelector('.photo-frame').prepend(check);}
  if(check){if(state.selecting)check.checked=selected;else check.remove();}
 }
 updateBatch();
}
function streamVisibleIds(){return $$('#photo-grid [data-photo]').filter(c=>{const b=c.getBoundingClientRect();return b.bottom>0&&b.top<innerHeight;}).map(c=>Number(c.dataset.photo));}
function restoreScrollInstant(top){scrollTo({top:Math.max(0,Number(top)||0),behavior:'instant'});}
function waitWaterfallFrames(count=2){return new Promise(resolve=>{const next=()=>count--<=0?resolve():requestAnimationFrame(next);next();});}
function waterfallPageTop(index){return waterfall.ranges[index]?.top??index*(waterfall.heights[0]||1800);}
async function restoreViewerPhotoPosition(exit){
 const fallback=()=>{if(exit&&Number.isFinite(exit.fallbackScroll))restoreScrollInstant(exit.fallbackScroll);streamSchedule();};
 try{
  if(!exit||!Number.isInteger(exit.photoId)||exit.photoId<1||!Number.isInteger(exit.absolute)||exit.absolute<0){fallback();return;}
  const changed=exit.photoId!==exit.openedPhotoId||exit.absolute!==exit.openedAbsolute;
  if(!changed){fallback();return;}
  const currentContext=typeof currentBrowseContext==='function'?currentBrowseContext():null;
  const wallContext=waterfall.query?{...waterfall.query,max_id:waterfall.maxId}:null;
  if(!sameBrowseContext(exit.context,exit.openedContext)||!sameBrowseContext(exit.context,currentContext)||!sameBrowseContext(exit.context,wallContext)||exit.waterfallGeneration!==waterfall.generation){fallback();return;}
  const pageIndex=Math.floor(exit.absolute/waterfall.pageSize),indexInPage=exit.absolute%waterfall.pageSize;
  const page=await streamPage(pageIndex);
  if(!page||!page.layout[indexInPage]||page.layout[indexInPage].a.id!==exit.photoId){fallback();return;}
  const grid=$('#photo-grid');if(!grid){fallback();return;}
  const gridDocumentTop=scrollY+grid.getBoundingClientRect().top;
  const roughTop=gridDocumentTop+page.layout[indexInPage].y;
  restoreScrollInstant(roughTop-innerHeight*.45);
  streamPaint();
  await waitWaterfallFrames(2);
  streamPaint();
  await waitWaterfallFrames(2);
  const card=grid.querySelector(`[data-photo="${exit.photoId}"]`);
  if(!card){fallback();return;}
  let rect=card.getBoundingClientRect();
  if(rect.bottom<=0||rect.top>=innerHeight){fallback();return;}
  const targetCenter=innerHeight*.45,center=rect.top+rect.height/2;
  if(Math.abs(center-targetCenter)>innerHeight*.2){restoreScrollInstant(scrollY+center-targetCenter);await waitWaterfallFrames(1);rect=card.getBoundingClientRect();}
  if(rect.bottom<=0||rect.top>=innerHeight)fallback();else streamSchedule();
 }catch(e){fallback();}
}
async function streamPage(index){
 if(waterfall.cache.has(index))return waterfall.cache.get(index);
 if(waterfall.pending.has(index))return waterfall.pending.get(index);
 const generation=waterfall.generation;
 const promise=(async()=>{
  try{
   const params=new URLSearchParams({...waterfall.query,offset:index*waterfall.pageSize,limit:waterfall.pageSize,max_id:waterfall.maxId});
   const data=await api('/api/photos?'+params,{signal:waterfall.abort.signal});
   if(generation!==waterfall.generation)return null;
   waterfall.total=data.total;waterfall.maxId=data.max_id;state.maxId=data.max_id;state.total=data.total;
   if(index>0&&!waterfall.pageEnds[index])streamReflow();
   const page=streamLayout(data.items,index);waterfall.cache.set(index,page);
   waterfall.error=false;$('#stream-retry').hidden=true;
   $('#result-count').textContent=fmt(data.total)+' 张';
   const pageTitle=$('#page-title');
   if(pageTitle && String(state.view||'').startsWith('group:')){
     const title=typeof groupTitle==='function'?groupTitle(state.view):['合影','合影'];
     pageTitle.textContent=title[1];
   }
   if(typeof syncGroupResultCount==='function') syncGroupResultCount(state.view,{total:data.total});
   return page;
  }catch(e){if(e.name!=='AbortError'&&generation===waterfall.generation){waterfall.error=true;$('#stream-status').textContent='加载暂时失败，已显示的照片仍可查看';$('#stream-retry').hidden=false;toast(e.message,true);}return null;}
  finally{if(generation===waterfall.generation){waterfall.pending.delete(index);streamSchedule();}}
 })();
 waterfall.pending.set(index,promise);return promise;
}
function streamPaint(){
 if(!waterfall.query||['people','passersby','scan','years','places','groups'].includes(state.view)||$('#detail-dialog').open)return;
 const grid=$('#photo-grid'),rect=grid.getBoundingClientRect(),start=Math.max(0,-rect.top-500),end=-rect.top+innerHeight+500;
 const metrics=streamMetrics(),{width,columns}=metrics;
 if(width<1)return;
 if(width!==waterfall.width||columns!==waterfall.columns){
  streamReflow(metrics);
  waterfall.width=width;waterfall.columns=columns;
 }
 const visible=[];
 for(let i=0;i<waterfall.ranges.length;i++){const range=waterfall.ranges[i];if(range&&range.bottom>=start&&range.top<=end)visible.push({index:i});}
 const finalEnds=waterfall.pageEnds[waterfall.shapes.length]||[];
 const gridHeight=Math.max(0,Math.max(...finalEnds,0)-(metrics.gap||0));
 grid.style.height=gridHeight+'px';
 const center=Math.max(0,Math.min(waterfall.heights.length-1,visible[Math.floor(visible.length/2)]?.index||0));
 const keep=new Set(visible.map(p=>p.index));
 const wanted=new Map();state.items=[];
 for(const block of visible){
  const page=waterfall.cache.get(block.index);
  if(!page){if(!waterfall.error&&waterfall.pending.size<2)streamPage(block.index);continue;}
  for(let j=0;j<page.layout.length;j++){const p=page.layout[j];const top=p.y;
   if(top+p.height<start||top>end||wanted.size>=144)continue;
   const position=block.index*waterfall.pageSize+j;
   wanted.set(String(p.a.id),{...p,y:top,position});state.items.push(p.a);
  }
 }
 // Retain existing nodes when possible: scrolling does not restart image loads.
 for(const node of [...grid.children])if(!wanted.has(node.dataset.photo)){streamEntrance.unobserve(node);const img=node.querySelector('img');if(img)img.removeAttribute('src');node.remove();}
 for(const [id,p] of wanted){let node=grid.querySelector(`[data-photo="${id}"]`);
  if(!node){grid.insertAdjacentHTML('beforeend',streamCard(p,p.position));node=grid.lastElementChild;streamMountMotion(node);}
  else {node.style.left=p.x+'px';node.style.top=p.y+'px';node.style.width=p.width+'px';node.querySelector('.photo-frame').style.height=p.picture+'px';}
 }
 streamSelection();
 const children=[...grid.children],ordered=[...children].sort((a,b)=>Number(a.dataset.position)-Number(b.dataset.position));
 if(ordered.some((node,i)=>children[i]!==node))grid.append(...ordered);
 for(const [index] of [...waterfall.cache].sort((a,b)=>Math.abs(b[0]-center)-Math.abs(a[0]-center))){if(waterfall.cache.size<=6)break;if(!keep.has(index))waterfall.cache.delete(index);}
 const expanded=Math.min(waterfall.heights.length*waterfall.pageSize,waterfall.total);
 const loading=waterfall.pending.size>0&&!waterfall.total&&!waterfall.error;
 if(!waterfall.error)$('#stream-status').textContent=loading?'正在加载照片…':(waterfall.total?`已展开 ${fmt(expanded)} / ${fmt(waterfall.total)} 张${expanded>=waterfall.total?' · 已到底部':' · 向下滚动继续'}`:'');
 const stats=state.status&&state.status.stats;
 const ready=Boolean(stats);
 const knownEmpty=ready&&Number(stats.assets||0)===0;
 const showEmpty=knownEmpty&&state.view==='timeline'&&!state.q&&!state.person&&!state.directory&&!state.place&&!state.dateFrom&&!state.dateTo&&!loading&&!waterfall.total;
 $('#empty').hidden=!showEmpty;
 $('#no-results').hidden=true;
 if(!ready||loading)$('#stream-status').textContent='正在加载照片';
 $('#stream-footer').hidden=showEmpty?true:(waterfall.total===0&&!waterfall.error&&!loading);
 $('#stream-top').hidden=scrollY<1200;
 if(end>=gridHeight-300&&expanded<waterfall.total&&!waterfall.error&&waterfall.pending.size===0){
  streamPage(waterfall.shapes.length);
 }
}
async function loadPhotos(){
 const oldTop=$('#photo-grid').getBoundingClientRect().top;
 waterfall.abort?.abort();waterfall.abort=new AbortController();waterfall.generation++;
 const ticket=waterfall.generation;
 const previous={
  cache:waterfall.cache,
  heights:waterfall.heights.slice(),
  shapes:waterfall.shapes.slice(),
  ranges:waterfall.ranges.slice(),
  pageEnds:waterfall.pageEnds.map(ends=>ends&&ends.slice()),
  total:waterfall.total,
  maxId:waterfall.maxId,
  query:waterfall.query
 };
 waterfall.pending.clear();waterfall.cache=new Map();waterfall.heights=[];waterfall.shapes=[];waterfall.ranges=[];waterfall.pageEnds=[];waterfall.total=0;waterfall.maxId=0;waterfall.error=false;
 waterfall.query={q:state.q,filter:state.view,person:state.person,directory:state.directory,sort:state.sort,date_from:state.dateFrom||'',date_to:state.dateTo||'',place:state.place||''};
 waterfall.width=streamMetrics().width;waterfall.columns=streamMetrics().columns;state.offset=0;
 const grid=$('#photo-grid');const keepHeight=Math.max(grid.offsetHeight||0,window.innerHeight*0.45);waterfall.seenPhotos=new Set();streamEntrance.disconnect();grid.classList.add('is-updating');grid.style.minHeight=keepHeight+'px';$('#stream-status').textContent='正在加载照片…';$('#stream-retry').hidden=true;$('#no-results').hidden=true;$('#empty').hidden=true;$('#result-count').textContent='加载中';
 if(typeof syncGroupResultCount==='function') syncGroupResultCount(state.view,{clear:true});
 if(oldTop<0&&!$('#detail-dialog').open)scrollTo({top:scrollY+oldTop-24,behavior:'instant'});
 const first=await streamPage(0);
 if(ticket!==waterfall.generation)return;
 const g=$('#photo-grid');
 if(!g)return;
 if(first){
  g.replaceChildren();
  g.style.height='0px';
  streamPaint();
  g.style.minHeight='';
  g.classList.remove('is-updating');
 }else{
  waterfall.cache=previous.cache;
  waterfall.heights=previous.heights;
  waterfall.shapes=previous.shapes;
  waterfall.ranges=previous.ranges;
  waterfall.pageEnds=previous.pageEnds;
  waterfall.total=previous.total;
  waterfall.maxId=previous.maxId;
  waterfall.query=previous.query;
  g.classList.remove('is-updating');
  g.style.minHeight='';
 }
}
window.addEventListener('scroll',streamScroll,{passive:true});
new ResizeObserver(()=>streamSchedule()).observe($('#photo-grid'));
$('#stream-retry').addEventListener('click',()=>{waterfall.error=false;$('#stream-retry').hidden=true;streamSchedule();if(!waterfall.heights.length)streamPage(0);});
$('#stream-top').addEventListener('click',()=>scrollTo({top:0,behavior:'smooth'}));
$('#detail-dialog').addEventListener('close',()=>{void restoreViewerPhotoPosition(viewer.exit);});
action(async()=>{await refreshStatus();loadPeopleOptions();await setView(state.view||'timeline');})();
