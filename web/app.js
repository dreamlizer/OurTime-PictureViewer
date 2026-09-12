const $=s=>document.querySelector(s);
const $$=s=>[...document.querySelectorAll(s)];
const state={view:'timeline',q:'',person:'',place:'',dateFrom:'',dateTo:'',offset:0,total:0,items:[],people:[],passersby:[],selected:new Set(),peopleSelected:new Set(),peopleMerging:false,personLabels:{},selecting:false,detail:null,personId:null,status:null,folder:null,directory:'',sort:'date_desc',maxId:0,folderTarget:'scan',scanRoots:[],scanSubmitting:false,scanStartedThisVisit:false,scanShowCompletedResult:false,exclusion:null,thumbRevision:0,peopleStream:{people:{items:[],offset:0,total:0,more:true,loading:false,q:'',generation:0},passersby:{items:[],offset:0,total:0,more:true,loading:false,q:'',generation:0}}};
const titles={all:['全部照片','全部照片'],folders:['文件夹','只看 I 盘。'],people:['人物档案','人物档案'],passersby:['路人','先不识别，以后还能找回来。'],timeline:['全部照片','全部照片'],years:['按年份查看','点某一年，只看那一年。'],places:['按地点','记得那是在哪里。'],objects:['物体','照片里有什么。'],groups:['合影','合影'],uncertain:['待确认时间','给记忆一个时间。'],no_place:['待补充地点','记得那是在哪里吗？'],duplicates:['重复副本','一张照片，多个来处。'],screenshots:['截图与小图','日常的片段，也有位置。'],errors:['读取问题','把未完成的部分看清楚。'],missing:['原文件缺失','寻找照片现在的位置。'],scan:['添加照片','添加照片'],excluded:['已排除','留下值得保存的记忆。']};
const statuses={running:'正在扫描',pausing:'正在暂停',paused:'已暂停',completed:'已完成',completed_with_errors:'完成，有读取问题',failed:'扫描失败'};
function setText(sel,value){const el=$(sel); if(el) el.textContent=value;}
function setHtml(sel,value){const el=$(sel); if(el) el.innerHTML=value;}
function setHidden(sel,on){const el=$(sel); if(el) el.hidden=on;}
function setDisabled(sel,on){const el=$(sel); if(el) el.disabled=on;}
function setClass(sel,value){const el=$(sel); if(el) el.className=value;}
function setValue(sel,value){const el=$(sel); if(el) el.value=value;}
function esc(v){return String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function prettyPlace(value){return String(value||'').replace(/附近/g,'');}
function dateSource(v){return String(v||'').replace(/^EXIF /,'');}
function readableError(v){const t=String(v||'');if(/truncated|broken data stream/.test(t))return '图片数据不完整或已损坏';if(/cannot identify image/.test(t))return '无法识别图片格式，文件可能损坏';if(/PermissionError|Permission denied/.test(t))return '没有权限读取这个文件';if(/not found|No such file|不存在/.test(t))return '未找到原文件，请检查磁盘或重新扫描';if(/timeout|超时/i.test(t))return '读取超时，可以稍后重试';return '读取未完成，请查看原始诊断';}
function basename(p){return (p||'').split(/[\\/]/).pop();}
function fmt(n){return Number(n||0).toLocaleString('zh-CN');}
function toast(message,error=false){const el=$('#toast');if(!el)return;el.textContent=message;el.className='visible'+(error?' error':'');clearTimeout(toast.timer);toast.timer=setTimeout(()=>el.className='',error?9000:4500);}
function showNotice(message,error=false){const notice=$('#person-notice');if(notice&&$('#person-dialog')&&$('#person-dialog').open){notice.hidden=false;notice.textContent=message;notice.className='person-notice'+(error?' error':'');}toast(message,error);}
function hideNotice(){const notice=$('#person-notice');if(notice){notice.hidden=true;notice.textContent='';notice.className='person-notice';}}
function apiError(value,text,status){const detail=value&&value.detail;if(typeof detail==='string')return detail;if(Array.isArray(detail)){return detail.map(x=>{if(typeof x==='string')return x;const loc=Array.isArray(x&&x.loc)?x.loc:[];if(x&&x.msg==='Field required'&&(loc.includes('target_id')||loc.includes('body')))return '请先选择要合并到的人';return (x&&(x.msg||x.message))||JSON.stringify(x);}).filter(Boolean).join('；');}if(detail)return JSON.stringify(detail);return (text||'').slice(0,180)||('请求失败 '+status);}
async function api(url,options={}){const headers={'Accept':'application/json',...(options.headers||{})};if(options.body!=null&&!headers['Content-Type'])headers['Content-Type']='application/json';const res=await fetch(url,{...options,headers});const text=await res.text();let value={};if(text){try{value=JSON.parse(text);}catch(err){throw new Error(res.ok?'服务器返回了无法解析的内容':((text||'').slice(0,180)||('请求失败 '+res.status)));}}if(!res.ok)throw new Error(apiError(value,text,res.status));return value;}
function action(fn){return async(...args)=>{try{await fn(...args);}catch(e){showNotice(e.message,true);}};}
function showDialog(id){const el=$(id);if(!el.open)el.showModal();}
function isTimelineView(view=state.view){return view==='timeline'||String(view).startsWith('year:');}
function isGroupView(view=state.view){return view==='groups'||String(view).startsWith('group:');}
function groupTitle(view=state.view){if(String(view).startsWith('group:')){const key=view.slice(6);if(key==='10plus')return ['合影','10人及以上'];const n=Number(key);return ['合影', Number.isFinite(n)?n+'人合影':'合影'];}return ['合影','合影'];}
function timelineTitle(view=state.view){if(String(view).startsWith('year:')){const year=view.slice(5);return ['全部照片',year==='unknown'?'时间未记录':year+' 年'];}return ['全部照片','最新的照片在前面。'];}
function folderDrive(path){const raw=String(path||''); const m=raw.match(/^[A-Za-z]:/); return m?m[0].toUpperCase():'';}
const FOLDER_ONLY_DRIVE='I:';
function resetPhotoStream(){waterfall.abort?.abort();waterfall.abort=new AbortController();waterfall.generation++;waterfall.pending.clear();waterfall.cache.clear();waterfall.heights=[];waterfall.total=0;waterfall.maxId=0;waterfall.error=false;waterfall.query=null;streamEntrance.disconnect();$('#photo-grid').replaceChildren();$('#photo-grid').style.height='0px';$('#stream-status').textContent='正在加载照片…';$('#stream-retry').hidden=true;$('#no-results').hidden=true;$('#empty').hidden=true;$('#result-count').textContent='加载中';}
function currentPlaceName(){return String(state.view||'').startsWith('place:')?state.view.slice(6):'';}
function updatePlaceRefine(){
  const box=$('#place-refine'); if(!box)return;
  const place=currentPlaceName();
  const on=Boolean(place) && place!=='unknown';
  box.hidden=!on;
  if(!on)return;
  $('#place-refine-title').textContent='把“'+place+'”写得更具体';
  $('#place-refine-help').textContent='附近有更细的地名时可以直接选；没有就手填保存。只改这些照片的人工地点，不改原图 GPS。';
  $('#place-refine-input').value=place.endsWith('附近')?place.replace(/附近$/,''):place;
  loadPlaceSuggestions($('#place-refine-input').value.trim());
}
async function loadPlaceSuggestions(q){
  const box=$('#place-refine-suggest'); if(!box)return;
  if(!q){box.innerHTML='';return;}
  const data=await api('/api/places/suggest?q='+encodeURIComponent(q));
  box.innerHTML=(data.items||[]).map(item=>'<button type="button" class="place-suggest '+(item.kind==='catalog'?'matched':'custom')+'" data-place-suggest="'+esc(item.name)+'">'+esc(item.name)+(item.kind==='catalog'?'<small>附近可匹配</small>':'<small>地图里没有，将手填保存</small>')+'</button>').join('');
}
async function savePlaceRefine(){
  const source=currentPlaceName();
  const target=($('#place-refine-input')&&$('#place-refine-input').value||'').trim();
  if(!source||source==='unknown')throw new Error('请先进入一个具体地点');
  if(!target)throw new Error('请填写更具体的地点');
  const data=await api('/api/places/rename',{method:'POST',body:JSON.stringify({from_place:source,to_place:target,remember:true})});
  toast(data.matched?('已保存为 '+data.name+'，并记住这个更细的地点'):('已保存为 '+data.name+'。附近地图没有这一条，先按手填记下'));
  await setView('place:'+data.name);
}
function updateTimelineTools(){const on=isTimelineView();$('#timeline-tools').hidden=!on;if(on){$$('#timeline-tools [data-time-sort]').forEach(b=>b.classList.toggle('active',b.dataset.timeSort===state.sort));$('#timeline-by-year').classList.toggle('active',state.view==='years');}document.body.classList.toggle('is-add-photos-view',state.view==='scan');window.__ourTimeHomeSync?.();}
function organizeViews(){return new Set(['passersby','uncertain','no_place','duplicates','screenshots','excluded']);}
function statsViews(){return new Set(['timeline','all']);}
function updateChrome(){
  const view=state.view;
  const organize=organizeViews().has(view);
  const showStats=view==='timeline';
  const stats=$('#stats'); if(stats) stats.hidden=!showStats;
  document.body.classList.toggle('is-subpage',!showStats);
  document.body.classList.toggle('is-people-view',view==='people');
  document.body.classList.toggle('is-group-query',String(view).startsWith('group:'));
  const organizeNav=$('#organize-nav');
  if(organizeNav) organizeNav.open=organize;
  $$('.nav[data-view]').forEach(el=>{
    el.classList.toggle('active',el.dataset.view===view||(el.dataset.view==='timeline'&&isTimelineView(view))||(el.dataset.view==='places'&&String(view).startsWith('place:'))||(el.dataset.view==='groups'&&isGroupView(view)));
  });
}
async function loadFolderBrowser(path){
  state.folderPath=path||'';
  const [data,ex]=await Promise.all([api('/api/folders?path='+encodeURIComponent(state.folderPath||'')), api('/api/exclusions')]);
  const excluded=new Set((ex.roots||[]).map(r=>(r.path||'').replace(/[\/]+$/,'').toLowerCase()));
  const current=(data.path||'').replace(/[\/]+$/,'');
  const parts=current?current.split(/[\/]+/).filter(Boolean):[];
  const depth=current?parts.length:0;
  const up=$('#folders-up'); if(up) up.hidden=!data.path;
  const trail=$('#folders-trail');
  if(trail){
    trail.hidden=false;
    const crumbs=[];
    crumbs.push('<button type="button" class="trail-item'+(depth?'' :' current')+'" data-folder-open="'+esc(FOLDER_ONLY_DRIVE+'\\')+'">I 盘</button>');
    if(parts.length){
      const slash=String.fromCharCode(92); const drive=parts[0].endsWith(":")?parts[0]+slash:parts[0];
      crumbs.push('<button type="button" class="trail-item'+(parts.length===1?' current':'')+'" data-folder-open="'+esc(drive)+'">'+esc(parts[0])+'</button>');
      let acc=drive.endsWith(slash)?drive.slice(0,-1):drive;
      for(let i=1;i<parts.length;i++){
        acc+=slash+parts[i];
        crumbs.push('<button type="button" class="trail-item'+(i===parts.length-1?' current':'')+'" data-folder-open="'+esc(acc)+'">'+esc(parts[i])+'</button>');
      }
    }
    trail.innerHTML=crumbs.join('<span class="trail-sep">/</span>');
  }
  const total=$('#folders-total');
  const list=$('#folders-list'); if(!list)return;
  const slash=String.fromCharCode(92);
  const items=(data.items||[]).filter(item=>depth>0 || folderDrive(item.path)===FOLDER_ONLY_DRIVE);
  if(total) total.textContent=items.length+' 项';
  if(!state.folderPath && items.length===1){
    await loadFolderBrowser(items[0].path);
    return;
  }
  list.dataset.depth=String(Math.min(depth,3));
  list.innerHTML=items.map(item=>{
    const key=(item.path||'').replace(/[\/]+$/,'').toLowerCase();
    const on=!excluded.has(key) && ![...excluded].some(r=>key===r || key.startsWith(r+slash) || key.startsWith(r+'/'));
    const kind=depth===0?'drive':(depth===1?'folder':(depth===2?'sub':'deep'));
    const label=depth===0?'I 盘':item.name;
    const add=depth===0?'<button type="button" class="text-button folder-add" data-folder-add="'+esc(item.path)+'">添加文件夹</button>':'';
    return '<article class="folder-card '+kind+'" data-folder-open="'+esc(item.path)+'"><input class="folder-tick" type="checkbox" data-folder-toggle="'+esc(item.path)+'" '+(on?'checked':'')+' aria-label="纳入浏览"><span class="folder-name">'+esc(label)+'</span>'+add+'</article>';
  }).join('')||'<div class="no-results">I 盘里没有可看的文件夹。</div>';
}
async function toggleFolderInclusion(path, include){
  if(include){
    const data=await api('/api/exclusions');
    const hit=(data.roots||[]).find(r=>(r.path||'').replace(/[\/]+$/,'').toLowerCase()===(path||'').replace(/[\/]+$/,'').toLowerCase());
    if(hit) await api('/api/exclusions/roots/'+hit.id,{method:'DELETE'});
    toast('已重新纳入浏览');
  }else{
    await api('/api/exclusions/roots',{method:'POST',body:JSON.stringify({path})});
    toast('已从工作台屏蔽，原文件还在');
  }
  await loadFolderBrowser(state.folderPath||'');
  refreshStatus().catch(()=>{});
}
function renderScanRoots(){const list=$('#scan-root-list');if(!list)return;const roots=state.scanRoots||[];list.hidden=!roots.length;list.innerHTML=roots.map((path,index)=>`<div class="scan-root"><span>${esc(path)}</span><button type="button" class="text-button" data-remove-scan-root="${index}" aria-label="移除 ${esc(path)}">×</button></div>`).join('');setHidden('#scan-add-folder',!roots.length);const start=$('#start-scan');if(start){start.disabled=!roots.length||state.scanSubmitting||Boolean(state.status?.job&&['running','pausing'].includes(state.status.job.status));}}
function addScanRoot(path){const value=String(path||'').trim().replace(/[\\/]+$/,'');if(!value)return;if(!(state.scanRoots||[]).some(item=>item.toLowerCase()===value.toLowerCase()))state.scanRoots.push(value);renderScanRoots();}
function removeScanRoot(index){state.scanRoots.splice(Number(index),1);renderScanRoots();}
function renderScanStatus(data){const j=data&&data.job;const inProgress=Boolean(j&&['running','pausing','paused'].includes(j.status));const terminal=Boolean(j&&['completed','completed_with_errors','failed'].includes(j.status));const showCompleted=Boolean(terminal&&state.scanStartedThisVisit&&state.scanShowCompletedResult);const showProgress=inProgress||showCompleted;const card=$('.add-photos-card');const progress=$('#scan-progress');if(!card||!progress)return;card.hidden=inProgress;progress.hidden=!showProgress;setText('#scan-progress-title',j?.status==='paused'?'已暂停':showCompleted?'扫描完成':j?.status==='running'||j?.status==='pausing'?'正在扫描照片':'扫描状态');setText('#scan-checked',fmt(j?.processed));setText('#scan-added',fmt(j?.metadata_reads));setText('#scan-errors-count',fmt(j?.errors));setText('#scan-current',j?.status==='paused'?'扫描已暂停':inProgress?(j.current_path||'正在读取照片…'):(j?.message||''));setHidden('#scan-view-all',!j||!['completed','completed_with_errors'].includes(j.status));setHidden('#scan-empty-hint',Boolean(state.scanRoots.length));renderScanRoots();if(j&&state.view==='scan'){setHtml('#job-actions',j.status==='paused'?'<button class="secondary" id="resume-scan">继续扫描</button>':inProgress?'<button class="secondary" id="pause-scan">暂停扫描</button>':'<button class="secondary" id="resume-scan">按此范围重新扫描</button>');}}
function openAddPhotos(){state.scanRoots=[];state.scanSubmitting=false;renderScanRoots();return setView('scan');}
function currentPageTitle(view, fallback){
  if(view==='people')return '人物档案';
  if(String(view).startsWith('group:'))return groupTitle(view)[1];
  return fallback;
}
async function setView(view){if(view==='all')view='timeline';const previousView=state.view;if(previousView==='scan'&&view!=='scan'){state.scanStartedThisVisit=false;state.scanShowCompletedResult=false;}if(view==='scan'&&previousView!=='scan'){state.scanStartedThisVisit=false;state.scanShowCompletedResult=false;}const leavingHome=previousView==='timeline'||String(previousView).startsWith('group:');const enteringHome=view==='timeline'||String(view).startsWith('group:');if(leavingHome&&!enteringHome){state.q='';state.person='';state.directory='';state.place='';state.dateFrom='';state.dateTo='';}if(['people','passersby','scan','places','years','groups','objects'].includes(view)||String(view).startsWith('year:')||String(view).startsWith('place:')||String(view).startsWith('group:'))resetPhotoStream();state.view=view;$('#exclusion-panel').hidden=view!=='excluded';if(view==='excluded')await loadExclusionRules();state.offset=0;state.selected.clear();state.selecting=false;if(view!=='people')setPeopleMerging(false);updateBatch();$$('.nav[data-view]').forEach(el=>el.classList.toggle('active',el.dataset.view===view||(el.dataset.view==='timeline'&&isTimelineView(view))||(el.dataset.view==='places'&&String(view).startsWith('place:'))||(el.dataset.view==='groups'&&isGroupView(view))));const title=titles[view]||(isTimelineView(view)?timelineTitle(view):isGroupView(view)?groupTitle(view):String(view).startsWith('place:')?['按地点',view.slice(6)]:[view,view]);const isGroupDetail=String(view).startsWith('group:');$('#breadcrumb').textContent=title[0];$('#page-title').textContent=currentPageTitle(view,title[1]);const groupsBack=$('#groups-back');if(groupsBack)groupsBack.hidden=!isGroupDetail;const hideLibrary=['people','passersby','scan','years','places','groups','folders'].includes(view) && !isGroupDetail;$('#library-view').hidden=hideLibrary;$('#people-view').hidden=view!=='people';$('#passersby-view').hidden=view!=='passersby';$('#timeline-view').hidden=view!=='years';$('#places-view').hidden=view!=='places';$('#groups-view').hidden=view!=='groups';$('#objects-view').hidden=view!=='objects';$('#scan-view').hidden=view!=='scan';const foldersView=$('#folders-view');if(foldersView)foldersView.hidden=view!=='folders';$('#collection-title').textContent=title[0];updateChrome();updateTimelineTools();updatePlaceRefine();if(view==='people'){state.peopleSelected.clear();setPeopleMerging(false);await loadPeople();}else if(view==='passersby')await loadPassersby();else if(view==='years')await loadTimeline();else if(view==='places')await loadPlaces();else if(view==='groups')await loadGroups();else if(view==='objects'){await setView('timeline');return;}else if(view==='folders')await loadFolderBrowser(state.folderPath||'');else if(view==='scan'){renderScanRoots();await refreshStatus();}else {if((isTimelineView(view)||String(view).startsWith('group:'))&&!['date_asc','date_desc'].includes(state.sort)){state.sort='date_desc';$('#sort-order').value=state.sort;}loadPhotos();refreshStatus();}}

async function refreshStatus(){const previous=state.status;const data=await api('/api/status');const previousActive=Boolean(previous?.job&&['running','pausing','paused'].includes(previous.job.status));const terminal=Boolean(data.job&&['completed','completed_with_errors','failed'].includes(data.job.status));if(state.view==='scan'&&state.scanStartedThisVisit&&previousActive&&terminal)state.scanShowCompletedResult=true;state.status=data;const s=data.stats;setText('#s-assets',fmt(s.assets));setText('#nav-count',fmt(s.assets));setText('#excluded-count',fmt(s.excluded_assets));if(typeof s.group_photos==='number'&&Number.isFinite(s.group_photos))setText('#groups-count',fmt(s.group_photos));state.connectionNotice=false;setText('#s-people',fmt(s.people));setText('#people-count',fmt(s.people));setText('#s-uncertain',fmt(s.uncertain_dates));setText('#s-duplicates',fmt(s.duplicates));const j=data.job;renderScanStatus(data);const active=j&&['running','pausing'].includes(j.status);const running=$('#running-dot'); if(running) running.className=active?'active':'';setDisabled('#start-scan',Boolean(active)||!state.scanRoots.length||state.scanSubmitting);setHidden('#job-banner',!active);setHtml('#job-banner',active?`<span>● ${statuses[j.status]} · 本轮已检查 ${fmt(j.processed)} 个文件 · ${j.workers} 路 · 新资料读取 ${data.metadata_per_second} 张/秒</span><button class="text-button" id="banner-details">查看进度 →</button>`:'');setText('#job-status',j?statuses[j.status]:'');if(j){setHtml('#job-details',`<div>${esc(JSON.parse(j.roots).join('；'))}</div><div class="job-metrics"><span><b>${fmt(j.discovered)}</b>本轮发现文件</span><span><b>${fmt(j.processed)}</b>本轮已检查</span><span><b>${fmt(j.metadata_reads)}</b>新读取元数据</span><span><b>${fmt(j.skipped)}</b>已完成直接跳过</span><span><b>${fmt(j.errors)}</b>读取问题</span></div><p>${esc(j.message||'正在扫描；已完成文件会快速跳过。')} · ${j.workers} 路处理 · 近一分钟新资料读取 ${data.metadata_per_second} 张/秒 · 略过 ${fmt(j.auxiliary)} 个辅助文件</p><div class="path-line">${esc(j.current_path)}</div>`);setHtml('#job-actions',active?'<button class="secondary" id="pause-scan">暂停扫描</button>':`<button class="secondary" id="resume-scan">${j.status==='paused'?'继续扫描':'按此范围重新扫描'}</button>`);}setHidden('#error-details',!data.errors.length);setText('#error-summary',`读取问题：共 ${fmt(j?.errors)} 项，显示最近 ${data.errors.length} 项`);setHtml('#scan-errors',data.errors.map(e=>`<div class="error-row"><b>${esc(e.stage)}</b><div>${esc(e.path)}</div><div>${esc(readableError(e.message))}</div><details><summary>原始诊断（技术信息）</summary><pre>${esc(e.message)}</pre></details></div>`).join(''));const cap=data.capabilities;setHtml('#capabilities',`人脸模型：${cap.face_model?'已找到 · '+(cap.face_runtime||'处理器')+' 运行':'未找到'}<br>HEIC / HEIF：${cap.heif?'可读取':'尚未安装解码组件，遇到时会记录问题'}<br>扩展元数据：${cap.exiftool?'已就绪 · 常驻进程':'仅基础读取器'}<br>离线地名：${cap.geo?'中文及海外地名数据已就绪':'未找到，保留 GPS 原始坐标'}<br>资料库：${esc(cap.data_dir)}`);if(previousActive&&!active&&terminal){toast(j.message||'扫描结束');if(state.view==='people')await loadPeople();else if(state.view==='passersby')await loadPassersby();else if(!['scan'].includes(state.view))await loadPhotos();}if(j&&!state.scanPrefilled){$('#scan-roots').value=JSON.parse(j.roots).join('\n');$('#scan-workers').value=String(j.workers);state.scanPrefilled=true;}return data;}
function personLabel(p){if(p.ignored)return p.name||'路人';return p.alias?(p.name||('待命名 '+p.id))+' / '+p.alias:(p.name||('待命名 '+p.id));}
function personStatus(p){if(p.ignored)return '路人 · 暂不识别';if(p.confirmed)return p.alias?'已命名 · '+p.alias:'已人工确认';return p.suggested_name?'可能是 '+p.suggested_name:'待核对分组';}
function updatePeopleMerge(){const n=state.peopleSelected.size;const merging=Boolean(state.peopleMerging);const bar=$('#people-merge-bar');if(!bar)return;bar.hidden=!merging;const count=$('#people-selected-count');if(count)count.textContent='已选 '+n+' 组';const merge=$('#merge-selected-people');if(merge)merge.disabled=n<2;const grid=$('#people-grid');if(grid)grid.classList.toggle('selecting',merging);const toggle=$('#people-merge-toggle');if(toggle){toggle.textContent=merging?'取消合并':'合并人物';toggle.setAttribute('aria-pressed',merging?'true':'false');}document.body.classList.toggle('is-people-merging',merging&&state.view==='people');}
function togglePersonSelection(id, selected){id=Number(id);if(!id)return;const on=selected==null?!state.peopleSelected.has(id):Boolean(selected);if(on)state.peopleSelected.add(id);else state.peopleSelected.delete(id);const card=$(`#people-grid [data-person="${id}"]`);if(card)card.classList.toggle('selected',on);const box=card&&card.querySelector('[data-select-person]');if(box)box.checked=on;updatePeopleMerge();}
function isPeopleSelecting(){return state.view==='people'&&Boolean(state.peopleMerging);}
function rememberPersonLabel(person){if(!person||person.id==null)return;const id=String(person.id);const label=personLabel(person);state.personLabels=state.personLabels||{};state.personLabels[id]=label;window.__ourTimeRememberPersonLabel?.(id,label);}
function setPeopleMerging(on){
  const entering=Boolean(on)&&!state.peopleMerging;
  const leaving=!Boolean(on)&&state.peopleMerging;
  state.peopleMerging=Boolean(on);
  if(!state.peopleMerging)state.peopleSelected.clear();
  if(leaving||entering){
    const stream=state.peopleStream&&state.peopleStream.people;
    const grid=$('#people-grid');
    if(grid&&stream&&stream.items)grid.innerHTML=stream.items.map(p=>personCard(p,'people')).join('');
  }else if(!state.peopleMerging){
    $$('#people-grid .person-card').forEach(card=>card.classList.remove('selected'));
    $$('#people-grid [data-select-person]').forEach(box=>box.checked=false);
  }
  updatePeopleMerge();
}
function peopleQuery(extra={}){const q=new URLSearchParams({ignored:String(extra.ignored||0),offset:String(extra.offset||0),limit:String(extra.limit||48)});if(extra.q)q.set('q',extra.q);if(extra.ids)q.set('ids',extra.ids);if(extra.named)q.set('named','1');return '/api/people?'+q.toString();}
function patchPersonInStream(id, patch){id=Number(id);const stream=state.peopleStream&&state.peopleStream.people;if(!stream||!stream.items)return null;const item=stream.items.find(p=>p.id===id);if(!item)return null;Object.assign(item,patch);return item;}
 function personCardById(id){const numeric=Number(id);if(!Number.isInteger(numeric)||numeric<1)return null;return document.querySelector(`#people-grid [data-person="${numeric}"]`);}
 function renderPersonCard(person){const card=personCardById(person.id);if(!card)return;const wrap=document.createElement('div');wrap.innerHTML=personCard(person,'people');const fresh=wrap.firstElementChild;if(!fresh)return;card.replaceWith(fresh);}
 function bumpPersonCard(id){const card=personCardById(id);if(!card)return;card.classList.add('named');if(state.view==='people')card.scrollIntoView({block:'nearest',behavior:'smooth'});}
 function removePersonFromLocalState(id){
  id=Number(id);const stream=state.peopleStream&&state.peopleStream.people;const card=personCardById(id);
  if(card){card.classList.add('removing');card.remove();}
  if(!stream||!stream.items)return Boolean(card);
  const before=stream.items.length;stream.items=stream.items.filter(p=>Number(p.id)!==id);
  if(stream.items.length!==before&&typeof stream.total==='number'&&stream.total>0)stream.total-=1;
  const total=$('#people-total');if(total)total.textContent=fmt(stream.total)+' 个分组';
  return Boolean(card)||stream.items.length!==before;
 }
 function normalizePersonText(value){return String(value??'').trim().replace(/ {2,}/g,' ');}
 function personNameMatchesUrl(name,alias,id){return '/api/people/matches?'+new URLSearchParams({name,alias,exclude_id:String(Number(id)||0)});}
 function ensurePersonNameConflictDialog(){
  let dialog=$('#person-name-conflict-dialog');if(dialog)return dialog;
  dialog=document.createElement('dialog');dialog.id='person-name-conflict-dialog';dialog.className='compact-dialog';dialog.setAttribute('aria-label','确认人物重名');
  dialog.innerHTML='<div class="eyebrow">人物重名提醒</div><h2>可能是同一个人</h2><div class="person-name-conflict-card"><img id="person-conflict-cover" alt="已有的人物头像"><div><strong id="person-conflict-name"></strong><p id="person-conflict-count" class="muted"></p></div></div><p id="person-conflict-copy" class="muted"></p><div class="quick-name-actions"><button type="button" class="text-button" id="person-conflict-cancel">取消</button><button type="button" class="secondary" id="person-conflict-separate">仍保存为不同人物</button><button type="button" class="primary" id="person-conflict-merge">合并到这个人物</button></div>';
  document.body.appendChild(dialog);return dialog;
 }
 function confirmPersonNameConflict(match){
  const dialog=ensurePersonNameConflictDialog();const cover=$('#person-conflict-cover');const label=personLabel(match);const count=fmt(match.photo_count||0);
  $('#person-conflict-name').textContent=label;$('#person-conflict-count').textContent=count+' 张照片';$('#person-conflict-copy').textContent=`已有“${label}”，${count} 张照片。这可能是同一个人。`;
  if(cover){cover.hidden=!match.cover;cover.src=match.cover?'/api/face/'+match.cover:'';}
  return new Promise(resolve=>{
   let settled=false;const finish=value=>{if(settled)return;settled=true;dialog.close();resolve(value);};
   $('#person-conflict-merge').onclick=()=>finish('merge');$('#person-conflict-separate').onclick=()=>finish('separate');$('#person-conflict-cancel').onclick=()=>finish('cancel');
   dialog.oncancel=e=>{e.preventDefault();finish('cancel');};dialog.onclose=()=>finish('cancel');showDialog('#person-name-conflict-dialog');
  });
 }
 async function mergePersonFromNaming(source,target,match){
  await api('/api/people/'+source+'/merge',{method:'POST',body:JSON.stringify({target_id:target})});
  try{
   state.peopleSelected.delete(Number(source));removePersonFromLocalState(source);
   if(state.detail&&Array.isArray(state.detail.faces))state.detail.faces.forEach(face=>{if(Number(face.person_id)===Number(source)){face.person_id=Number(target);face.name=match.name;face.alias=match.alias||'';}});
   if(Number(state.personId)===Number(source)&&$('#person-dialog')?.open)await globalThis['openPerson'](Number(target));else applyNamedPersonToOpenPhoto(Number(target),match.name,match.alias||'');
   updatePeopleMerge();
  }catch(e){showNotice('人物已合并，界面刷新未完成，请刷新页面',true);}
  return {merged:true,target:Number(target)};
 }
function applyNamedPersonToOpenPhoto(id,name,alias){
 id=Number(id);
 if(!state.detail||!Array.isArray(state.detail.faces))return;
 let changed=false;
 state.detail.faces.forEach(face=>{
  if(Number(face.person_id)!==id)return;
  face.name=name;
  face.alias=alias||'';
  face.ignored=0;
  changed=true;
 });
 if(changed && typeof renderFaceNames==='function') renderFaceNames(state.detail);
 const people=$('#detail-people');
 if(people && changed){
  people.innerHTML=state.detail.faces.map(p=>`<button type="button" data-person="${p.person_id}"><img src="/api/face/${p.id}" alt="">${esc(p.alias?((p.name||('待命名 '+p.person_id))+' / '+p.alias):(p.name||('待命名 '+p.person_id)))}</button>`).join('');
 }
}
async function rememberPersonName(id,name,alias){
 name=normalizePersonText(name);
 alias=normalizePersonText(alias);
 const matches=await api(personNameMatchesUrl(name,alias,id));
 const exact=(matches.exact||[])[0];
 if(exact){
  const choice=await confirmPersonNameConflict(exact);
  if(choice==='cancel')return {cancelled:true};
  if(choice==='merge')return mergePersonFromNaming(Number(id),Number(exact.id),exact);
 }else if((matches.same_name||[]).length){
  const same=(matches.same_name||[])[0];
  showNotice(`已有同名人物：${personLabel(same)} · ${fmt(same.photo_count||0)} 张照片`);
 }
 await api('/api/people/'+id,{method:'PATCH',body:JSON.stringify({name:name,alias:alias})});
 let item=patchPersonInStream(id,{name:name,alias:alias,confirmed:1,ignored:0});
 if(!item){
  const data=await api(peopleQuery({limit:1,ids:String(id)}));
  item=(data.items||[])[0];
  if(item&&state.peopleStream&&state.peopleStream.people&&state.peopleStream.people.items){
   const idx=state.peopleStream.people.items.findIndex(p=>p.id===item.id);
   if(idx>=0)state.peopleStream.people.items[idx]=item;
  }
 }
 if(item)renderPersonCard(item);
 bumpPersonCard(id);
 applyNamedPersonToOpenPhoto(id,name,alias);
 if(state.personDetail && Number(state.personDetail.id)===Number(id)){
  state.personDetail=Object.assign({},state.personDetail,{name,alias,confirmed:1,ignored:0});
  const title=$('#person-title'); if(title) title.textContent=personLabel(state.personDetail);
 }
 refreshStatus().catch(function(){});
}
function personCard(p,mode='people'){
  if(mode==='passersby')return `<button class="person-card" data-person="${p.id}"><img src="/api/face/${p.cover}" alt="路人缩略图" loading="lazy"><b>${esc(p.name||'路人')}</b><p>${p.photo_count} 张照片 · ${p.face_count} 张人脸</p><small>暂不识别，点开可恢复</small></button>`;
  const named=Boolean(p.name && p.name!=='待核对' && p.confirmed);
  const merging=Boolean(state.peopleMerging);
  return `<article class="person-card ${state.peopleSelected.has(p.id)?'selected':''}" data-person="${p.id}">${merging?'<label class="person-check"><input type="checkbox" data-select-person="'+p.id+'" '+(state.peopleSelected.has(p.id)?'checked':'')+' aria-label="选择这个人"></label>':''}<button type="button" class="person-open" data-open-person="${p.id}"><img src="/api/face/${p.cover}" alt="人物候选缩略图" loading="lazy"><b>${esc(personLabel(p))}</b><p>${p.photo_count} 张</p></button>${named?'':'<button type="button" class="text-button person-quick-name" data-quick-name="'+p.id+'">命名</button><button type="button" class="text-button person-ignore" data-ignore-person="'+p.id+'">标为路人</button>'}</article>`;
}
async function fetchPeoplePage(kind, reset=false){
  const stream=state.peopleStream[kind];
  if(stream.loading||(!reset&&!stream.more))return;
  if(reset){stream.offset=0;stream.items=[];stream.more=true;}
  stream.loading=true;stream.generation++; const ticket=stream.generation;
  const status=$(kind==='people'?'#people-stream-status':'#passersby-stream-status');
  if(status)status.textContent=reset?'正在加载…':'继续加载…';
  try{
    const data=await api(peopleQuery({ignored:kind==='passersby'?1:0,q:stream.q,offset:stream.offset,limit:48}));
    if(ticket!==stream.generation)return;
    stream.total=data.total; stream.items=reset?data.items:stream.items.concat(data.items); stream.offset=stream.items.length; stream.more=stream.items.length<data.total;
    const grid=$(kind==='people'?'#people-grid':'#passersby-grid');
    const total=$(kind==='people'?'#people-total':'#passersby-total');
    if(kind==='people')state.people=stream.items; else state.passersby=stream.items;
    if(total)total.textContent=kind==='people'?'':(fmt(stream.total)+' 组路人');
    if(grid)grid.innerHTML=stream.items.length?stream.items.map(p=>personCard(p,kind)).join(''):`<div class="no-results" style="grid-column:1/-1">${kind==='people'?'还没有人物分组。到“扫描与入库”勾选人脸检测，扫描照片后即可核对和命名。':'还没有标为路人的面孔。'}</div>`;
    if(status)status.textContent=stream.more?'向下滚动继续加载':(stream.items.length?'已显示全部':'');
    if(kind==='people')updatePeopleMerge();
  }finally{ if(ticket===stream.generation)stream.loading=false; }
}
async function loadPeopleOptions(){
  const current=state.person?await api(peopleQuery({limit:8,ids:state.person})): {items:[]};
  const named=await api(peopleQuery({limit:40}));
  const ignored=await api(peopleQuery({ignored:1,limit:1}));
  const options=new Map();
  options.set('','全部人物');
  for(const p of [...current.items,...named.items]) options.set(String(p.id), `${personLabel(p)} (${p.photo_count})`);
  $('#person-filter').innerHTML=[...options.entries()].map(([id,label])=>`<option value="${esc(id)}">${esc(label)}</option>`).join('');
  $('#person-filter').value=state.person;
  $('#people-count').textContent=fmt(named.total);
  const pc=$('#passersby-count'); if(pc)pc.textContent=fmt(ignored.total);
}
async function loadPeople(){state.peopleStream.people.q=($('#people-search')?.value||'').trim(); await fetchPeoplePage('people',true); loadPeopleOptions();}
async function loadPassersby(){state.peopleStream.passersby.q=($('#passersby-search')?.value||'').trim(); await fetchPeoplePage('passersby',true); loadPeopleOptions();}
async function fillMergeTargets(query, selectedId){
  const ignored=state.personDetail?.ignored?1:0;
  const data=await api(peopleQuery({ignored,q:query||'',limit:40,ids:selectedId?String(selectedId):''}));
  const items=(data.items||[]).filter(p=>p.id!==state.personId);
  const select=$('#merge-target');
  if(!select)return;
  select.innerHTML='<option value="">选择已有的人物…</option>'+items.map(p=>`<option value="${p.id}" ${Number(selectedId)===p.id?'selected':''}>${esc(personLabel(p))} · ${p.photo_count} 张照片</option>`).join('');
  if(selectedId&&!items.some(p=>p.id===Number(selectedId)))select.value='';
}
async function loadTimeline(){
 const data=await api('/api/timeline');
 const years=data.years||[];
 $('#timeline-total').textContent=years.length?fmt(data.dated)+' 张':'还没有可按年汇总的照片';
 $('#timeline-list').innerHTML=years.length?years.map(y=>{
  const label=y.year==='unknown'?'时间未记录':y.year+' 年';
  return '<button class="facet-card" data-year="'+esc(y.year)+'"><b>'+esc(label)+'</b><span>'+fmt(y.count)+' 张</span></button>';
 }).join(''):'<div class="no-results">还没有可按年份汇总的照片。</div>';
}
function groupLabel(g){return g.key==='10plus'||g.people>=10?'10人及以上':g.people+'人合影';}
async function loadObjects(){const data=await api('/api/objects');const job=await api('/api/objects/job').catch(()=>({status:'idle'}));const count=$('#objects-count');if(count)count.textContent=fmt(data.tagged||0);$('#objects-total').textContent=fmt(data.tagged||0)+' tagged / '+fmt(data.pending||0)+' pending';$('#objects-job').textContent=job.status==='running'?('running '+fmt(job.processed||0)+' / '+fmt(job.limit||0)):(job.message||'idle');$('#objects-list').innerHTML=(data.items||[]).map(x=>'<button class="facet-card" data-object="'+esc(x.label)+'"><b>'+esc(x.label)+'</b><span>'+fmt(x.n)+'</span></button>').join('')||'<div class="no-results">no object tags yet</div>';}
async function loadGroups(){
 const data=await api('/api/groups');
 const items=data.items||[];
 const count=$('#groups-count'); if(count) count.textContent=fmt(data.photos||0);
 const ready=items.some(g=>g.count);
 $('#groups-total').textContent=ready?fmt(data.photos)+' 张合影':'暂无合影';
 $('#groups-list').innerHTML=items.map(g=>{
  const empty=g.count? '':' empty';
  const coverKey=g.key==='10plus'?'10':g.key;
  const label=groupLabel(g);
  const cover='<img src="/assets/group-covers/'+encodeURIComponent(coverKey)+'.png" alt="" loading="lazy" decoding="async">';
  const arrow='<span class="group-arrow" aria-hidden="true"><svg viewBox="0 0 24 24" focusable="false"><path d="M5 12h13M13 6l6 6-6 6"/></svg></span>';
  return '<button class="group-card'+empty+'" data-group="'+esc(g.key)+'" '+(g.count?'':'disabled')+'><span class="group-cover">'+cover+'</span><span class="group-copy"><span class="group-copy-text"><b>'+esc(label)+'</b><em>'+(g.count?fmt(g.count)+' 张':'还没有')+'</em></span>'+arrow+'</span></button>';
 }).join('')||'<div class="no-results">还没有识别出两人及以上的合影。单人照和没认出脸的照片不进入这里。</div>';
}
const BEIJING_VIEW={lat:39.9042,lng:116.4074,zoom:11};
const WORLD_VIEW={lat:30,lng:20,zoom:2};
const PLACE_BASEMAPS={
  gaode:{url:'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}',options:{subdomains:'1234',maxZoom:18,attribution:'高德地图'}},
  osm:{url:'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',options:{maxZoom:19,attribution:'&copy; OpenStreetMap'}}
};
function setPlaceBasemap(name){
  const spec=PLACE_BASEMAPS[name]||PLACE_BASEMAPS.gaode;
  if(!state.placeMap)return;
  if(state.placeTiles)state.placeMap.removeLayer(state.placeTiles);
  state.placeTiles=L.tileLayer(spec.url,spec.options).addTo(state.placeMap);
  try{localStorage.setItem('shiguang-basemap', name);}catch(e){}
}
function placeZoomFactor(zoom){
  const z=Number(zoom);
  if(!Number.isFinite(z))return 1;
  return Math.pow(2, BEIJING_VIEW.zoom-z);
}
function placeZoomFromFactor(factor){
  const n=Number(factor);
  if(!Number.isFinite(n)||n<=0)return BEIJING_VIEW.zoom;
  return Math.max(2, Math.min(19, BEIJING_VIEW.zoom-Math.log2(n)));
}
function updatePlaceZoomInput(){
  const input=$('#places-zoom'); if(!input||!state.placeMap||document.activeElement===input)return;
  const factor=placeZoomFactor(state.placeMap.getZoom());
  input.value=factor>=10?factor.toFixed(0):(factor>=2?factor.toFixed(1):factor.toFixed(2));
}
function applyPlaceZoomInput(){
  const map=ensurePlaceMap();
  const factor=Number($('#places-zoom').value);
  map.setZoom(placeZoomFromFactor(factor));
  updatePlaceZoomInput();
}
function nudgePlaceZoom(delta){
  const map=ensurePlaceMap();
  map.setZoom(map.getZoom()+delta);
  updatePlaceZoomInput();
}
function ensurePlaceMap(){
  if(state.placeMap)return state.placeMap;
  if(typeof L==='undefined'){toast('地图组件还没准备好，照片仍可浏览',true);return null;}
  const map=L.map('places-map',{zoomControl:true,attributionControl:true}).setView([BEIJING_VIEW.lat,BEIJING_VIEW.lng],BEIJING_VIEW.zoom);
  state.placeMap=map;
  let preferred='gaode'; try{preferred=localStorage.getItem('shiguang-basemap')||'gaode'; if(preferred==='carto'||preferred==='paper')preferred='gaode';}catch(e){}
  const select=$('#places-basemap'); if(select)select.value=PLACE_BASEMAPS[preferred]?preferred:'gaode';
  setPlaceBasemap(select&&select.value||'gaode');
  const cluster=L.markerClusterGroup({showCoverageOnHover:false,maxClusterRadius:48,disableClusteringAtZoom:16});
  map.addLayer(cluster);
  state.placeCluster=cluster; state.placeMapTimer=null;
  map.on('moveend zoomend',()=>{updatePlaceZoomInput();clearTimeout(state.placeMapTimer);state.placeMapTimer=setTimeout(()=>{if(state.view==='places')loadPlaceMap();},180);}); updatePlaceZoomInput();
  return map;
}
async function loadPlaceMap(){
  const map=ensurePlaceMap();
  map.invalidateSize();
  const b=map.getBounds();
  const params=new URLSearchParams({west:b.getWest(),south:b.getSouth(),east:b.getEast(),north:b.getNorth(),zoom:String(map.getZoom())});
  const data=await api('/api/places?'+params);
  $('#places-total').textContent=(data.clusters&&data.clusters.length?fmt(data.clusters.reduce(function(n,x){return n+(x.count||0);},0))+' 张在当前视野':'当前视野还没有坐标点');
  state.placeCluster.clearLayers();
  for(const item of data.clusters||[]){
    if(item.latitude==null||item.longitude==null)continue;
    const marker=L.marker([item.latitude,item.longitude]);
    marker.bindPopup('<button type="button" class="place-popup" data-place="'+esc(item.place||'')+'"><span class="place-popup-kicker">拍摄地点</span><strong>'+esc(item.place||'未命名地点')+'</strong><em>'+fmt(item.count)+' 张照片</em><span class="place-popup-go">查看这些照片</span></button>', {className:'place-popup-wrap', closeButton:true, maxWidth:280});
    state.placeCluster.addLayer(marker);
  }
  const status=$('#places-stream-status'); if(status)status.textContent=data.clusters&&data.clusters.length?'点击圆点查看该处照片':'这一层视野里还没有坐标点，可缩小地图或回到北京';
}
async function loadPlaces(reset=true){
  const q=($('#places-search')&&$('#places-search').value||'').trim();
  if(reset){state.placeStream={items:[],offset:0,total:0,more:true,loading:false,q:q,unknown:0,dated:0};}
  const stream=state.placeStream; if(stream.loading||(!reset&&!stream.more))return; stream.loading=true;
  const status=$('#places-stream-status'); if(status&&q)status.textContent=reset?'正在搜索地点…':'继续加载…';
  try{
    if(!q){ await loadPlaceMap(); return; }
    const data=await api('/api/places?'+new URLSearchParams({q:stream.q,offset:String(stream.offset),limit:'24'}));
    stream.total=data.total; stream.unknown=data.unknown||0; stream.dated=data.dated||0;
    stream.items=reset?data.places:stream.items.concat(data.places||[]);
    stream.offset=stream.items.length; stream.more=stream.items.length<data.total;
    const unknownCard=stream.unknown?'<button class="facet-card" data-place="unknown"><b>地点未记录</b><span>'+fmt(stream.unknown)+' 张</span></button>':'';
    $('#places-list').innerHTML=(stream.items.map(p=>'<button class="facet-card" data-place="'+esc(p.place)+'"><b>'+esc(prettyPlace(p.place))+'</b><span>'+fmt(p.count)+' 张</span></button>').join('')+unknownCard)||'<div class="no-results">还没有可按地点汇总的照片。</div>';
    if(q&&status)status.textContent=stream.more?'向下滚动继续加载':(stream.items.length?'已显示全部':'');
  }finally{stream.loading=false;}
}
function pickMergeTarget(ids,items){const selected=new Set((ids||[]).map(Number));const pool=(items||[]).filter(p=>selected.has(Number(p.id)));if(!pool.length)return null;const rank=(a,b)=>(b.photo_count-a.photo_count)||(a.id-b.id);const named=pool.filter(p=>p.confirmed&&p.name).slice().sort(rank);const rest=pool.slice().sort(rank);const target=named[0]||rest[0];if(target&&target.ignored&&rest.some(p=>!p.ignored))return rest.find(p=>!p.ignored)||null;return target||null;}
async function resolveMergeTarget(ids){if(!ids.length)throw new Error('请先选择要合并的人物');if(ids.length>100)throw new Error('一次最多选择 100 个人物');const data=await api(peopleQuery({limit:1,ids:ids.join(',')}));const pool=(data.items||[]).filter(p=>ids.includes(p.id));if(!pool.length)throw new Error('没有找到所选人物，请刷新后再试');const target=pickMergeTarget(ids,pool);if(!target)throw new Error('没有可合并的目标');if(!ids.includes(target.id))throw new Error('合并目标必须属于当前选择');return target;}
function faceItemHtml(f,person){return `<div class="face-item" data-face-id="${f.id}"><img src="/api/face/${f.id}" data-face-photo="${f.asset_id}" alt="点击查看原照片" tabindex="0"><button type="button" class="text-button" data-split="${f.id}">这不是同一个人 · 移出</button><button type="button" class="text-button" data-ignore-face="${f.id}">${person.ignored?'这张恢复识别':'这张是路人'}</button></div>`;}
function updatePersonFaceStatus(){const p=state.personDetail;const status=$('#person-faces-status');if(!status||!p)return;const n=(p.faces||[]).length;const total=p.face_count||n;if(state.personFaces&&state.personFaces.loading){status.hidden=false;status.textContent='正在加载人脸';return;}if(p.more){status.hidden=false;status.textContent='已显示 '+fmt(n)+' / '+fmt(total)+' · 继续下滚加载';return;}status.hidden=!n;status.textContent=n?('已显示全部 '+fmt(total)+' 张人脸'):'';}
function renderPersonFaces(reset=false, appended=[]){const p=state.personDetail;if(!p)return;const faces=p.faces||[];const grid=$('#person-faces');if(!grid)return;if(reset)grid.innerHTML=faces.map(f=>faceItemHtml(f,p)).join('');else (appended||[]).forEach(f=>{if(!grid.querySelector('[data-face-id="'+f.id+'"]'))grid.insertAdjacentHTML('beforeend',faceItemHtml(f,p));});updatePersonFaceStatus();}
function maybeLoadMorePersonFaces(){const p=state.personDetail;const body=$('.person-body');if(!p||!p.more||!body)return;if(state.personFaces&&state.personFaces.loading)return;if(body.scrollTop+body.clientHeight>body.scrollHeight-160)loadPersonFaces(false);}
async function removePersonFaceLocally(fid){const p=state.personDetail;if(!p)return;const id=Number(fid);const body=$('.person-body');const top=body?body.scrollTop:0;p.faces=(p.faces||[]).filter(f=>Number(f.id)!==id);if(typeof p.face_count==='number'&&p.face_count>0)p.face_count-=1;const item=$('#person-faces [data-face-id="'+id+'"]');if(!item){if(body)body.scrollTop=top;updatePersonFaceStatus();return;}const height=item.getBoundingClientRect().height;item.style.height=height+'px';item.classList.add('removing');await new Promise(resolve=>setTimeout(resolve,180));if(item.parentNode)item.remove();if(body)body.scrollTop=top;updatePersonFaceStatus();}
function bindPersonFaceScroll(){const body=$('.person-body');if(!body||body.dataset.scrollBound)return;body.dataset.scrollBound='1';body.addEventListener('scroll',()=>maybeLoadMorePersonFaces(),{passive:true});}
async function loadPersonFaces(reset=false){const id=state.personId;if(!id)return;const stream=state.personFaces||(state.personFaces={generation:0,offset:0,loading:false});if(stream.loading&&!reset)return;if(reset){stream.offset=0;}const ticket=++stream.generation;const offset=reset?0:stream.offset;stream.loading=true;try{const p=await api('/api/people/'+id+'?offset='+offset+'&limit=48');if(ticket!==stream.generation||Number(state.personId)!==Number(id))return;const incoming=p.faces||[];const existing=reset?[]:((state.personDetail&&state.personDetail.faces)||[]);const seen=new Set(existing.map(f=>f.id));const appended=incoming.filter(f=>!seen.has(f.id));p.faces=existing.concat(appended);state.personDetail=p;stream.offset=(typeof p.offset==='number'?p.offset+incoming.length:p.faces.length);renderPersonFaces(reset, appended);}finally{if(ticket===stream.generation)stream.loading=false;}}
function updateBatch(){$('#batch-bar').hidden=!state.selecting;$('#selected-count').textContent=`已选 ${state.selected.size} 张`;$('#select-mode').textContent=state.selecting?'退出批量':'批量补录';$('#batch-edit').disabled=!state.selected.size;$('#batch-exclude').disabled=!state.selected.size;$('#batch-restore').disabled=!state.selected.size;$('#batch-exclude').hidden=state.view==='excluded';$('#batch-restore').hidden=state.view!=='excluded';window.__ourTimeHomeSync?.();}
async function renderPhoto(id){const ticket=++renderPhoto.ticket;const a=await api('/api/photos/'+id);if(ticket!==renderPhoto.ticket)return false;state.detail=a;$('#exclude-photo').hidden=!a.in_library;$('#restore-photo').hidden=a.in_library||!a.excluded;$('#exclusion-detail-note').textContent=a.in_library?'':a.excluded?'排除原因：'+a.exclude_reason:'此图片受目录排除规则影响，请到“已排除”恢复对应目录。';$('#detail-name').textContent=basename(a.files[0]?.path)||'照片 '+id;$('#original-link').href='/api/original/'+id;$('#detail-facts').innerHTML=`<div class="fact"><span>当前时间</span><p>${esc(a.effective_date||'未知')}<small>${esc(dateSource(a.effective_source)||'无来源')} · ${esc(a.effective_precision||'未知精度')}</small></p></div><div class="fact"><span>原始时间</span><p>${esc(a.captured_at||'未读取到')}<small>${esc(dateSource(a.date_source))}</small></p></div><div class="fact"><span>地点</span><p>${esc(prettyPlace(a.effective_place)||'待补充')}<small>${esc(a.manual_place?'人工补录':a.place_source||'未记录地点')}</small></p></div>${a.latitude!==null?`<div class="fact"><span>定位坐标</span><p>${a.latitude.toFixed(6)}, ${a.longitude.toFixed(6)}</p></div>`:''}<div class="fact"><span>照片信息</span><p>${esc(a.format)} · ${a.width||'?'} × ${a.height||'?'}<small>${esc(a.camera||'未记录设备')} · ${esc(a.category)}</small></p></div>${a.error||a.face_error?`<div class="fact"><span>读取问题</span><p class="tag error">${esc(readableError(a.error||a.face_error))}</p></div><details><summary>原始诊断（技术信息）</summary><pre>${esc(a.error||a.face_error)}</pre></details>`:''}`;$('#detail-people').innerHTML=a.faces.map(p=>`<button type="button" data-person="${p.person_id}"><img src="/api/face/${p.id}" alt="">${esc(p.alias?((p.name||'待命名 '+p.person_id)+' / '+p.alias):(p.name||'待命名 '+p.person_id))}</button>`).join('');$('#edit-date').value=a.manual_date||'';$('#edit-precision').value=a.manual_precision||'年';$('#edit-place').value=a.manual_place||'';$('#edit-notes').value=a.notes||'';$('#raw-metadata').textContent=JSON.stringify(a.metadata,null,2);$('#file-paths').innerHTML=a.files.map(f=>`<div class="file-row">${esc(f.path)}<br>${fmt(f.size)} 字节 · 修改于 ${esc(f.modified_at)} · ${f.exists_now?'上次扫描存在':'原文件缺失'}${f.excluded?' · 所在目录已排除':''}</div>`).join('');$('#edit-history').innerHTML=a.history.map(e=>`<div class="file-row">${esc(e.created_at)}<br>${esc(e.after_json)}</div>`).join('');showDialog('#detail-dialog');return true;}
async function openPerson(id){state.personId=id;hideNotice();state.personFaces={generation:0,offset:0,loading:false};const p=await api('/api/people/'+id+'?limit=48');state.personDetail=p;state.personFaces.offset=(p.faces||[]).length;$('#person-eyebrow').textContent=p.ignored?'路人':'熟悉的面孔';$('#person-title').textContent=personLabel(p);const count=p.face_count||(p.faces||[]).length;$('#person-help').hidden=true;$('#person-title').dataset.count=fmt(count);$('#person-name').value=p.ignored?'':((p.name&&p.name!=='待核对')?p.name:'');$('#person-alias').value=p.alias||'';$('#ignore-person').textContent=p.ignored?'恢复识别':'标为路人';await fillMergeTargets('', p.suggested_person_id);renderPersonFaces(true);showDialog('#person-dialog');bindPersonFaceScroll();maybeLoadMorePersonFaces();}
async function openFolder(path=''){const data=await api('/api/folders?path='+encodeURIComponent(path));state.folder=data.path;$('#folder-current').textContent=data.path||'本机磁盘';$('#folder-list').innerHTML=(data.parent!==null?`<button data-folder="${esc(data.parent)}">↑ 返回上一级</button>`:'')+data.items.map(x=>`<button data-folder="${esc(x.path)}">▱ ${esc(x.name)}</button>`).join('');$('#use-folder').disabled=!data.path;showDialog('#folder-dialog');}
document.addEventListener('click',action(async e=>{
 if(e.target.closest('[data-folder-toggle]'))return;
 const add=e.target.closest('[data-folder-add]');
 if(add){e.preventDefault();e.stopPropagation();state.folderTarget='scan';await openFolder(add.dataset.folderAdd||'I:\\');return;}
 const open=e.target.closest('[data-folder-open]');
 if(open){e.preventDefault();await loadFolderBrowser(open.dataset.folderOpen);return;}
}));
$('#folders-up')&&$('#folders-up').addEventListener('click',action(async()=>{const data=await api('/api/folders?path='+encodeURIComponent(state.folderPath||'')); const parent=data.parent||''; const slash=String.fromCharCode(92); await loadFolderBrowser(parent && folderDrive(parent)===FOLDER_ONLY_DRIVE ? parent : (FOLDER_ONLY_DRIVE+slash));}));
$('#folders-scan-new')&&$('#folders-scan-new').addEventListener('click',action(async()=>{const path=state.folderPath;if(!path)throw new Error('先进入一个盘符或文件夹，再扫描新照片');state.scanStartedThisVisit=true;state.scanShowCompletedResult=false;await api('/api/scan',{method:'POST',body:JSON.stringify({roots:[path],with_faces:true,include_system:false,workers:1})});toast('开始增量扫描，已入库的会跳过');await setView('scan');state.scanStartedThisVisit=true;state.scanShowCompletedResult=false;const status=await refreshStatus();if(status.job&&['completed','completed_with_errors','failed'].includes(status.job.status)){state.scanShowCompletedResult=true;renderScanStatus(status);}}));
document.addEventListener('change',action(async e=>{const box=e.target.closest('[data-folder-toggle]');if(!box)return;await toggleFolderInclusion(box.dataset.folderToggle, box.checked);}));
$$('[data-view]').forEach(b=>b.addEventListener('click',action(()=>setView(b.dataset.view))));$$('[data-jump]').forEach(b=>b.addEventListener('click',action(()=>setView(b.dataset.jump))));['#add-folder','#empty-add'].forEach(s=>$(s).addEventListener('click',action(()=>openAddPhotos())));$$('[data-close]').forEach(b=>b.addEventListener('click',()=>$('#'+b.dataset.close).close()));

async function findCachedPerson(id){
 id=Number(id);
 return (state.people||[]).find(p=>Number(p.id)===id)
  ||(state.passersby||[]).find(p=>Number(p.id)===id)
  ||((state.peopleStream&&state.peopleStream.people&&state.peopleStream.people.items)||[]).find(p=>Number(p.id)===id)
  ||(state.personDetail&&Number(state.personDetail.id)===id?state.personDetail:null)
  ||null;
}
function personStubFromOpenPhoto(id){
 id=Number(id);
 const face=(state.detail&&state.detail.faces||[]).find(f=>Number(f.person_id)===id);
 if(!face) return null;
 return {id, name:face.name||'', alias:face.alias||'', ignored:face.ignored||0};
}
function displayNameValue(name){return (name&&name!=='待核对'&&name!=='命名')?name:'';}
let quickMergeTargets=[];let quickMergeTimer=null;
function resetQuickMerge(){
  quickMergeTargets=[];clearTimeout(quickMergeTimer);quickMergeTimer=null;
  const toggle=$('#quick-merge-toggle'),panel=$('#quick-merge-panel'),search=$('#quick-merge-search'),select=$('#quick-merge-target');
  if(toggle){toggle.setAttribute('aria-expanded','false');}
  if(panel)panel.hidden=true;
  if(search)search.value='';
  if(select)select.replaceChildren(new Option('选择已有的人物…',''));
}
async function loadQuickMergeTargets(){
  const source=Number(state.quickNameId);if(!source)return;
  const ignored=state.quickNameIgnored?1:0;
  const query=$('#quick-merge-search')?.value.trim()||'';
  const select=$('#quick-merge-target');if(!select)return;
  select.replaceChildren(new Option('正在读取人物…',''));select.disabled=true;
  try{
    const data=await api(peopleQuery({ignored,q:query,limit:40}));
    if(source!==Number(state.quickNameId))return;
    quickMergeTargets=(data.items||[]).filter(p=>Number(p.id)!==source);
    select.replaceChildren(new Option('选择已有的人物…',''),...quickMergeTargets.map(p=>new Option(`${personLabel(p)} · ${p.photo_count||0} 张`,String(p.id))));
  }finally{if(source===Number(state.quickNameId))select.disabled=false;}
}
async function openQuickName(id){
 id=Number(id);
 if(!id) throw new Error('没有要命名的人');
 let person=await findCachedPerson(id);
 if(!person){
  try{
   const data=await api(peopleQuery({limit:1,ids:String(id)}));
   person=(data.items||[]).find(p=>Number(p.id)===Number(id));
  }catch(e){ person=null; }
 }
 if(!person) person=personStubFromOpenPhoto(id);
 if(!person) throw new Error('人物不存在');
 state.quickNameId=id;
 state.quickNameIgnored=Boolean(person.ignored);
 resetQuickMerge();
 $('#quick-name-title').textContent=person.name?personLabel(person):'给这个人一个名字';
 $('#quick-name-input').value=displayNameValue(person.name);
 $('#quick-alias-input').value=person.alias||'';
 showDialog('#quick-name-dialog');
 requestAnimationFrame(()=>{$('#quick-name-input').focus();$('#quick-name-input').select();});
}
document.addEventListener('click',action(async e=>{const quick=e.target.closest('[data-quick-name]');if(!quick)return;e.preventDefault();e.stopPropagation();await openQuickName(Number(quick.dataset.quickName));}));
$('#quick-name-dialog')&&$('#quick-name-dialog').addEventListener('click',e=>{if(e.target===$('#quick-name-dialog'))$('#quick-name-dialog').close();});
$('#quick-name-dialog')&&$('#quick-name-dialog').addEventListener('close',resetQuickMerge);
$('#quick-merge-toggle')&&$('#quick-merge-toggle').addEventListener('click',()=>{const panel=$('#quick-merge-panel'),toggle=$('#quick-merge-toggle');const open=Boolean(panel?.hidden);if(panel)panel.hidden=!open;if(toggle)toggle.setAttribute('aria-expanded',String(open));if(open)loadQuickMergeTargets().catch(err=>toast(err.message||'人物列表读取失败',true));});
$('#quick-merge-search')&&$('#quick-merge-search').addEventListener('input',()=>{clearTimeout(quickMergeTimer);quickMergeTimer=setTimeout(()=>loadQuickMergeTargets().catch(err=>toast(err.message||'人物列表读取失败',true)),250);});
$('#quick-merge-submit')&&$('#quick-merge-submit').addEventListener('click',action(async()=>{const source=Number(state.quickNameId),target=Number($('#quick-merge-target')?.value);const match=quickMergeTargets.find(p=>Number(p.id)===target);if(!source||!target||!match){toast('请先选择要合并到的人物',true);return;}const button=$('#quick-merge-submit');if(button.disabled)return;button.disabled=true;try{await mergePersonFromNaming(source,target,match);$('#quick-name-dialog').close();}catch(err){toast(err.message||'人物合并失败',true);}finally{button.disabled=false;}}));
$('#quick-name-form')&&$('#quick-name-form').addEventListener('submit',async e=>{e.preventDefault();const id=state.quickNameId;if(!id){toast('没有要命名的人',true);return;}const submit=e.submitter||$('#quick-name-form button[type="submit"]');if(submit)submit.disabled=true;try{const result=await rememberPersonName(id,$('#quick-name-input').value,$('#quick-alias-input').value);if(result?.cancelled)return;$('#quick-name-dialog').close();if(!result?.merged)toast('人物名字已保存');}catch(err){toast(err.message||'人物名字保存失败',true);}finally{if(submit)submit.disabled=false;}});

$('#person-dialog').addEventListener('click',async e=>{
  if(e.target===$('#person-dialog')){$('#person-dialog').close();return;}
  const ignoreFace=e.target.closest('[data-ignore-face]');
  if(!ignoreFace)return;
  e.preventDefault();e.stopPropagation();
  const p=state.personDetail; if(!p)return;
  const ignored=!(p.ignored);
  const fid=ignoreFace.dataset.ignoreFace;
  ignoreFace.disabled=true;
  try{
    await api('/api/faces/'+fid+'/ignore',{method:'POST',body:JSON.stringify({ignored})});
    if(ignored) await removePersonFaceLocally(fid);
    else await openPerson(state.personId);
  }catch(err){ toast(err.message||'这张没能标为路人', true); }
  finally{ ignoreFace.disabled=false; }
});
$('#search').addEventListener('input',()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(action(async()=>{state.q=$('#search').value.trim();state.offset=0;await loadPhotos();}),250);});
$('#place-refine-input').addEventListener('input',()=>{clearTimeout(state.placeSuggestTimer);state.placeSuggestTimer=setTimeout(action(()=>loadPlaceSuggestions($('#place-refine-input').value.trim())),200);});
$('#place-refine-save').addEventListener('click',action(()=>savePlaceRefine()));
$('#place-refine-suggest').addEventListener('click',e=>{const b=e.target.closest('[data-place-suggest]');if(!b)return;$('#place-refine-input').value=b.dataset.placeSuggest;});
$('#person-filter').addEventListener('change',action(async()=>{state.person=$('#person-filter').value;state.offset=0;await loadPhotos();}));
const personFilterSearch=$('#person-filter-search'); personFilterSearch&&personFilterSearch.addEventListener('input',()=>{clearTimeout(state.personFilterTimer);state.personFilterTimer=setTimeout(action(async()=>{const data=await api(peopleQuery({q:$('#person-filter-search').value.trim(),limit:40,ids:state.person}));const options=new Map([['','全部人物']]);for(const p of data.items)options.set(String(p.id), personLabel(p)+' ('+p.photo_count+')');$('#person-filter').innerHTML=[...options.entries()].map(([id,label])=>'<option value="'+esc(id)+'">'+esc(label)+'</option>').join('');$('#person-filter').value=state.person;}),250);});
$('#people-search').addEventListener('input',()=>{clearTimeout(state.peopleSearchTimer);state.peopleSearchTimer=setTimeout(action(()=>loadPeople()),250);});
$('#places-search').addEventListener('input',()=>{clearTimeout(state.placesSearchTimer);state.placesSearchTimer=setTimeout(action(()=>loadPlaces(true)),250);});
$('#places-beijing').addEventListener('click',action(async()=>{ensurePlaceMap().setView([BEIJING_VIEW.lat,BEIJING_VIEW.lng],BEIJING_VIEW.zoom);await loadPlaceMap();}));
$('#places-world').addEventListener('click',action(async()=>{ensurePlaceMap().setView([WORLD_VIEW.lat,WORLD_VIEW.lng],WORLD_VIEW.zoom);await loadPlaceMap();}));
$('#places-unknown').addEventListener('click',action(()=>setView('place:unknown')));
$('#places-basemap').addEventListener('change',()=>setPlaceBasemap($('#places-basemap').value));
$('#places-zoom-out').addEventListener('click',()=>nudgePlaceZoom(-1));
$('#places-zoom-in').addEventListener('click',()=>nudgePlaceZoom(1));
$('#places-zoom-reset').addEventListener('click',action(async()=>{ensurePlaceMap().setView([BEIJING_VIEW.lat,BEIJING_VIEW.lng],BEIJING_VIEW.zoom);updatePlaceZoomInput();await loadPlaceMap();}));
$('#places-zoom').addEventListener('change',()=>applyPlaceZoomInput());
$('#places-zoom').addEventListener('keydown',e=>{if(e.key==='Enter'){e.preventDefault();applyPlaceZoomInput();}});
$('#passersby-search').addEventListener('input',()=>{clearTimeout(state.passersbySearchTimer);state.passersbySearchTimer=setTimeout(action(()=>loadPassersby()),250);});
$('#merge-search').addEventListener('input',()=>{clearTimeout(state.mergeSearchTimer);state.mergeSearchTimer=setTimeout(action(()=>fillMergeTargets($('#merge-search').value.trim(), Number($('#merge-target').value)||state.personDetail&&state.personDetail.suggested_person_id)),250);});
window.addEventListener('scroll',()=>{if(state.view==='places'){const status=$('#places-stream-status');if(status&&status.getBoundingClientRect().top<window.innerHeight+200)loadPlaces(false);return;}const kind=state.view==='people'?'people':state.view==='passersby'?'passersby':null;if(!kind)return;const status=$(kind==='people'?'#people-stream-status':'#passersby-stream-status');if(!status)return;const rect=status.getBoundingClientRect();if(rect.top<window.innerHeight+200)fetchPeoplePage(kind);},{passive:true});
$('#timeline-tools').addEventListener('click',action(async e=>{const sort=e.target.closest('[data-time-sort]');if(sort){state.sort=sort.dataset.timeSort;$('#sort-order').value=state.sort;if(state.view==='years')await setView('timeline');else await setView(isTimelineView()?state.view:'timeline');return;}if(e.target.id==='timeline-by-year')await setView('years');}));
$('#home-years-link').addEventListener('click',action(()=>setView('years')));
$('#groups-back').addEventListener('click',action(()=>setView('groups')));
$('#timeline-back').addEventListener('click',action(()=>setView('timeline')));
$('#sort-order').addEventListener('change',action(async()=>{state.sort=$('#sort-order').value;updateTimelineTools();await loadPhotos();}));
$('#refresh-photos').addEventListener('click',action(()=>loadPhotos()));
$('#select-mode').addEventListener('click',action(async()=>{state.selecting=!state.selecting;state.selected.clear();streamSelection();}));
$('#select-page').addEventListener('click',action(async()=>{for(const id of streamVisibleIds()){if(state.selected.size>=1000)break;state.selected.add(id);}streamSelection();}));
$('#clear-selection').addEventListener('click',action(async()=>{state.selected.clear();streamSelection();}));
$('#batch-edit').addEventListener('click',()=>{$('#batch-description').textContent=`将补录 ${state.selected.size} 张照片。原始元数据会完整保留。`;$('#batch-form').reset();showDialog('#batch-dialog');});
$('#batch-form').addEventListener('submit',action(async e=>{e.preventDefault();const body={ids:[...state.selected]};if($('#batch-date-enabled').checked){body.manual_date=$('#batch-date').value;body.manual_precision=$('#batch-precision').value;}if($('#batch-place-enabled').checked)body.manual_place=$('#batch-place').value;if($('#batch-notes-enabled').checked)body.notes=$('#batch-notes').value;await api('/api/photos',{method:'PATCH',body:JSON.stringify(body)});$('#batch-dialog').close();toast('补录已保存，原照片未修改');await refreshStatus();await loadPhotos();}));

