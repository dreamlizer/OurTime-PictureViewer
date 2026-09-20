/* Homepage discovery. Does not replace timeline, organize, or viewer contracts. */
(function(global){
  'use strict';
  const PREF_KEY='ourtime.home.prefs.v1';
  const HIDE_DAYS=30;
  const RECENT_DAYS=7;
  const TABS=[
    {id:'all', label:'综合推荐'},
    {id:'on_this_day', label:'往年今日'},
    {id:'place_revisit', label:'重访一个地方'},
    {id:'people_years', label:'人物这些年'}
  ];
  const KIND_LABEL={on_this_day:'往年今日', on_this_month:'那年这个月', place_span:'重访一个地方', person_years:'人物这些年', person_fragment:'人物片段'};
  let generation=0, payload=null, lastRevision='';
  const cache=new Map();
  let homeAbort=null, waitTimer=null;

  const $=s=>document.querySelector(s);
  const $$=s=>document.querySelectorAll(s);
  const esc=global.esc || (v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])));
  const thumb=id=>'/api/thumb/'+id+'?w=960';

  function prefs(){
    try{return JSON.parse(localStorage.getItem(PREF_KEY)||'{}');}catch{return {};}
  }
  function savePrefs(next){
    try{localStorage.setItem(PREF_KEY, JSON.stringify(next));}catch{/* private mode still browses */}
  }
  function readPrefs(){
    const data=prefs();
    const now=Date.now();
    const hidden=(data.hidden||[]).filter(item=>item.until>now);
    const recent=(data.recent||[]).filter(item=>now-item.at<RECENT_DAYS*86400000);
    if(hidden.length!==(data.hidden||[]).length || recent.length!==(data.recent||[]).length){
      savePrefs({version:1, hidden, recent, cursor:data.cursor||{}});
    }
    return {version:1, hidden, recent, cursor:data.cursor||{}};
  }
  function isHidden(id){return readPrefs().hidden.some(item=>item.group_id===id);}
  function isRecent(id){return readPrefs().recent.some(item=>item.group_id===id);}
  function hideGroup(id){
    const data=readPrefs();
    data.hidden=data.hidden.filter(item=>item.group_id!==id);
    data.hidden.push({group_id:id, until:Date.now()+HIDE_DAYS*86400000});
    savePrefs(data);
  }
  function restoreHidden(){
    const data=readPrefs();
    data.hidden=[];
    savePrefs(data);
  }
  function rememberOpen(id){
    const data=readPrefs();
    data.recent=data.recent.filter(item=>item.group_id!==id);
    data.recent.push({group_id:id, at:Date.now()});
    savePrefs(data);
  }
  function ranked(list){
    const visible=(list||[]).filter(card=>!isHidden(card.group_id));
    const fresh=visible.filter(card=>!isRecent(card.group_id));
    const old=visible.filter(card=>isRecent(card.group_id));
    return fresh.concat(old);
  }
  function nextCursor(category){
    const data=readPrefs();
    return Number(data.cursor[category]||0);
  }
  function setCursor(category, value){
    const data=readPrefs();
    data.cursor[category]=value;
    savePrefs(data);
  }
  function pickCard(category){
    const list=ranked((payload&&payload.candidates&&payload.candidates[category])||[]);
    if(!list.length) return null;
    const index=nextCursor(category)%list.length;
    return list[index];
  }
  function kindLabel(card){return KIND_LABEL[card.kind]||'推荐';}
  function coversOf(card){
    if(Array.isArray(card.covers)&&card.covers.length){
      return card.covers.map(item=>({
        asset_id:item.asset_id,
        year:item.year||'',
        target_face_id:item.target_face_id||null,
        object_position:item.object_position||'50% 28%',
        preview:item.preview||'thumb'
      }));
    }
    const years=card.years||[];
    return (card.cover_asset_ids||[]).map((id,i)=>({
      asset_id:id, year:years[i]||'', target_face_id:null, object_position:'50% 28%', preview:'thumb'
    }));
  }
  function coverSrc(cover){
    if(cover.preview==='face' && cover.target_face_id) return '/api/face/'+cover.target_face_id;
    return thumb(cover.asset_id);
  }
  function photoTag(id, extra, toolsHtml){
    const cls=extra||'home-card-photo';
    const img=id?'<img src="'+esc(thumb(id))+'" alt="">':'';
    return '<div class="home-media '+cls+'">'+img+(toolsHtml||'')+'</div>';
  }
  function tools(card, slot, options){
    const withSwap=!(options && options.swap===false);
    return '<div class="home-card-tools">'
      +(withSwap?'<button type="button" data-home-swap="'+esc(slot)+'">换一组</button>':'')
      +'<button type="button" data-home-hide="'+esc(card.group_id)+'">不推荐</button>'
      +'</div>';
  }
  function heroCopy(card){
    const n=Number(card.photo_count||0);
    if(card.kind==='on_this_day') return '这一天留下了 '+n+' 张照片';
    if(card.kind==='on_this_month') return '这个月留下了 '+n+' 张照片';
    return card.subtitle||'';
  }
  function hero(card, slot){
    const cover=coversOf(card)[0];
    const coverId=cover?cover.asset_id:(card.cover_asset_ids||[])[0];
    const place=card.place_label?(' · '+card.place_label):'';
    return '<article class="home-hero" data-home-slot="'+esc(slot||'on_this_day')+'" data-home-open="'+esc(card.group_id)+'">'
      +photoTag(coverId,'home-hero-photo',tools(card, slot||'on_this_day'))
      +'<div class="home-hero-text"><p class="home-kicker">'+esc(kindLabel(card))+'</p>'
      +'<h3>'+esc(card.title)+esc(place)+'</h3>'
      +'<p class="home-copy">'+esc(heroCopy(card))+'</p>'
      +'<button type="button" class="primary" data-home-open="'+esc(card.group_id)+'">'+esc(card.cta||'往年今日')+'</button>'
      +'</div></article>';
  }
  function placeCard(card, options){
    const title=card.title||'';
    const full=card.title_full||card.place||title;
    return '<article class="home-card" data-home-slot="place_revisit" data-home-open="'+esc(card.group_id)+'">'
      +photoTag((coversOf(card)[0]||{}).asset_id||(card.cover_asset_ids||[])[0],'home-card-photo',tools(card,'place_revisit', options))
      +'<p class="home-kicker">重访一个地方</p>'
      +'<h3 title="'+esc(full)+'">'+esc(title)+'</h3>'
      +'<p class="home-copy">'+esc(heroCopy(card))+'</p>'
      +'</article>';
  }
  function peopleCard(card, options){
    const covers=coversOf(card).slice(0,4);
    let mosaic='';
    if(covers.length>1){
      mosaic='<div class="home-media home-years-wrap"><div class="home-years" data-count="'+covers.length+'" style="--year-cols:'+covers.length+'">'
        +covers.map(item=>{
          const year=item.year?('<b>'+esc(item.year)+'</b>'):'';
          return '<div class="home-year-photo"><img src="'+esc(coverSrc(item))+'" alt="" style="object-position:'+esc(item.object_position)+'" data-fallback="'+esc(thumb(item.asset_id))+'">'+year+'</div>';
        }).join('')
        +'</div>'+tools(card,'people_years', options)+'</div>';
    }else mosaic=photoTag(covers[0]&&covers[0].asset_id,'home-card-photo',tools(card,'people_years', options));
    return '<article class="home-card" data-home-slot="people_years" data-home-open="'+esc(card.group_id)+'">'
      +mosaic
      +'<p class="home-kicker">'+esc(kindLabel(card))+'</p>'
      +'<h3>'+esc(card.title)+'</h3>'
      +'<p class="home-copy">'+esc(card.subtitle||'')+'</p>'
      +'</article>';
  }
  function gridCard(card){
    const listTools={swap:false};
    if(card.kind==='place_span') return placeCard(card, listTools);
    if(String(card.kind).startsWith('person')) return peopleCard(card, listTools);
    return '<article class="home-card" data-home-open="'+esc(card.group_id)+'">'
      +photoTag((coversOf(card)[0]||{}).asset_id,'home-card-photo',tools(card, state.homeCategory||'on_this_day', listTools))
      +'<p class="home-kicker">'+esc(kindLabel(card))+'</p>'
      +'<h3>'+esc(card.title)+'</h3>'
      +'<p class="home-copy">'+esc(heroCopy(card))+'</p>'
      +'</article>';
  }
  function emptyHtml(kind){
    if(kind==='loading') return skeletonHtml();
    if(kind==='updating') return '<div class="home-status home-status-plain"><h3>更新中</h3></div>';
    const messages={
      empty:['暂时没有合适的推荐','照片少、日期不准确或人物都还没命名时，会先空着这一页。'],
      error:['推荐暂时不可用','可以稍后再试，或者去全部照片里继续看。']
    };
    const pair=messages[kind]||messages.error;
    return '<div class="home-status"><h3>'+pair[0]+'</h3><p>'+pair[1]+'</p><div class="home-actions">'
      +(kind==='error'?'<button type="button" class="secondary" id="home-retry">重试</button>':'')
      +'<button type="button" class="primary" data-view-all>全部照片</button></div></div>';
  }

  function skeletonHtml(){
    return '<div class="home-skeleton" aria-hidden="true"><div class="home-hero"><div class="home-media home-hero-photo"></div><div class="home-hero-text"><p class="home-kicker">&nbsp;</p><h3>&nbsp;</h3><p class="home-copy">&nbsp;</p></div></div><div class="home-pair"><article class="home-card"><div class="home-media home-card-photo"></div></article><article class="home-card"><div class="home-media home-card-photo"></div></article></div></div>';
  }
  function syncTabs(){
    $$('#home-tabs button').forEach(btn=>btn.setAttribute('aria-selected', btn.dataset.homeTab===(state.homeCategory||'all')?'true':'false'));
  }
  function setBoardBusy(on){
    const board=$('#home-board');
    if(!board) return;
    if(waitTimer){clearTimeout(waitTimer); waitTimer=null;}
    if(!on){ board.classList.remove('is-busy','is-wait'); board.removeAttribute('aria-busy'); return; }
    board.classList.add('is-busy');
    board.setAttribute('aria-busy','true');
    waitTimer=setTimeout(()=>{ if(board.classList.contains('is-busy')) board.classList.add('is-wait'); }, 220);
  }
  function rememberPayload(data){
    if(data.revision && lastRevision && data.revision!==lastRevision) cache.clear();
    if(data.revision) lastRevision=data.revision;
    cache.set(data.category, data);
  }
  function cachedPayload(category){
    const hit=cache.get(category);
    if(hit && (!lastRevision || !hit.revision || hit.revision===lastRevision)) return hit;
    const all=cache.get('all');
    if(category!=='all' && all && (!lastRevision || !all.revision || all.revision===lastRevision) && all.candidates && all.candidates[category]){
      const list=all.candidates[category];
      return Object.assign({}, all, {category:category, items:list.slice(0,6), next_cursor:list.length>6?'o:6':null});
    }
    return null;
  }
  function slotHtml(card, slot){
    if(slot==='people_years' || String(card.kind||'').startsWith('person')) return peopleCard(card);
    if(slot==='place_revisit' || card.kind==='place_span') return placeCard(card);
    return hero(card, slot||'on_this_day');
  }
  function replaceSlot(slot){
    const card=pickCard(slot);
    const node=document.querySelector('[data-home-slot="'+slot+'"]');
    if(!card){
      if(node) node.remove();
      const pair=document.querySelector('.home-pair');
      if(pair){
        const n=pair.querySelectorAll('[data-home-slot]').length;
        pair.classList.toggle('is-single', n===1);
        if(!n) pair.remove();
      }
      return;
    }
    const wrap=document.createElement('div');
    wrap.innerHTML=slotHtml(card, slot);
    const next=wrap.firstElementChild;
    if(node) node.replaceWith(next);
    else render();
  }
  function appendGrid(items){
    const grid=document.querySelector('#home-board .home-grid');
    if(!grid){ render(); return; }
    grid.insertAdjacentHTML('beforeend', items.map(gridCard).join(''));
    const more=$('#home-more');
    if(!payload.next_cursor && more){
      const host=more.closest('.home-more');
      if(host) host.remove();
    }
  }

  function renderDate(info){
    const el=$('#home-date');
    if(!el||!info) return;
    const day=info.as_of_date||'';
    const parts=day.split('-');
    const month=parts[1]?Number(parts[1])+'月':'';
    const d=parts[2]?Number(parts[2])+'日':'';
    const year=parts[0]||'';
    const weekday=info.weekday||'';
    const second=esc(year?(year+'年'):'')+(weekday?(' · '+esc(weekday)):'');
    el.innerHTML='<strong>'+esc(month+d)+'</strong><span>'+second+'</span>';
  }
  function render(){
    const board=$('#home-board');
    if(!board||!payload) return;
    const category=state.homeCategory||'all';
    if(category==='all'){
      const day=pickCard('on_this_day');
      const place=pickCard('place_revisit');
      const person=pickCard('people_years');
      if(!day && !place && !person){board.innerHTML=emptyHtml('empty');return;}
      let html='', rest=[];
      if(day){ html+=hero(day,'on_this_day'); rest=[place,person].filter(Boolean); }
      else if(place){ html+=hero(place,'place_revisit'); rest=[person].filter(Boolean); }
      else { html+=hero(person,'people_years'); }
      if(rest.length){
        html+='<div class="home-pair'+(rest.length===1?' is-single':'')+'">';
        html+=rest.map(card=>String(card.kind||'').startsWith('person')?peopleCard(card):placeCard(card)).join('');
        html+='</div>';
      }
      if(readPrefs().hidden.length) html+='<p class="home-more"><button type="button" class="text-button" id="home-restore-hidden">恢复暂时隐藏的推荐</button></p>';
      board.innerHTML=html;
      return;
    }
    const items=payload.items||[];
    if(!items.length){board.innerHTML=emptyHtml('empty');return;}
    board.innerHTML='<div class="home-grid">'+items.map(gridCard).join('')+'</div>'
      +(payload.next_cursor?'<p class="home-more"><button type="button" class="secondary" id="home-more">查看更多</button></p>':'')
      +(readPrefs().hidden.length?'<p class="home-more"><button type="button" class="text-button" id="home-restore-hidden">恢复暂时隐藏的推荐</button></p>':'');
  }
  async function loadHomeDiscovery(options={}){
    const category=options.category||state.homeCategory||'all';
    const board=$('#home-board');
    if(!options.cursor && !options.force && !options.append){
      const hit=cachedPayload(category);
      if(hit){
        state.homeCategory=category;
        payload=hit;
        syncTabs(); renderDate(hit); render();
        revealCatalogButton(!!hit.catalog);
        return;
      }
    }
    if(homeAbort) homeAbort.abort();
    homeAbort=new AbortController();
    const ticket=++generation;
    const empty=!board || !board.querySelector('.home-hero, .home-card, .home-grid');
    if(empty && board && !options.append) board.innerHTML=skeletonHtml();
    else if(!options.append) setBoardBusy(true);
    try{
      const query=new URLSearchParams({category:category});
      if(options.cursor) query.set('cursor', options.cursor);
      const data=await api('/api/home/recommendations?'+query.toString(), {signal:options.signal||homeAbort.signal});
      if(ticket!==generation) return;
      rememberPayload(data);
      if(options.append && payload && payload.category===data.category){
        const seen=new Set((payload.items||[]).map(card=>card.group_id));
        const extra=(data.items||[]).filter(card=>!seen.has(card.group_id));
        payload=Object.assign({}, data, {items:[...(payload.items||[]), ...extra]});
        rememberPayload(payload);
        state.homeCategory=category;
        syncTabs(); setBoardBusy(false); renderDate(payload); appendGrid(extra);
      }else{
        payload=data;
        state.homeCategory=category;
        syncTabs(); setBoardBusy(false); renderDate(payload); render();
      }
      revealCatalogButton(!!(payload && payload.catalog));
    }catch(error){
      if(ticket!==generation || (error && error.name==='AbortError')) return;
      setBoardBusy(false);
      if(board && !board.querySelector('.home-hero, .home-card, .home-grid')) board.innerHTML=emptyHtml('error');
    }
  }
  function revealCatalogButton(on){
    const btn=$('#home-refresh-catalog');
    if(btn) btn.hidden=!on;
  }
  async function refreshCatalog(){
    const btn=$('#home-refresh-catalog');
    if(btn){ btn.disabled=true; btn.textContent='更新中'; }
    setBoardBusy(true);
    try{
      await api('/api/home/catalog/rebuild', {method:'POST', timeoutMs:180000});
      cache.clear();
      lastRevision='';
      await loadHomeDiscovery({force:true});
    }catch(error){
      if(error && error.name==='AbortError') return;
      if(typeof toast==='function') toast((error && error.message) || '推荐没有更新完', true);
    }finally{
      if(btn){ btn.disabled=false; btn.textContent='更新推荐'; }
      setBoardBusy(false);
    }
  }
  async function openGroup(groupId){
    rememberOpen(groupId);
    const opened=await api('/api/home/groups/open', {method:'POST', body:JSON.stringify({group_id:groupId})});
    state.homeSnapshotId=opened.snapshot_id;
    state.homeGroupId=opened.group_id;
    state.homeGroupTitle=opened.title;
    await setView('home-group');
  }
  function bind(){
    const tabs=$('#home-tabs');
    if(tabs && !tabs.dataset.ready){
      tabs.dataset.ready='1';
      tabs.innerHTML=TABS.map(tab=>'<button type="button" data-home-tab="'+tab.id+'" aria-selected="'+(tab.id==='all'?'true':'false')+'">'+tab.label+'</button>').join('');
      tabs.addEventListener('click', async e=>{
        const tab=e.target.closest('[data-home-tab]');
        if(!tab) return;
        await loadHomeDiscovery({category:tab.dataset.homeTab});
      });
    }
    if(!document.body.dataset.homeImgFallback){
      document.body.dataset.homeImgFallback='1';
      document.addEventListener('error', e=>{
        const img=e.target;
        if(!img || img.tagName!=='IMG' || !img.dataset || !img.dataset.fallback) return;
        if(img.src && img.src.indexOf(img.dataset.fallback)>=0) return;
        img.src=img.dataset.fallback;
      }, true);
    }
    if(!document.body.dataset.homeClicks){
      document.body.dataset.homeClicks='1';
      document.addEventListener('click', async e=>{
        const open=e.target.closest('[data-home-open]');
        const swap=e.target.closest('[data-home-swap]');
        const hide=e.target.closest('[data-home-hide]');
        if(swap){ e.preventDefault(); e.stopPropagation(); const cat=swap.dataset.homeSwap; setCursor(cat, nextCursor(cat)+1); replaceSlot(cat); return; }
        if(hide){ e.preventDefault(); e.stopPropagation(); const slotEl=hide.closest('[data-home-slot]'); const slot=slotEl&&slotEl.dataset.homeSlot; hideGroup(hide.dataset.homeHide); if(slot && (state.homeCategory||'all')==='all') replaceSlot(slot); else render(); toast('已暂时隐藏，30 天内不再出现'); return; }
        if(e.target.closest('#home-restore-hidden')){ restoreHidden(); render(); return; }
        if(e.target.closest('#home-retry')){ await loadHomeDiscovery({category:state.homeCategory}); return; }
        if(e.target.closest('#home-refresh-catalog')){ e.preventDefault(); await refreshCatalog(); return; }
        if(e.target.closest('#home-more')){ await loadHomeDiscovery({category:state.homeCategory, cursor:payload&&payload.next_cursor, append:true}); return; }
        if(e.target.closest('[data-view-all]')){ await setView('timeline'); return; }
        if(open){ e.preventDefault(); await openGroup(open.dataset.homeOpen); }
      });
    }
    const back=$('#home-group-back');
    if(back && !back.dataset.homeReady){
      back.dataset.homeReady='1';
      back.addEventListener('click', async ()=>setView('home'));
    }
  }

  bind();
  global.loadHomeDiscovery=loadHomeDiscovery;
  global.OurTimeHome={load:loadHomeDiscovery, openGroup};
})(window);