$('#detail-form').addEventListener('submit',action(async e=>{e.preventDefault();await api('/api/photos',{method:'PATCH',body:JSON.stringify({ids:[state.detail.id],manual_date:$('#edit-date').value,manual_precision:$('#edit-precision').value,manual_place:$('#edit-place').value,notes:$('#edit-notes').value})});viewer.drafts.delete(state.detail.id);toast('补录已保存，原始信息仍然保留');await openPhoto(state.detail.id);await refreshStatus();await loadPhotos();}));
$('#reveal-button').addEventListener('click',action(async()=>{await api('/api/reveal/'+state.detail.id,{method:'POST'});}));
$('#all-drives').addEventListener('click',action(async()=>{const d=await api('/api/drives');d.roots.filter(p=>!/^c:/i.test(p)).forEach(addScanRoot);toast('已添加本机磁盘；点击“开始扫描”后才会运行');}));
$('#choose-folder').addEventListener('click',action(()=>{state.folderTarget='scan';return openFolder();}));$$('#scan-folder-picker,#scan-add-folder').forEach(button=>button?.addEventListener('click',action(()=>{state.folderTarget='scan';return openFolder();})));$('#use-folder').addEventListener('click',()=>{if(state.folderTarget==='home-query'){const path=state.folder||'';window.__ourTimeHomeFolderResolve?.(path);$('#folder-dialog').close();return;}if(state.folderTarget==='browse'){$('#directory-filter').value=state.folder;$('#folder-dialog').close();$('#apply-directory').click();return;}if(state.folderTarget==='exclude'){$('#exclude-root').value=state.folder;$('#folder-dialog').close();return;}addScanRoot(state.folder);$('#folder-dialog').close();});document.addEventListener('click',e=>{const remove=e.target.closest('[data-remove-scan-root]');if(remove){e.preventDefault();removeScanRoot(remove.dataset.removeScanRoot);}});

$('#probe-objects').addEventListener('click',action(async()=>{const status=$('#object-probe-status'); if(status) status.textContent='正在试扫物体标签…'; const data=await api('/api/objects/probe',{method:'POST',body:JSON.stringify({limit:24})}); const ok=(data.items||[]).filter(x=>x.tags&&x.tags.length); if(status) status.textContent='试扫 '+data.count+' 张，约 '+data.per_image+' 秒/张 · '+data.runtime; toast('物体试扫完成：'+ok.length+' 张打上标签');}));

$('#start-objects')&&$('#start-objects').addEventListener('click',action(async()=>{await api('/api/objects/scan',{method:'POST',body:JSON.stringify({limit:200})});toast('object scan started');await loadObjects();}));
$('#start-scan').addEventListener('click',action(async()=>{const roots=[...(state.scanRoots||[])];if(!roots.length)throw new Error('请先选择照片文件夹');if(state.scanSubmitting)return;const previousJobId=state.status?.job?.id;state.scanStartedThisVisit=true;state.scanShowCompletedResult=false;state.scanSubmitting=true;renderScanRoots();try{await api('/api/scan',{method:'POST',body:JSON.stringify({roots,with_faces:true,include_system:false,workers:1})});state.scanRoots=[];toast('开始扫描，已入库的照片会自动跳过');const status=await refreshStatus();if(status.job&&status.job.id!==previousJobId&&['completed','completed_with_errors','failed'].includes(status.job.status)){state.scanShowCompletedResult=true;renderScanStatus(status);} }finally{state.scanSubmitting=false;renderScanRoots();}}));$('#scan-view-all').addEventListener('click',action(()=>setView('timeline')));
$('#person-form').addEventListener('submit',async e=>{e.preventDefault();const pid=state.personId;const submit=e.submitter||$('#person-form button[type="submit"]');if(submit)submit.disabled=true;try{const result=await rememberPersonName(pid,$('#person-name').value,$('#person-alias').value);if(result?.cancelled)return;if(!result?.merged){toast('人物名字已保存');if($('#person-dialog').open){const item=((state.peopleStream.people||{}).items||[]).find(p=>p.id===Number(pid));if(item){$('#person-title').textContent=personLabel(item);state.personDetail=Object.assign({},state.personDetail,item);}}}}catch(err){showNotice(err.message||'人物名字保存失败',true);}finally{if(submit)submit.disabled=false;}});
$('#merge-person').addEventListener('click',async e=>{e.preventDefault();e.stopPropagation();const button=$('#merge-person');const target=Number($('#merge-target').value);if(!target){showNotice('请先在下拉框里选择要合并到的人',true);return;}if(target===Number(state.personId)){showNotice('不能合并到自己',true);return;}const source=Number(state.personId);if(button?.disabled)return;button.disabled=true;try{await api('/api/people/'+source+'/merge',{method:'POST',body:JSON.stringify({target_id:target})});try{state.peopleSelected.delete(source);removePersonFromLocalState(source);await openPerson(target);showNotice('人物分组已合并');}catch(uiError){showNotice('人物已合并，界面刷新未完成，请刷新页面',true);}refreshStatus().catch(()=>{});}catch(err){showNotice(err.message||'人物合并失败',true);}finally{button.disabled=false;}});
$('#show-person-photos').addEventListener('click',action(async()=>{$('#person-dialog').close();const person=state.personDetail||{id:state.personId};rememberPersonLabel(person);state.person=String(state.personId);$('#person-filter').value=state.person;await setView('timeline');}));
$('#ignore-person').addEventListener('click',action(async()=>{const ignored=!(state.personDetail&&state.personDetail.ignored);await api('/api/people/'+state.personId+'/ignore',{method:'POST',body:JSON.stringify({ignored})});toast(ignored?'这组已标为路人，不再参与识别':'已恢复为待识别');$('#person-dialog').close();await refreshStatus();if(ignored)await setView('passersby');else await setView('people');}));
$('#people-merge-toggle')?.addEventListener('click',()=>setPeopleMerging(!state.peopleMerging));
$('#clear-people-selection').addEventListener('click',()=>setPeopleMerging(false));
$('#merge-selected-people').addEventListener('click',async()=>{const button=$('#merge-selected-people');if(button?.disabled)return;const ids=[...state.peopleSelected].map(Number);if(ids.length<2){showNotice('请至少选择两组同一人',true);return;}if(ids.length>100){showNotice('一次最多选择 100 个人物',true);return;}button.disabled=true;const done=[];const pending=ids.slice();let backendError=null;let target=null;try{target=await resolveMergeTarget(ids);if(!ids.includes(Number(target.id)))throw new Error('合并目标必须属于当前选择');for(const id of ids.filter(x=>x!==Number(target.id))){await api('/api/people/'+id+'/merge',{method:'POST',body:JSON.stringify({target_id:target.id})});done.push(id);pending.splice(pending.indexOf(id),1);}}catch(err){backendError=err;}if(backendError){const leftover=pending.filter(id=>!done.includes(id));const msg=done.length?('已合并 '+done.length+' 组，未完成：'+leftover.join('、')+'。'+backendError.message):backendError.message;showNotice(msg,true);toast(msg,true);}else if(done.length){toast('已合并到 '+personLabel(target));}if(done.length){try{for(const id of done){state.peopleSelected.delete(id);removePersonFromLocalState(id);}setPeopleMerging(false);await loadPeople();}catch(uiError){showNotice('人物已合并，界面刷新未完成，请刷新页面',true);}}button.disabled=false;refreshStatus().catch(()=>{});});
$('#backup-button').addEventListener('click',action(async()=>{const data=await api('/api/backup',{method:'POST'});toast('备份已保存：'+data.path);}));
document.addEventListener('click',action(async e=>{const photo=e.target.closest('[data-photo]');if(photo){const id=Number(photo.dataset.photo);if(state.selecting){if(state.selected.has(id))state.selected.delete(id);else if(state.selected.size<1000)state.selected.add(id);else toast('一次最多选择 1000 张照片');streamSelection();}else await openPhoto(id,currentBrowseContext());}const ignorePersonCard=e.target.closest('[data-ignore-person]');if(ignorePersonCard){e.preventDefault();e.stopPropagation();const id=Number(ignorePersonCard.dataset.ignorePerson);ignorePersonCard.disabled=true;try{await api('/api/people/'+id+'/ignore',{method:'POST',body:JSON.stringify({ignored:true})});state.peopleSelected.delete(id);updatePeopleMerge();const card=ignorePersonCard.closest('.person-card');const next=card&&card.nextElementSibling;if(card){card.classList.add('removing');await new Promise(r=>setTimeout(r,180));card.remove();}if(state.peopleStream&&state.peopleStream.people){state.peopleStream.people.items=(state.peopleStream.people.items||[]).filter(p=>p.id!==id);if(typeof state.peopleStream.people.total==='number'&&state.peopleStream.people.total>0)state.peopleStream.people.total-=1;const total=$('#people-total');if(total)total.textContent=fmt(state.peopleStream.people.total)+' 个分组';}if(next)next.scrollIntoView({block:'nearest',behavior:'smooth'});refreshStatus().catch(()=>{});}catch(err){toast(err.message||'没能标为路人',true);}finally{ignorePersonCard.disabled=false;}return;}const peopleCard=e.target.closest('#people-grid .person-card');if(peopleCard){if(e.target.closest('[data-quick-name],[data-ignore-person]'))return;const hitCheck=e.target.closest('.person-check,[data-select-person]');if(hitCheck||isPeopleSelecting()){e.preventDefault();togglePersonSelection(peopleCard.dataset.person);return;}await openPerson(Number(peopleCard.dataset.person));return;}const openPersonBtn=e.target.closest('[data-open-person]');if(openPersonBtn){await openPerson(Number(openPersonBtn.dataset.openPerson));return;}const person=e.target.closest('[data-person]');if(person)await openPerson(Number(person.dataset.person));const facePhoto=e.target.closest('[data-face-photo]');if(facePhoto){$('#person-dialog').close();await openPhoto(Number(facePhoto.dataset.facePhoto),{q:'',filter:'all',person:String(state.personId),directory:'',sort:'date_desc'});}const split=e.target.closest('[data-split]');if(split){await api('/api/faces/'+split.dataset.split+'/split',{method:'POST'});toast('已移到新的待命名分组');await openPerson(state.personId);await refreshStatus();if(state.view==='people')await loadPeople();if(state.view==='passersby')await loadPassersby();}const year=e.target.closest('[data-year]');if(year){state.sort='date_desc';$('#sort-order').value=state.sort;await setView('year:'+year.dataset.year);return;}const group=e.target.closest('[data-group]');if(group){state.sort='date_desc';$('#sort-order').value=state.sort;await setView('group:'+group.dataset.group);return;}const place=e.target.closest('[data-place]');if(place){await setView('place:'+place.dataset.place);return;}const folder=e.target.closest('[data-folder]');if(folder)await openFolder(folder.dataset.folder);if(e.target.id==='banner-details')await setView('scan');if(e.target.id==='pause-scan'){await api('/api/scan/pause',{method:'POST'});await refreshStatus();}if(e.target.id==='resume-scan'){await api('/api/scan/'+state.status.job.id+'/resume',{method:'POST'});await refreshStatus();}}));
document.addEventListener('keydown',e=>{if((e.key==='Enter'||e.key===' ')&&e.target.matches('[data-photo], [data-face-photo]')){e.preventDefault();e.target.click();}});


function bytes(n){return n>=1024*1024?(n/1024/1024).toFixed(1)+' MB':Math.round(n/1024)+' KB';}
async function loadExclusionRules(){
 const data=await api('/api/exclusions');
 $('#excluded-rules').innerHTML=data.roots.length?data.roots.map(r=>`<div class="exclusion-rule"><span>${esc(r.path)}<small>含全部子目录 · ${esc(r.created_at)}</small></span><button class="secondary small" data-restore-root="${r.id}">恢复目录</button></div>`).join(''):'<p class="footnote">还没有目录排除规则。下方显示已排除的档案；缩略图已释放。</p>';
}
async function exclusionDialog(ids=null,path=null){
 if(ids){state.exclusion={ids,excluded:true};$('#exclude-impact').textContent=`将排除 ${ids.length} 份图片档案，以及这些图片的全部重复副本。`;}
 else {const impact=await api('/api/exclusions/preview?path='+encodeURIComponent(path));state.exclusion={path:impact.path};$('#exclude-impact').textContent=`目录：${impact.path}\n已登记 ${fmt(impact.files)} 个文件，对应 ${fmt(impact.assets)} 份档案。\n${impact.scope}`;}
 showDialog('#exclude-dialog');
}
async function restoreAssets(ids){
 const result=await api('/api/exclusions/assets',{method:'POST',body:JSON.stringify({ids,excluded:false})});
 toast(result.message);state.thumbRevision++;state.selected.clear();updateBatch();await refreshStatus();await loadPhotos();
 if($('#detail-dialog').open)await openPhoto(state.detail.id);
}
$('#batch-exclude').addEventListener('click',action(()=>exclusionDialog([...state.selected])));
$('#batch-restore').addEventListener('click',action(()=>restoreAssets([...state.selected])));
$('#exclude-photo').addEventListener('click',action(()=>exclusionDialog([state.detail.id])));
$('#restore-photo').addEventListener('click',action(()=>restoreAssets([state.detail.id])));
$('#choose-exclude-folder').addEventListener('click',action(()=>{state.folderTarget='exclude';return openFolder();}));
$('#preview-root-exclusion').addEventListener('click',action(()=>exclusionDialog(null,$('#exclude-root').value)));
$('#exclude-current-directory').addEventListener('click',action(()=>{const path=$('#directory-filter').value.trim();if(!path)throw new Error('请先填写要筛选的目录');return exclusionDialog(null,path);}));
$('#apply-directory').addEventListener('click',action(async()=>{state.directory=$('#directory-filter').value.trim();state.sort=state.directory?'name_asc':'date_desc';$('#sort-order').value=state.sort;state.offset=0;await loadPhotos();}));
$('#clear-directory').addEventListener('click',action(async()=>{state.directory='';state.sort='date_desc';$('#sort-order').value=state.sort;$('#directory-filter').value='';state.offset=0;await loadPhotos();}));
$('#with-faces').addEventListener('change',()=>{$('#scan-workers').disabled=$('#with-faces').checked;$('#worker-hint').textContent=$('#with-faces').checked?'本轮包含人脸识别，使用 1 路处理，保持人物分组顺序稳定。':'元数据工具常驻运行；并发数越高，CPU 与磁盘负载越高。';});
$('#confirm-exclusion').addEventListener('click',action(async()=>{
 const button=$('#confirm-exclusion');button.disabled=true;
 try {const body={...state.exclusion};if(body.ids)body.reason=$('#exclude-reason').value||'手工排除';
 const result=await api(body.ids?'/api/exclusions/assets':'/api/exclusions/roots',{method:'POST',body:JSON.stringify(body)});
 $('#exclude-dialog').close();if($('#detail-dialog').open)$('#detail-dialog').close();
 state.thumbRevision++;state.selected.clear();updateBatch();toast('已排除，释放缓存 '+bytes(result.released_bytes)+'；原文件保留');
 await refreshStatus();await loadPhotos();await loadExclusionRules();
 } finally {button.disabled=false;}
}));
document.addEventListener('click',action(async e=>{const button=e.target.closest('[data-restore-root]');if(!button)return;const result=await api('/api/exclusions/roots/'+button.dataset.restoreRoot,{method:'DELETE'});state.thumbRevision++;toast(result.message);await loadExclusionRules();await refreshStatus();await loadPhotos();}));

window.__ourTimeApp={state,api,peopleQuery,personLabel,prettyPlace,showDialog,openFolder,exclusionDialog,restoreAssets,refreshStatus,setView,openAddPhotos};

setInterval(()=>refreshStatus().catch(()=>{if(!state.connectionNotice){toast('本机后台暂时没有响应，请检查是否仍在运行',true);state.connectionNotice=true;}}),8000);

document.querySelectorAll("dialog").forEach(el=>{if(el.open)el.close();});
renderPhoto.ticket=0;
