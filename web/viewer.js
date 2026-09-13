// Browsing order is an immutable list of IDs from the current collection.
// UI redesign: lightweight viewer toolbar, persistent face-label preferences,
// adaptive photo metadata strip. 2026-09-11.

const VIEWER_PREFS_KEY='ourtime.viewer.preferences.v2';
const SIGNATURE_STYLES=[
  {key:'original',label:'原版'},
  {key:'classic',label:'双端参数'},
  {key:'gallery',label:'作品底签'},
  {key:'handwritten',label:'手写题签'},
  {key:'tone',label:'随照片取色'}
];
// A browsing-session choice: a fresh page always starts with the original.
let signatureStyleIndex=0;
let signaturePaletteSrc='';
let signatureLayouts=null;
const FACE_LABEL_IMAGES={
  ivory:{
    s:'/api/face-label-bg/1.png',
    m:'/api/face-label-bg/2.png',
    l:'/api/face-label-bg/3.png'
  },
  tea:{
    s:'/api/face-label-bg/4.png',
    m:'/api/face-label-bg/5.png',
    l:'/api/face-label-bg/6.png'
  }
};
const faceLabelThemeReadiness=new Map();
const TEA_FACE_FONT_OPTIONS=[
  {value:'ma-shan-zheng',label:'Ma Shan Zheng（默认）'},
  {value:'long-cang',label:'Long Cang · 龙藏体'},
  {value:'liu-jian-mao-cao',label:'Liu Jian Mao Cao · 毛草'}
];
const TEA_FACE_FONT_KEYS=new Set(TEA_FACE_FONT_OPTIONS.map(option=>option.value));
const ACCENT_FACE_FONT_OPTIONS=[
  {value:'zcool-xiaowei',label:'ZCOOL XiaoWei（默认）'},
  {value:'noto-serif-sc',label:'Noto Serif SC · 思源宋体'},
  {value:'zhi-mang-xing',label:'Zhi Mang Xing · 志莽行书'}
];
const ACCENT_FACE_FONT_KEYS=new Set(ACCENT_FACE_FONT_OPTIONS.map(option=>option.value));
const GENERAL_FACE_FONT_OPTIONS=[
  {value:'sans',label:'黑体 / 无衬线'},
  {value:'serif',label:'宋体 / 衬线'},
  {value:'kai',label:'楷体'},
  {value:'fangsong',label:'仿宋'},
  {value:'other',label:'其他…'}
];
const FACE_STYLE_PRESETS={
  classic:{
    theme:'classic',
    fontSize:13,
    fontFamily:'kai',
    textColor:'#f6f1e6',
    backgroundColor:'#141812',
    backgroundOpacity:.5,
    radius:10,
    paddingX:5,
    paddingY:5,
    shadow:true
  },
  ivory:{
    theme:'ivory',
    fontSize:15,
    fontFamily:'kai',
    textColor:'#5a2f28',
    backgroundColor:'#ffffff',
    backgroundOpacity:.76,
    radius:0,
    paddingX:0,
    paddingY:0,
    shadow:false
  },
  tea:{
    theme:'tea',
    fontSize:16,
    fontFamily:'ma-shan-zheng',
    textColor:'#f7ead8',
    backgroundColor:'#7e624b',
    backgroundOpacity:.85,
    radius:0,
    paddingX:0,
    paddingY:0,
    shadow:false
  },
  accent:{
    theme:'accent',
    fontSize:15,
    fontFamily:'zcool-xiaowei',
    textColor:'#f4e4cb',
    backgroundColor:'#6f302c',
    backgroundOpacity:.84,
    radius:4,
    paddingX:7,
    paddingY:7,
    shadow:false
  }
};
const DEFAULT_FACE_STYLE={
  ...FACE_STYLE_PRESETS.classic
};
const FACE_UNNAMED_MARKERS=new Set(['plus','pulse','ring']);
const FACE_LABEL_POSITIONS=new Set(['auto','left','right','top','bottom']);
const FACE_LABEL_OVERLAP_LIMIT=.12;
const FACE_LABEL_FACE_OVERLAP_LIMIT=.25;
const LEGACY_CLASSIC_FACE_STYLE={
  theme:'classic',
  fontSize:13,
  fontFamily:'serif',
  textColor:'#f6f1e6',
  backgroundColor:'#141812',
  backgroundOpacity:.38,
  radius:4,
  paddingX:7,
  paddingY:5,
  shadow:true
};
function readViewerPrefs(){
  try{
    const raw=localStorage.getItem(VIEWER_PREFS_KEY);
    return raw?JSON.parse(raw):{};
  }catch(e){return {};}
}
const storedViewerPrefs=readViewerPrefs();
const LEGACY_FACE_THEMES=new Set(['ink','paper','cinnabar']);
function initialFaceStyle(){
  const stored=storedViewerPrefs.faceStyle;
  if(!stored)return {...DEFAULT_FACE_STYLE};
  if(stored.theme==='outline')return {...FACE_STYLE_PRESETS.tea};
  if(LEGACY_FACE_THEMES.has(stored.theme))return {...DEFAULT_FACE_STYLE};
  if(stored.theme==='classic'&&Object.entries(LEGACY_CLASSIC_FACE_STYLE).every(([key,value])=>stored[key]===value)){
    return {...DEFAULT_FACE_STYLE};
  }
  const theme=Object.prototype.hasOwnProperty.call(FACE_STYLE_PRESETS,stored.theme)?stored.theme:'classic';
  const style={...FACE_STYLE_PRESETS[theme],...stored,theme};
  if(theme==='tea'&&!TEA_FACE_FONT_KEYS.has(style.fontFamily)){
    style.fontFamily=FACE_STYLE_PRESETS.tea.fontFamily;
    delete style.customFontFamily;
  }
  if(theme==='accent'&&!ACCENT_FACE_FONT_KEYS.has(style.fontFamily)){
    style.fontFamily=FACE_STYLE_PRESETS.accent.fontFamily;
    delete style.customFontFamily;
  }
  return style;
}
const viewer={
  ids:[],index:0,target:0,offset:0,total:0,context:null,generation:0,busy:false,
  timer:null,playing:false,scale:1,fit:true,loader:null,drafts:new Map(),window:200,
  returnScroll:undefined,openedPhotoId:null,openedAbsolute:null,openedContext:null,openedWaterfallGeneration:null,exit:null,
  sequencePromise:null,loadingTimer:null,transitionDirection:0,closingTimer:null,
  faceNames:storedViewerPrefs.faceNames!==false,
  faceAlias:storedViewerPrefs.faceAlias===true,
  faceVertical:storedViewerPrefs.faceVertical!==false,
  faceLabelPosition:FACE_LABEL_POSITIONS.has(storedViewerPrefs.faceLabelPosition)?storedViewerPrefs.faceLabelPosition:'auto',
  faceUnnamedMarker:FACE_UNNAMED_MARKERS.has(storedViewerPrefs.faceUnnamedMarker)?storedViewerPrefs.faceUnnamedMarker:'plus',
  faceStyle:initialFaceStyle()
};
function saveViewerPrefs(){
  try{
    localStorage.setItem(VIEWER_PREFS_KEY,JSON.stringify({
      faceNames:viewer.faceNames!==false,
      faceAlias:viewer.faceAlias===true,
      faceVertical:viewer.faceVertical!==false,
      faceLabelPosition:viewer.faceLabelPosition,
      faceUnnamedMarker:viewer.faceUnnamedMarker,
      faceStyle:viewer.faceStyle,
      slideDelay:Number($('#slide-delay')?.value||storedViewerPrefs.slideDelay||5)
    }));
  }catch(e){}
}
function injectViewerOverrideCss(){
  if(document.querySelector('link[data-viewer-overrides]'))return;
  const link=document.createElement('link');
  link.rel='stylesheet';
  link.href='/viewer-overrides.css';
  link.dataset.viewerOverrides='true';
  document.head.appendChild(link);
}
injectViewerOverrideCss();

const iconSvg={
  minus:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12h12"/></svg>',
  plus:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 6v12M6 12h12"/></svg>',
  fit:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5"/></svg>',
  actual:'<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 8h8v8H8z"/></svg>',
  person:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="8" r="3.5"/><path d="M5 20v-1.5a7 7 0 0 1 14 0V20"/></svg>',
  alias:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 12 12 4h6l2 2v6l-8 8-8-8Z"/><circle cx="16.5" cy="7.5" r="1"/></svg>',
  horizontal:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 7h14M5 12h10M5 17h14"/></svg>',
  vertical:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5v14M12 5v10M17 5v14"/></svg>',
  style:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 19 10.5 5h3L19 19M7 14h10"/><circle cx="18.5" cy="6" r="2"/></svg>',
  info:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="9"/><path d="M12 10v7M12 7h.01"/></svg>',
  play:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m9 7 8 5-8 5V7Z"/></svg>',
  pause:'<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 7v10M15 7v10"/></svg>',
  locate:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="12" r="7"/><circle cx="12" cy="12" r="2"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3"/></svg>',
  organize:'<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="8" cy="8" r="2.5"/><circle cx="16" cy="9" r="2"/><path d="M3.5 18v-1a4.5 4.5 0 0 1 9 0v1M13 17.5a3.5 3.5 0 0 1 7 0V18"/></svg>'
};
function setToolIcon(selector,svg,label){
  const el=$(selector); if(!el)return;
  el.innerHTML=svg;
  el.title=label;
  el.setAttribute('aria-label',label);
}
function buildViewerToolbar(){
  const tools=document.querySelector('.viewer-tools');
  if(!tools||tools.dataset.redesigned)return;
  tools.dataset.redesigned='true';

  const zoomGroup=document.createElement('div');zoomGroup.className='viewer-tool-group viewer-tool-zoom';
  const peopleGroup=document.createElement('div');peopleGroup.className='viewer-tool-group viewer-tool-people';
  const actionGroup=document.createElement('div');actionGroup.className='viewer-tool-group viewer-tool-actions';

  ['#zoom-out','#zoom-fit','#zoom-level','#zoom-actual','#zoom-in'].forEach(sel=>{const el=$(sel);if(el)zoomGroup.appendChild(el);});
  ['#toggle-face-names','#toggle-face-alias','#toggle-face-dir'].forEach(sel=>{const el=$(sel);if(el)peopleGroup.appendChild(el);});

  const styleBtn=document.createElement('button');
  styleBtn.type='button';styleBtn.id='face-style-button';styleBtn.setAttribute('aria-expanded','false');
  styleBtn.innerHTML=iconSvg.style;styleBtn.title='人名标签样式';styleBtn.setAttribute('aria-label','人名标签样式');
  peopleGroup.appendChild(styleBtn);

  const manageBtn=document.createElement('button');
  manageBtn.type='button';manageBtn.id='photo-people-manage';manageBtn.setAttribute('aria-expanded','false');
  manageBtn.innerHTML=iconSvg.organize;manageBtn.title='整理本照片人物';manageBtn.setAttribute('aria-label','整理本照片人物');
  peopleGroup.appendChild(manageBtn);

  ['#viewer-info','#viewer-play','#slide-delay','#reveal-button'].forEach(sel=>{const el=$(sel);if(el)actionGroup.appendChild(el);});
  tools.replaceChildren(zoomGroup,peopleGroup,actionGroup);

  setToolIcon('#zoom-out',iconSvg.minus,'缩小');
  setToolIcon('#zoom-fit',iconSvg.fit,'适应窗口');
  setToolIcon('#zoom-actual',iconSvg.actual,'原尺寸 1:1');
  setToolIcon('#zoom-in',iconSvg.plus,'放大');
  setToolIcon('#toggle-face-names',iconSvg.person,'显示/隐藏人名');
  setToolIcon('#toggle-face-alias',iconSvg.alias,'显示/隐藏别名');
  setToolIcon('#viewer-info',iconSvg.info,'详细资料');
  setToolIcon('#viewer-play',iconSvg.play,'播放幻灯片');
  setToolIcon('#reveal-button',iconSvg.locate,'在资源管理器中定位');

  const delay=$('#slide-delay');
  if(delay){
    const saved=Number(storedViewerPrefs.slideDelay);
    if([3,5,10].includes(saved))delay.value=String(saved);
    delay.title='幻灯片切换间隔';
  }
  buildFaceStylePopover();
  buildPhotoPeoplePopover();
  buildFaceActionPopover();
}
function buildFaceStylePopover(){
  if($('#face-style-popover'))return;
  const body=document.querySelector('.viewer-body'); if(!body)return;
  const pop=document.createElement('section');
  pop.id='face-style-popover';pop.className='face-style-popover';pop.hidden=true;
  pop.setAttribute('aria-label','人名标签样式设置');
  pop.innerHTML=`
    <div class="face-style-head">
      <div><b>人名标签</b><span>照片中的姓名显示</span></div>
      <button type="button" id="face-style-close" aria-label="关闭">×</button>
    </div>
    <div class="face-style-preview">
      <span id="face-style-preview-label">示例姓名</span>
      <small id="face-style-preview-caption">默认 · 实际比例</small>
    </div>
    <div class="face-style-group">
      <div class="face-style-group-title">样式</div>
      <label class="face-style-field"><span>风格</span><select id="face-theme"><option value="classic">默认</option><option value="ivory">素笺</option><option value="tea">茶棕</option><option value="accent">暗朱</option></select></label>
      <label class="face-style-field face-custom-control face-range-field"><span>字号</span><output id="face-font-size-value"></output><input id="face-font-size" type="range" min="10" max="22" step="1"></label>
      <label class="face-style-field face-custom-control"><span>字体</span><select id="face-font-family"><option value="sans">黑体 / 无衬线</option><option value="serif">宋体 / 衬线</option><option value="kai">楷体</option><option value="fangsong">仿宋</option><option value="other">其他…</option></select></label>
    </div>
    <div class="face-style-group">
      <div class="face-style-group-title">布局</div>
      <label class="face-style-field"><span>标签位置</span><select id="face-label-position"><option value="auto">自动</option><option value="left">优先左侧</option><option value="right">优先右侧</option><option value="top">优先上方</option><option value="bottom">优先下方</option></select></label>
      <label class="face-style-field"><span>待命名标记</span><select id="face-unnamed-marker"><option value="plus">默认加号</option><option value="pulse">呼吸绿点</option><option value="ring">静态绿环</option></select></label>
    </div>
    <div class="face-style-group">
      <div class="face-style-group-title">外观</div>
      <div class="face-style-colors"><label><span>文字</span><input id="face-text-color" type="color" aria-label="文字颜色"></label><label><span>底色</span><input id="face-bg-color" type="color" aria-label="底色"></label></div>
      <label class="face-style-field face-custom-control face-range-field"><span>底色透明度</span><output id="face-bg-opacity-value"></output><input id="face-bg-opacity" type="range" min="0" max="90" step="1"></label>
      <label class="face-style-field face-custom-control"><span>圆角</span><select id="face-radius"><option value="0">直角</option><option value="4">微圆角</option><option value="10">圆角</option><option value="999">胶囊</option></select></label>
      <label class="face-style-field face-shadow-row"><input id="face-shadow" type="checkbox"><span>文字阴影</span><small>亮背景下更清楚</small></label>
    </div>
    <button type="button" id="face-style-reset" class="face-style-reset">恢复默认</button>`;
  body.appendChild(pop);

  const bind=(id,event,fn)=>{const el=$(id);if(el)el.addEventListener(event,fn);};
  bind('#face-style-close','click',()=>toggleFaceStylePopover(false));
  bind('#face-theme','change',e=>applyFacePreset(e.target.value));
  bind('#face-font-size','input',e=>updateFaceStyle({fontSize:Number(e.target.value)}));
  bind('#face-font-family','change',e=>{
    if(e.target.value==='other'){
      syncFaceFontSelect(e.target,viewer.faceStyle||DEFAULT_FACE_STYLE);
      openLocalFontDialog();
      return;
    }
    updateFaceStyle({fontFamily:e.target.value});
  });
  bind('#face-label-position','change',e=>{viewer.faceLabelPosition=FACE_LABEL_POSITIONS.has(e.target.value)?e.target.value:'auto';saveViewerPrefs();if(state.detail)renderFaceNames(state.detail);});
  bind('#face-unnamed-marker','change',e=>{
    viewer.faceUnnamedMarker=FACE_UNNAMED_MARKERS.has(e.target.value)?e.target.value:'plus';
    applyFaceStyle();
    saveViewerPrefs();
  });
  bind('#face-text-color','input',e=>updateFaceStyle({textColor:e.target.value}));
  bind('#face-bg-color','input',e=>updateFaceStyle({backgroundColor:e.target.value}));
  bind('#face-bg-opacity','input',e=>updateFaceStyle({backgroundOpacity:Number(e.target.value)/100}));
  bind('#face-radius','change',e=>updateFaceStyle({radius:Number(e.target.value)}));
  bind('#face-shadow','change',e=>updateFaceStyle({shadow:e.target.checked}));
  bind('#face-style-reset','click',()=>{viewer.faceStyle={...DEFAULT_FACE_STYLE};viewer.faceLabelPosition='auto';viewer.faceUnnamedMarker='plus';applyFaceStyle();saveViewerPrefs();if(state.detail)renderFaceNames(state.detail);});
  buildLocalFontDialog();
  applyFaceStyle();
}
function faceHasUsableName(face){
  const name=String(face?.name||'').trim();
  const alias=String(face?.alias||'').trim();
  return !face?.ignored&&(Boolean(alias)||(Boolean(name)&&!['待核对','命名','路人'].includes(name)));
}
function photoPeopleSummary(){
  const unique=new Map();
  for(const face of state.detail?.faces||[])if(!unique.has(Number(face.person_id)))unique.set(Number(face.person_id),face);
  const values=[...unique.values()];
  return {
    named:values.filter(faceHasUsableName).length,
    pending:values.filter(face=>!face.ignored&&!faceHasUsableName(face)).length,
    passerby:values.filter(face=>Boolean(face.ignored)).length
  };
}
function buildPhotoPeoplePopover(){
  if($('#photo-people-popover'))return;
  const shell=$('#detail-dialog .dialog-shell');if(!shell)return;
  const pop=document.createElement('section');
  pop.id='photo-people-popover';pop.className='people-manage-popover';pop.hidden=true;
  pop.setAttribute('aria-label','整理本照片人物');
  pop.innerHTML=`<div class="people-manage-head"><div><b>整理本照片人物</b><span>快速收起合影中的无关人物</span></div><button type="button" data-close-people-popover aria-label="关闭">×</button></div>
    <div class="people-manage-counts" aria-live="polite"><span><strong id="photo-named-count">0</strong> 已命名</span><span><strong id="photo-pending-count">0</strong> 待处理</span><span><strong id="photo-passerby-count">0</strong> 路人</span></div>
    <button type="button" id="photo-passersby-start" class="people-manage-primary">将剩余待命名者设为路人</button>
    <div id="photo-passersby-confirm" class="people-manage-confirm" hidden><p id="photo-passersby-copy"></p><small>同一人物组在其他照片中的人脸也会一起归入路人。</small><div><button type="button" id="photo-passersby-cancel">取消</button><button type="button" id="photo-passersby-confirm-button">确认处理</button></div></div>
    <button type="button" id="photo-passersby-undo" class="people-manage-undo" hidden>撤销上一步</button>`;
  shell.appendChild(pop);
  pop.querySelector('[data-close-people-popover]').addEventListener('click',()=>togglePhotoPeoplePopover(false));
  $('#photo-passersby-start').addEventListener('click',()=>{
    const summary=photoPeopleSummary();
    if(!summary.pending){toast('当前照片没有待命名人物');return;}
    $('#photo-passersby-copy').textContent=`保留 ${summary.named} 位已命名人物；只处理本照片中的 ${summary.pending} 位待命名者。`;
    $('#photo-passersby-confirm').hidden=false;
  });
  $('#photo-passersby-cancel').addEventListener('click',()=>{$('#photo-passersby-confirm').hidden=true;});
  $('#photo-passersby-confirm-button').addEventListener('click',action(async()=>{
    const assetId=Number(state.detail?.id);if(!assetId)throw new Error('当前照片未载入');
    const button=$('#photo-passersby-confirm-button');button.disabled=true;
    try{
      const result=await api(`/api/photos/${assetId}/passersby`,{method:'POST',body:JSON.stringify({ignored:true})});
      const ids=(result.person_ids||[]).map(Number);
      setCurrentPeopleIgnored(ids,true);
      viewer.lastPasserbyBatch=ids.length?{assetId,personIds:ids}:null;
      $('#photo-passersby-confirm').hidden=true;
      updatePhotoPeoplePopover();
      toast(ids.length?`已将 ${ids.length} 位待命名者设为路人`:'当前照片没有需要处理的人物');
      loadPeopleOptions().catch(()=>{});
    }finally{button.disabled=false;}
  }));
  $('#photo-passersby-undo').addEventListener('click',action(async()=>{
    const batch=viewer.lastPasserbyBatch;
    if(!batch||batch.assetId!==Number(state.detail?.id))return;
    const button=$('#photo-passersby-undo');button.disabled=true;
    try{
      const result=await api(`/api/photos/${batch.assetId}/passersby`,{method:'POST',body:JSON.stringify({ignored:false,person_ids:batch.personIds})});
      setCurrentPeopleIgnored((result.person_ids||[]).map(Number),false);
      viewer.lastPasserbyBatch=null;
      updatePhotoPeoplePopover();
      toast(`已恢复 ${Number(result.count)||0} 位待命名者`);
      loadPeopleOptions().catch(()=>{});
    }finally{button.disabled=false;}
  }));
}
function setCurrentPeopleIgnored(personIds,ignored){
  const ids=new Set(personIds.map(Number));
  for(const face of state.detail?.faces||[]){
    if(!ids.has(Number(face.person_id)))continue;
    face.ignored=ignored?1:0;
    if(!ignored&&!face.confirmed&&String(face.name||'').trim()==='路人'){face.name='';face.alias='';}
  }
  if(state.detail){renderFaceNames(state.detail);if(typeof renderOpenPhotoPeople==='function')renderOpenPhotoPeople();}
}
function updatePhotoPeoplePopover(){
  const pop=$('#photo-people-popover');if(!pop)return;
  const summary=photoPeopleSummary();
  $('#photo-named-count').textContent=String(summary.named);
  $('#photo-pending-count').textContent=String(summary.pending);
  $('#photo-passerby-count').textContent=String(summary.passerby);
  const start=$('#photo-passersby-start');
  if(start){start.disabled=!summary.pending;start.textContent=summary.pending?`将剩余 ${summary.pending} 位设为路人`:'没有待命名人物';}
  const batch=viewer.lastPasserbyBatch;
  $('#photo-passersby-undo').hidden=!(batch&&batch.assetId===Number(state.detail?.id)&&batch.personIds.length);
}
function togglePhotoPeoplePopover(force){
  const pop=$('#photo-people-popover'),btn=$('#photo-people-manage');if(!pop)return;
  const open=force==null?pop.hidden:!!force;
  if(open){toggleFaceStylePopover(false);closeFaceActionPopover();updatePhotoPeoplePopover();}
  pop.hidden=!open;if(btn)btn.setAttribute('aria-expanded',String(open));
  if(!open)$('#photo-passersby-confirm').hidden=true;
}
function buildFaceActionPopover(){
  if($('#face-action-popover'))return;
  const dialog=$('#detail-dialog');if(!dialog)return;
  const pop=document.createElement('section');
  pop.id='face-action-popover';pop.className='face-action-popover';pop.hidden=true;
  pop.innerHTML=`<div class="face-action-head"><span>这张照片中的</span><b id="face-action-name"></b></div><button type="button" id="face-action-split">认错了 · 从此人物移出</button><button type="button" id="face-action-ignore">这张是路人</button><small>只处理这张照片中的这张脸，不影响此人的其他照片。</small>`;
  dialog.appendChild(pop);
  $('#face-action-split').addEventListener('click',action(async()=>{
    const faceId=Number(pop.dataset.faceId),face=(state.detail?.faces||[]).find(item=>Number(item.id)===faceId);if(!face)return;
    const oldPerson=Number(face.person_id),oldName=String(face.name||'').trim()||'这个人物';
    const result=await api(`/api/faces/${faceId}/split`,{method:'POST'});
    Object.assign(face,{person_id:Number(result.person_id),name:'',alias:'',confirmed:0,ignored:0});
    closeFaceActionPopover();renderFaceNames(state.detail);renderOpenPhotoPeople();
    toast(`已从“${oldName}”移出；只影响这张照片`);
    if(typeof refreshPersonInLocalState==='function')refreshPersonInLocalState(oldPerson).catch(()=>{});
  }));
  $('#face-action-ignore').addEventListener('click',action(async()=>{
    const faceId=Number(pop.dataset.faceId),face=(state.detail?.faces||[]).find(item=>Number(item.id)===faceId);if(!face)return;
    const oldPerson=Number(face.person_id),result=await api(`/api/faces/${faceId}/ignore`,{method:'POST',body:JSON.stringify({ignored:true})});
    Object.assign(face,{person_id:Number(result.person_id),name:'路人',alias:'',confirmed:0,ignored:1});
    closeFaceActionPopover();renderFaceNames(state.detail);renderOpenPhotoPeople();toast('这张脸已标为路人');
    if(typeof refreshPersonInLocalState==='function')refreshPersonInLocalState(oldPerson).catch(()=>{});
  }));
}
function openFaceActionPopover(button,face){
  const pop=$('#face-action-popover');if(!pop)return;
  toggleFaceStylePopover(false);togglePhotoPeoplePopover(false);
  viewer.faceAction={faceId:Number(face.id),button};
  pop.dataset.faceId=String(face.id);$('#face-action-name').textContent=String(face.name||'').trim();pop.hidden=false;
  showFaceGuide(button);
  requestAnimationFrame(()=>{
    const rect=button.getBoundingClientRect(),width=pop.offsetWidth,height=pop.offsetHeight,pad=12;
    const sourceBox=faceBox(face),imageRect=$('#detail-img').getBoundingClientRect();
    const faceCenter=sourceBox?imageRect.left+(sourceBox.x1+sourceBox.width/2)/sourceBox.w*imageRect.width:rect.left;
    const preferLeft=faceCenter>=rect.left+rect.width/2;
    let left=preferLeft?rect.left-width-10:rect.right+10;
    if(left<pad||left+width>innerWidth-pad)left=preferLeft?rect.right+10:rect.left-width-10;
    pop.style.left=Math.round(Math.max(pad,Math.min(innerWidth-width-pad,left)))+'px';
    pop.style.top=Math.round(Math.max(pad,Math.min(innerHeight-height-pad,rect.top+rect.height/2-height/2)))+'px';
  });
}
function closeFaceActionPopover(){
  const pop=$('#face-action-popover');if(pop)pop.hidden=true;
  viewer.faceAction=null;hideFaceGuide(true);
}
function applyFacePreset(name){
  const valid=Object.prototype.hasOwnProperty.call(FACE_STYLE_PRESETS,name);
  const theme=valid?name:'classic';
  viewer.faceStyle={...FACE_STYLE_PRESETS[theme]};
  if(FACE_LABEL_IMAGES[theme])viewer.faceVertical=true;
  applyFaceStyle();
  saveViewerPrefs();
  if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function customFaceFontStack(family){
  const clean=String(family||'').replace(/[\u0000-\u001f\u007f]/g,'').trim().slice(0,200);
  return clean?`${JSON.stringify(clean)},"Microsoft YaHei UI","Microsoft YaHei",sans-serif`:faceFontStack('kai');
}
function faceFontStack(kind,customFamily){
  if(kind==='custom')return customFaceFontStack(customFamily||viewer.faceStyle?.customFontFamily);
  if(kind==='ma-shan-zheng')return '"Ma Shan Zheng","LXGW WenKai GB Screen","STKaiti","KaiTi",serif';
  if(kind==='long-cang')return '"Long Cang","LXGW WenKai GB Screen","STKaiti","KaiTi",serif';
  if(kind==='liu-jian-mao-cao')return '"Liu Jian Mao Cao","LXGW WenKai GB Screen","STKaiti","KaiTi",serif';
  if(kind==='zcool-xiaowei')return '"ZCOOL XiaoWei","Noto Serif SC","STSong","SimSun",serif';
  if(kind==='noto-serif-sc')return '"Noto Serif SC","Source Han Serif SC","STSong","SimSun",serif';
  if(kind==='zhi-mang-xing')return '"Zhi Mang Xing","LXGW WenKai GB Screen","STKaiti","KaiTi",serif';
  if(kind==='sans')return '"Microsoft YaHei UI","Microsoft YaHei","PingFang SC","Noto Sans CJK SC",sans-serif';
  if(kind==='kai')return '"LXGW WenKai","STKaiti","Kaiti SC","KaiTi",serif';
  if(kind==='fangsong')return '"FangSong","STFangsong","FangSong_GB2312","Songti SC","STSong","SimSun",serif';
  return '"Iowan Old Style","Palatino Linotype","STSong","SimSun",serif';
}
function setFaceFontOptions(select,options,key){
  if(!select||select.dataset.optionSet===key)return;
  select.replaceChildren(...options.map(item=>{
    const option=document.createElement('option');
    option.value=item.value;
    option.textContent=item.label;
    return option;
  }));
  select.dataset.optionSet=key;
}
function syncFaceFontSelect(select,style,theme=style?.theme){
  if(!select)return;
  if(theme==='tea'){
    setFaceFontOptions(select,TEA_FACE_FONT_OPTIONS,'tea');
    select.value=TEA_FACE_FONT_KEYS.has(style?.fontFamily)?style.fontFamily:FACE_STYLE_PRESETS.tea.fontFamily;
    return;
  }
  if(theme==='accent'){
    setFaceFontOptions(select,ACCENT_FACE_FONT_OPTIONS,'accent');
    select.value=ACCENT_FACE_FONT_KEYS.has(style?.fontFamily)?style.fontFamily:FACE_STYLE_PRESETS.accent.fontFamily;
    return;
  }
  setFaceFontOptions(select,GENERAL_FACE_FONT_OPTIONS,'general');
  let custom=select.querySelector('option[value="custom"]');
  const family=String(style?.customFontFamily||'').trim();
  if(style?.fontFamily==='custom'&&family){
    if(!custom){
      custom=document.createElement('option');
      custom.value='custom';
      select.querySelector('option[value="other"]')?.before(custom);
    }
    custom.textContent=`其他 · ${family}`;
    select.value='custom';
  }else{
    custom?.remove();
    select.value=['sans','serif','kai','fangsong'].includes(style?.fontFamily)?style.fontFamily:'kai';
  }
}
const localFontPicker={all:[],selected:''};
function buildLocalFontDialog(){
  if($('#local-font-dialog'))return;
  const dialog=document.createElement('dialog');
  dialog.id='local-font-dialog';
  dialog.className='local-font-dialog';
  dialog.setAttribute('aria-labelledby','local-font-title');
  dialog.innerHTML=`
    <div class="local-font-card">
      <div class="local-font-head">
        <div><b id="local-font-title">选择本机字体</b><span>选择后先预览，再应用到人名标签</span></div>
        <button type="button" id="local-font-close" aria-label="关闭">×</button>
      </div>
      <div class="local-font-preview" aria-live="polite">
        <span id="local-font-preview-label">示例姓名</span>
        <div><strong id="local-font-preview-name">等待选择字体</strong><small>标签实际效果预览</small></div>
      </div>
      <label class="local-font-search-label">搜索字体<input id="local-font-search" type="search" placeholder="输入字体名称"></label>
      <select id="local-font-list" size="9" aria-label="本机字体列表"></select>
      <p id="local-font-status" class="local-font-status">正在读取本机字体…</p>
      <div class="local-font-actions">
        <button type="button" id="local-font-retry" hidden>重新读取</button>
        <button type="button" id="local-font-cancel">取消</button>
        <button type="button" id="local-font-confirm" class="primary" disabled>确定</button>
      </div>
    </div>`;
  document.body.appendChild(dialog);
  $('#local-font-close').addEventListener('click',()=>dialog.close('cancel'));
  $('#local-font-cancel').addEventListener('click',()=>dialog.close('cancel'));
  dialog.addEventListener('cancel',event=>{event.preventDefault();dialog.close('cancel');});
  dialog.addEventListener('close',()=>toggleFaceStylePopover(true));
  $('#local-font-search').addEventListener('input',renderLocalFontOptions);
  $('#local-font-list').addEventListener('change',event=>{
    localFontPicker.selected=event.target.value;
    updateLocalFontPreview();
  });
  $('#local-font-list').addEventListener('dblclick',()=>{
    if(localFontPicker.selected)confirmLocalFont();
  });
  $('#local-font-retry').addEventListener('click',loadLocalFonts);
  $('#local-font-confirm').addEventListener('click',confirmLocalFont);
}
function localFontPreviewText(){
  const currentFaces=typeof state!=='undefined'?namedFaces(state.detail):[];
  return currentFaces[0]?.name||'示例姓名';
}
function updateLocalFontPreview(){
  const family=localFontPicker.selected;
  const preview=$('#local-font-preview-label');
  if(preview){
    preview.textContent=localFontPreviewText();
    preview.style.fontFamily=customFaceFontStack(family);
    preview.style.writingMode=faceLabelsVertical()?'vertical-rl':'horizontal-tb';
    preview.style.textOrientation=faceLabelsVertical()?'upright':'mixed';
  }
  if($('#local-font-preview-name'))$('#local-font-preview-name').textContent=family||'等待选择字体';
  if($('#local-font-confirm'))$('#local-font-confirm').disabled=!family;
}
function renderLocalFontOptions(){
  const list=$('#local-font-list');
  if(!list)return;
  const query=String($('#local-font-search')?.value||'').trim().toLocaleLowerCase('zh-CN');
  const visible=localFontPicker.all.filter(name=>!query||name.toLocaleLowerCase('zh-CN').includes(query));
  list.replaceChildren(...visible.map(name=>{
    const option=document.createElement('option');
    option.value=name;
    option.textContent=name;
    option.style.fontFamily=customFaceFontStack(name);
    return option;
  }));
  const preferred=visible.includes(localFontPicker.selected)?localFontPicker.selected:visible[0]||'';
  localFontPicker.selected=preferred;
  list.value=preferred;
  updateLocalFontPreview();
  if(localFontPicker.all.length&&$('#local-font-status')){
    $('#local-font-status').textContent=visible.length?`共 ${localFontPicker.all.length} 种字体，当前显示 ${visible.length} 种`:'没有匹配的字体';
  }
}
async function loadLocalFonts(){
  const status=$('#local-font-status'),retry=$('#local-font-retry'),list=$('#local-font-list');
  if(status)status.textContent='正在读取本机字体…';
  if(retry)retry.hidden=true;
  if(list)list.disabled=true;
  localFontPicker.all=[];
  localFontPicker.selected='';
  updateLocalFontPreview();
  if(typeof window.queryLocalFonts!=='function'){
    if(status)status.textContent='当前浏览器不支持读取本机字体。请使用最新版 Chrome 打开拾光。';
    return;
  }
  try{
    const fonts=await window.queryLocalFonts();
    localFontPicker.all=[...new Set(fonts.map(font=>String(font.family||'').trim()).filter(Boolean))]
      .sort((a,b)=>a.localeCompare(b,'zh-CN',{sensitivity:'base'}));
    if(!localFontPicker.all.length)throw new Error('empty-font-list');
    const current=viewer.faceStyle?.fontFamily==='custom'?viewer.faceStyle.customFontFamily:'';
    localFontPicker.selected=localFontPicker.all.includes(current)?current:localFontPicker.all[0];
    if(list)list.disabled=false;
    renderLocalFontOptions();
  }catch(error){
    console.warn('读取本机字体失败',error);
    if(status)status.textContent='没有获得本机字体权限。请允许字体访问后点击“重新读取”。';
    if(retry)retry.hidden=false;
    if(list)list.disabled=true;
  }
}
function openLocalFontDialog(){
  buildLocalFontDialog();
  const dialog=$('#local-font-dialog');
  if(!dialog)return;
  $('#local-font-search').value='';
  dialog.showModal();
  loadLocalFonts();
}
function confirmLocalFont(){
  if(!localFontPicker.selected)return;
  updateFaceStyle({fontFamily:'custom',customFontFamily:localFontPicker.selected});
  $('#local-font-dialog')?.close('confirm');
}
function faceLabelProfile(name){
  const normalized=String(name||'').replace(/\s+/g,'');
  const count=[...normalized].length;
  return {count,size:count<=2?'s':count===3?'m':'l',long:count>=5};
}
function applyFaceLabelProfile(element,name){
  if(!element)return;
  const profile=faceLabelProfile(name);
  element.dataset.faceLabelSize=profile.size;
  element.classList.toggle('long-name',profile.long);
}
function faceLabelsVertical(){
  return Boolean(FACE_LABEL_IMAGES[viewer.faceStyle?.theme])||viewer.faceVertical!==false;
}
function faceLabelThemeReady(theme){
  if(!FACE_LABEL_IMAGES[theme])return Promise.resolve(true);
  if(faceLabelThemeReadiness.has(theme))return faceLabelThemeReadiness.get(theme);
  const promise=Promise.all(Object.values(FACE_LABEL_IMAGES[theme]).map(src=>new Promise(resolve=>{
    const image=new Image();
    image.onload=()=>resolve(true);
    image.onerror=()=>resolve(false);
    image.src=src;
  }))).then(results=>results.every(Boolean));
  faceLabelThemeReadiness.set(theme,promise);
  return promise;
}
function ensureFaceLabelThemeAvailable(theme){
  if(!FACE_LABEL_IMAGES[theme])return;
  faceLabelThemeReady(theme).then(ready=>{
    if(ready||viewer.faceStyle?.theme!==theme)return;
    console.warn(`人名标签主题 ${theme} 的底图缺失，已恢复默认样式`);
    viewer.faceStyle={...DEFAULT_FACE_STYLE};
    applyFaceStyle();
    saveViewerPrefs();
    if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
  });
}
function applyFaceStyle(){
  const root=$('#detail-dialog')||document.documentElement;
  const s=viewer.faceStyle||DEFAULT_FACE_STYLE;
  const theme=Object.prototype.hasOwnProperty.call(FACE_STYLE_PRESETS,s.theme)?s.theme:'classic';
  if(theme==='tea'&&!TEA_FACE_FONT_KEYS.has(s.fontFamily)){
    s.fontFamily=FACE_STYLE_PRESETS.tea.fontFamily;
    delete s.customFontFamily;
  }
  if(theme==='accent'&&!ACCENT_FACE_FONT_KEYS.has(s.fontFamily)){
    s.fontFamily=FACE_STYLE_PRESETS.accent.fontFamily;
    delete s.customFontFamily;
  }
  root.dataset.faceTheme=theme;
  root.dataset.unnamedMarker=FACE_UNNAMED_MARKERS.has(viewer.faceUnnamedMarker)?viewer.faceUnnamedMarker:'plus';
  const pop=$('#face-style-popover');
  if(pop)pop.dataset.faceTheme=theme;
  root.style.setProperty('--face-font-size',`${s.fontSize}px`);
  root.style.setProperty('--face-font-family',faceFontStack(s.fontFamily,s.customFontFamily));
  root.style.setProperty('--face-text-color',s.textColor);
  root.style.setProperty('--face-bg-color',s.backgroundColor);
  root.style.setProperty('--face-bg-opacity',String(s.backgroundOpacity));
  root.style.setProperty('--face-bg-rgba',hexToRgba(s.backgroundColor,s.backgroundOpacity));
  root.style.setProperty('--face-radius',s.radius>=999?'999px':`${s.radius}px`);
  root.style.setProperty('--face-padding-x',`${s.paddingX}px`);
  root.style.setProperty('--face-padding-y',`${s.paddingY}px`);
  root.style.setProperty('--face-shadow',s.shadow?'0 1px 10px rgba(0,0,0,.65)':'none');
  const themeImages=FACE_LABEL_IMAGES[theme];
  if(themeImages){
    Object.entries(themeImages).forEach(([size,url])=>root.style.setProperty(`--face-label-${size}-image`,`url("${url}")`));
  }

  const themeSelect=$('#face-theme'),fontSize=$('#face-font-size'),family=$('#face-font-family'),position=$('#face-label-position'),marker=$('#face-unnamed-marker'),text=$('#face-text-color'),bg=$('#face-bg-color'),op=$('#face-bg-opacity'),radius=$('#face-radius'),shadow=$('#face-shadow');
  if(themeSelect)themeSelect.value=theme;
  if(fontSize){
    fontSize.min=themeImages?'12':'10';
    fontSize.max=themeImages?'18':'22';
    fontSize.value=String(s.fontSize);
  }
  syncFaceFontSelect(family,s,theme);
  if(position)position.value=viewer.faceLabelPosition;
  if(marker)marker.value=root.dataset.unnamedMarker;
  if(text)text.value=s.textColor;
  if(bg)bg.value=s.backgroundColor;
  if(op)op.value=String(Math.round(s.backgroundOpacity*100));
  if(radius)radius.value=String(s.radius);
  if(shadow)shadow.checked=!!s.shadow;
  const imageTheme=Boolean(themeImages);
  const lockedControls=new Set([text,bg,radius,shadow]);
  [fontSize,family,text,bg,op,radius,shadow].forEach(control=>{
    if(!control)return;
    control.disabled=imageTheme&&(lockedControls.has(control)||(control===family&&theme!=='tea'));
  });
  if(pop){
    [fontSize,family,op,radius].forEach(control=>{
      control?.closest('label')?.classList.toggle('is-disabled',Boolean(control.disabled));
    });
    pop.querySelector('.face-style-colors')?.classList.toggle('is-disabled',imageTheme);
    pop.querySelector('.face-shadow-row')?.classList.toggle('is-disabled',imageTheme);
  }
  if($('#face-font-size-value'))$('#face-font-size-value').textContent=`${s.fontSize}px`;
  if($('#face-bg-opacity-value'))$('#face-bg-opacity-value').textContent=`${Math.round(s.backgroundOpacity*100)}%`;
  if(family){
    family.title=theme==='tea'
      ?'茶棕主题：三款本地毛笔字体'
      :theme==='accent'
        ?'暗朱主题：三款本地题签字体'
        :family.disabled?'由当前主题固定':'可选择本机其他字体';
  }
  if($('#face-style-preview-caption')){
    const themeName={classic:'默认',ivory:'素笺',tea:'茶棕',accent:'暗朱'}[theme]||'默认';
    const themedFont=theme==='tea'||theme==='accent';
    const fontName=themedFont?family?.selectedOptions?.[0]?.textContent?.replace('（默认）','')?.split(' · ')[0]:'实际比例';
    $('#face-style-preview-caption').textContent=`${themeName} · ${fontName||'实际比例'}`;
  }
  const preview=$('#face-style-preview-label');
  if(preview){
    const currentFaces=typeof state!=='undefined'?namedFaces(state.detail):[];
    preview.textContent=currentFaces[0]?.name||'示例姓名';
    applyFaceLabelProfile(preview,preview.textContent);
    preview.style.fontSize=themeImages?'':`${s.fontSize}px`;
    preview.style.fontFamily=themeImages?'':faceFontStack(s.fontFamily,s.customFontFamily);
    preview.style.color=themeImages?'':s.textColor;
    preview.style.backgroundColor=themeImages?'':hexToRgba(s.backgroundColor,s.backgroundOpacity);
    preview.style.borderRadius=themeImages?'':s.radius>=999?'999px':`${s.radius}px`;
    preview.style.textShadow=themeImages?'':s.shadow?'0 1px 10px rgba(0,0,0,.65)':'none';
    preview.style.writingMode=faceLabelsVertical()?'vertical-rl':'horizontal-tb';
    preview.style.textOrientation=faceLabelsVertical()?'upright':'mixed';
  }
  ensureFaceLabelThemeAvailable(theme);
}
function hexToRgba(hex,alpha){
  const h=String(hex||'#000000').replace('#','');
  const full=h.length===3?h.split('').map(x=>x+x).join(''):h.padEnd(6,'0').slice(0,6);
  const n=parseInt(full,16);if(!Number.isFinite(n))return `rgba(0,0,0,${alpha})`;
  return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${alpha})`;
}
function updateFaceStyle(patch){
  viewer.faceStyle={...viewer.faceStyle,...patch};applyFaceStyle();saveViewerPrefs();
  if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function toggleFaceStylePopover(force){
  const pop=$('#face-style-popover'),btn=$('#face-style-button');if(!pop)return;
  const open=force==null?pop.hidden:!!force;
  if(open){togglePhotoPeoplePopover(false);closeFaceActionPopover();}
  pop.hidden=!open;if(btn)btn.setAttribute('aria-expanded',String(open));
  if(open)applyFaceStyle();
}
function updateToolVisuals(){
  const dir=$('#toggle-face-dir');
  if(dir){
    const fixedTheme=viewer.faceStyle?.theme;
    const fixedVertical=Boolean(FACE_LABEL_IMAGES[fixedTheme]);
    const vertical=faceLabelsVertical();
    dir.innerHTML=vertical?iconSvg.vertical:iconSvg.horizontal;
    const fixedThemeName=fixedTheme==='ivory'?'素笺':fixedTheme==='tea'?'茶棕':'图片标签';
    const next=fixedVertical?`${fixedThemeName}使用竖排`:vertical?'切换为横排':'切换为竖排';
    dir.title=next;dir.setAttribute('aria-label',next);
  }
  const play=$('#viewer-play');
  if(play){
    play.innerHTML=viewer.playing?iconSvg.pause:iconSvg.play;
    const label=viewer.playing?'暂停幻灯片':'播放幻灯片';play.title=label;play.setAttribute('aria-label',label);
  }
}

function currentBrowseContext(){return {q:state.q,filter:state.view,person:state.person,directory:state.directory,sort:state.sort,date_from:state.dateFrom||'',date_to:state.dateTo||'',place:state.place||'',max_id:state.maxId};}
function browseContextKey(context={}){
 const filter=String(context.filter||'all');
 return JSON.stringify({q:String(context.q||''),filter:filter==='all'?'timeline':filter,person:String(context.person||''),directory:String(context.directory||''),sort:String(context.sort||'date_desc'),date_from:String(context.date_from||''),date_to:String(context.date_to||''),place:String(context.place||''),max_id:Number(context.max_id||0)});
}
function sameBrowseContext(a,b){return browseContextKey(a)===browseContextKey(b);}
function contextLabel(c){const person=state.people.find(p=>String(p.id)===c.person);const filter=c.filter||'';const heading=c.person?(person?.name||'人物 '+c.person):filter.startsWith('year:')?(filter.slice(5)==='unknown'?'时间未记录':filter.slice(5)+' 年'):filter.startsWith('group:')?(filter.slice(6)==='10plus'?'10人及以上':Number(filter.slice(6))+'人合影'):filter.startsWith('place:')?(filter.slice(6)==='unknown'?'地点未记录':filter.slice(6)):titles[filter]?.[0]||'照片';return [heading,c.directory?basename(c.directory):'',c.place?prettyPlace(c.place):'',c.date_from||c.date_to?((c.date_from||'')+(c.date_to&&c.date_to!==c.date_from?' ~ '+c.date_to:'')):'',c.q?'搜索：'+c.q:''].filter(Boolean).join(' · ');}
function viewerMessage(text){$('#viewer-message').textContent=text;}
function clearViewerImage(){
 const img=$('#detail-img'),signature=$('#photo-signature'),faces=$('#face-name-layer');
 if(img){img.removeAttribute('src');img.hidden=true;img.style.width='';img.style.height='';}
 if(signature){signature.hidden=true;signature.replaceChildren();}
 if(faces){faces.hidden=true;faces.replaceChildren();}
 const favorite=$('#photo-favorite');if(favorite)favorite.hidden=true;
 const placeMap=$('#photo-place-map');if(placeMap)placeMap.hidden=true;
}
function setViewerLoading(loading){
 const dialog=$('#detail-dialog'),img=$('#detail-img'),signature=$('#photo-signature'),faces=$('#face-name-layer'),stage=document.querySelector('.viewer-stage');
 if(!dialog)return;
 let indicator=$('#viewer-loading');
 if(!indicator&&stage){indicator=document.createElement('div');indicator.id='viewer-loading';indicator.textContent='加载中…';indicator.setAttribute('aria-live','polite');stage.appendChild(indicator);}
 clearTimeout(viewer.loadingTimer);viewer.loadingTimer=null;
 dialog.classList.toggle('is-loading',Boolean(loading));
 const hasVisibleImage=Boolean(img&&!img.hidden&&img.getAttribute('src'));
 dialog.classList.toggle('is-switching',Boolean(loading&&hasVisibleImage));
 if(indicator)indicator.hidden=true;
 if(loading){
  if(!hasVisibleImage&&indicator)viewer.loadingTimer=setTimeout(()=>{if(dialog.classList.contains('is-loading'))indicator.hidden=false;},100);
  viewerMessage('');
 }else{
  if(img)img.hidden=!img.getAttribute('src');
  if(signature)signature.hidden=!signature.childElementCount;
  dialog.classList.remove('is-switching');
 }
}
function viewerImageFailed(message){
 const img=$('#detail-img');
 if(!img||img.hidden||!img.getAttribute('src'))clearViewerImage();
 setViewerLoading(false);
 viewerMessage(message);
}
function waitViewerFrames(count=2){return new Promise(resolve=>{const next=()=>count--<=0?resolve():requestAnimationFrame(next);next();});}
function closePhotoViewer(){
 const dialog=$('#detail-dialog');
 if(!dialog||!dialog.open)return;
 clearTimeout(viewer.closingTimer);
 if(matchMedia('(prefers-reduced-motion: reduce)').matches){dialog.close();return;}
 dialog.classList.add('viewer-closing');
 viewer.closingTimer=setTimeout(()=>{if(dialog.open)dialog.close();},150);
}
globalThis.closePhotoViewer=closePhotoViewer;
function stopSlides(){viewer.playing=false;clearTimeout(viewer.timer);viewer.timer=null;syncViewerTools();}
function pressTool(id, on){const el=$(id); if(!el)return; el.setAttribute('aria-pressed', String(!!on));}
function syncViewerTools(){
 pressTool('#toggle-face-names', viewer.faceNames!==false);
 pressTool('#toggle-face-alias', viewer.faceAlias===true);
 pressTool('#toggle-face-dir', viewer.faceVertical!==false);
 pressTool('#viewer-info', !$('#detail-dialog').classList.contains('hide-info'));
 pressTool('#viewer-play', viewer.playing);
 const aliasBtn=$('#toggle-face-alias'); if(aliasBtn) aliasBtn.disabled=viewer.faceNames===false;
 const dirBtn=$('#toggle-face-dir'); if(dirBtn) dirBtn.disabled=viewer.faceNames===false||Boolean(FACE_LABEL_IMAGES[viewer.faceStyle?.theme]);
 const styleBtn=$('#face-style-button');if(styleBtn)styleBtn.disabled=viewer.faceNames===false;
 updateToolVisuals();
}
async function togglePhotoFavorite(){
 const id=Number(state.detail?.id);if(!id)throw new Error('没有可收藏的照片');
 const button=$('#photo-favorite');if(!button||button.disabled)return;
 const favorite=!Boolean(state.detail?.favorite);
 button.disabled=true;
 try{
  const result=await api('/api/photos/'+id+'/favorite',{method:'PUT',body:JSON.stringify({favorite})});
  if(Number(state.detail?.id)===id){state.detail.favorite=Boolean(result.favorite);syncPhotoFavorite(state.detail);}
  if(state.status?.stats)state.status.stats.favorite_photos=Number(result.favorite_count)||0;
  setText('#favorites-count',fmt(result.favorite_count));
  if(state.view==='favorites')state.favoriteViewDirty=true;
  toast(result.favorite?'已收藏这张照片':'已取消收藏');
 }finally{if(Number(state.detail?.id)===id&&button)button.disabled=false;}
}
function syncPhotoFavorite(photo=state.detail){
 const button=$('#photo-favorite');if(!button)return;
 const ready=Boolean(photo&&photo.id);
 const favorite=Boolean(photo?.favorite);
 button.hidden=!ready;
 button.disabled=false;
 button.setAttribute('aria-pressed',String(favorite));
 button.setAttribute('aria-label',favorite?'取消收藏这张照片':'收藏这张照片');
 button.title=favorite?'取消收藏':'收藏这张照片';
 const label=button.querySelector('span');if(label)label.textContent=favorite?'已收藏':'收藏';
}
$('#photo-favorite')?.addEventListener('click',action(togglePhotoFavorite));
function syncPhotoPlaceButton(photo=state.detail){
 const button=$('#photo-place-map');if(!button)return;
 const ready=Boolean(photo&&photo.id);
 const located=ready&&hasPhotoCoordinates(photo);
 button.hidden=!ready;
 button.dataset.located=String(located);
 button.setAttribute('aria-label',located?'在地图上查看这张照片':'这张照片没有定位坐标');
 button.title=located?'在地图上查看':'没有定位坐标';
}
async function openCurrentPhotoPlaceMap(){
 const photo=state.detail;
 if(!hasPhotoCoordinates(photo))throw new Error('这张照片没有定位坐标');
 const context=viewer.context?{...viewer.context}:currentBrowseContext();
 const dialog=$('#detail-dialog');
 if(dialog&&dialog.open){const closed=new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true}));closePhotoViewer();await closed;}
 await showPhotoPlaceMap(photo,{context,returnView:state.view,absolute:viewer.absolute});
}
$('#photo-place-map')?.addEventListener('click',action(async e=>{e.preventDefault();e.stopPropagation();await openCurrentPhotoPlaceMap();}));
function scheduleSlide(){clearTimeout(viewer.timer);viewer.timer=setTimeout(async()=>{try {await movePhoto(1,true);}catch(e){viewerMessage(e.message);stopSlides();}},Number($('#slide-delay').value)*1000);}
function updatePosition(){
 const absolute=Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.index||0);
 $('#viewer-position').textContent=`${fmt(absolute+1)} / ${fmt(viewer.total||viewer.ids.length)}`;
 $('#viewer-prev').disabled=absolute<=0;$('#viewer-next').disabled=absolute>=(viewer.total||viewer.ids.length)-1;
 $('#viewer-context').textContent=contextLabel(viewer.context||{});
}
function signatureModeForWidth(width){
 const w=Number(width)||0;
 return w>=980?'wide':w>=620?'medium':'narrow';
}
function signatureHeightForMode(mode){
 return mode==='narrow'?88:80;
}
function signatureWidthForPhoto(width){
 const signature=$('#photo-signature');
 const frame=signature?.dataset.style==='tone'?12:0;
 return signature&&!signature.hidden?Math.max(width,Math.min(280,$('#image-viewport').clientWidth-frame)):width;
}
function applySignatureMode(mode,width){
 const dialog=$('#detail-dialog');
 if(!dialog)return 0;
 dialog.dataset.signatureMode=mode;
 dialog.dataset.signatureSmall=String(signatureWidthForPhoto(width)<380);
 const content=$('#photo-signature .signature-v3');
 if(Number.isFinite(width))$('#photo-mat').style.width=signatureWidthForPhoto(width)+'px';
 // Measure natural wrapped content; fixed heights used to clip long tokens.
 const groups=content?.querySelectorAll(':scope > div:not([hidden])').length||0;
 const minimum=groups<=1?64:signatureHeightForMode(mode);
 // Layout height excludes the dialog's opening scale animation.
 const height=Math.max(minimum,Math.ceil(content?parseFloat(getComputedStyle(content).height)||content.offsetHeight:0));
 dialog.style.setProperty('--viewer-signature-h',height+'px');
 return height;
}
function updateZoom(reset=false){
 const img=$('#detail-img'),area=$('#image-viewport'),mat=$('#photo-mat');if(!img.naturalWidth)return;
 const signature=$('#photo-signature');
 const framed=signature&&!signature.hidden&&signature.dataset.style==='tone';
  let w=0,h=0;
  let reserved=0;
  // Reserve monotonically within one fit operation so a breakpoint cannot
  // oscillate between a wider/shorter and narrower/taller caption.
  for(let pass=0;pass<10;pass++){
   if(viewer.fit){
    const width=Math.max(40,area.clientWidth-(framed?12:0));
    const height=Math.max(1,area.clientHeight-reserved-(framed?6:0));
    viewer.scale=Math.min(width/img.naturalWidth,height/img.naturalHeight);
   }
   w=Math.max(1,Math.round(img.naturalWidth*viewer.scale));
   h=Math.max(1,Math.round(img.naturalHeight*viewer.scale));
   const mode=signatureModeForWidth(w);
   const nextHeight=signature&&!signature.hidden?applySignatureMode(mode,w):0;
   if(!viewer.fit||nextHeight<=reserved)break;
   reserved=nextHeight;
  }
  w=Math.max(1,Math.round(img.naturalWidth*viewer.scale));
  h=Math.max(1,Math.round(img.naturalHeight*viewer.scale));
  if(signature&&!signature.hidden)applySignatureMode(signatureModeForWidth(w),w);
  area.classList.toggle('zoomed',!viewer.fit);
  const matWidth=signatureWidthForPhoto(w);
  mat.style.width=matWidth+'px';
  mat.style.setProperty('--viewer-image-inset',((matWidth-w)/2)+'px');
  img.style.width=w+'px';
  img.style.height=h+'px';
  const captionHeight=signature&&!signature.hidden?signature.offsetHeight:0;
  const photoCenter=(area.clientHeight-captionHeight)/2;
  const arrowHalf=($('#viewer-next').offsetHeight||56)/2;
  const navigationCenter=Math.min(photoCenter,photoCenter+h/2-arrowHalf-4);
  $('#detail-dialog').style.setProperty('--viewer-photo-center',Math.max(arrowHalf,navigationCenter)+'px');
  $('#zoom-level').textContent=Math.round(viewer.scale*100)+'%';
  if(reset){area.scrollTop=0;area.scrollLeft=0;}
  if(state.detail&&viewer.faceNames!==false)requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function renderSignature(a,file){
 const signature=$('#photo-signature');if(!signature)return;
 const tags=a.metadata?.ExifTool||{};
 const tag=name=>Object.entries(tags).find(([key])=>key.split(':').at(-1)===name)?.[1];
 const clean=value=>String(value||'').replace(/\0/g,'').trim();
 const number=name=>{const raw=tag(name);const n=Number(raw);return Number.isFinite(n)&&n>0?n:null;};
 const make=clean(tag('Make')),model=clean(tag('Model'));
 const camera=model?(make&&model.toLowerCase().startsWith(make.toLowerCase())?model:[make,model].filter(Boolean).join(' ')):clean(a.camera);
 const source=a.effective_source||'';
 const timeKind=source.includes('修改')?'文件时间参考':source.includes('推测')?'推测时间':source==='人工确认'?'补录时间':'拍摄时间';
 const rawDate=clean(a.effective_date);
 const shownDate=/^\d{4}-\d\d-\d\dT/.test(rawDate)?rawDate.slice(0,16).replaceAll('-','.').replace('T','  '):rawDate;
 const dateParts=shownDate.match(/^(\d{4}\.\d{2}\.\d{2})\s+(\d{2}:\d{2})$/);
 const dateMarkup=dateParts?`<span>${esc(dateParts[1])}</span><span class="signature-clock">${esc(dateParts[2])}</span>`:esc(shownDate);
 const place=prettyPlace(a.effective_place)||'';
 const focal=number('FocalLengthIn35mmFormat')||number('FocalLength');
 const aperture=number('FNumber'),shutter=number('ExposureTime'),iso=number('ISO');
 const exposure=[];
 if(focal)exposure.push(`${Number(focal.toFixed(1))}mm`);
 if(aperture)exposure.push(`ƒ/${Number(aperture.toFixed(1))}`);
 if(shutter)exposure.push(shutter<1?`1/${Math.round(1/shutter)}s`:`${Number(shutter.toFixed(2))}s`);
 if(iso)exposure.push(`ISO ${Math.round(iso)}`);
 const size=Number(file?.size);
 const fileSize=Number.isFinite(size)&&size>0?(size>=1024*1024?(size/1024/1024).toFixed(1)+' MB':(size/1024).toFixed(0)+' KB'):'';
 const dimensions=(Number(a.width)>0&&Number(a.height)>0)?`${a.width} × ${a.height}`:'';
 const format=clean(a.format)||clean(file?.path?.split('.').pop()?.toUpperCase());
 const basics=[dimensions,format,fileSize].filter(Boolean);
 const filename=file?.path?basename(file.path):'';
 const hasMemory=Boolean(shownDate||place);
 const hasCapture=Boolean(camera||exposure.length);
 const hasFile=basics.length>0;
 const timeKindMarkup=shownDate&&timeKind!=='拍摄时间'?`<small id="signature-primary-kind">${esc(timeKind)}</small>`:'';
 const exposureMarkup=exposure.map(value=>`<span class="signature-exposure-token">${esc(value)}</span>`).join('');
 const fileMarkup=[[dimensions,'dimensions'],[format,'format-token'],[fileSize,'size-token']].filter(([value])=>value).map(([value,kind])=>`<span class="signature-file-token signature-${kind}">${esc(value)}</span>`).join('');
 const placeMarkup=place?`<span id="signature-place" class="signature-place" title="${esc(place)}">${esc(place)}</span>`:'';
 const title=[shownDate&&`${timeKind}：${rawDate}`,place&&`地点：${place}`,camera&&`设备：${camera}`,exposure.join(' · '),basics.join(' · '),filename&&`文件名：${filename}`].filter(Boolean).join('\n');

 signature.innerHTML=`
  <div class="signature-v3 ${hasMemory?'has-memory':''} ${hasCapture?'has-capture':''} ${hasFile?'has-file':''}" data-has-memory="${hasMemory}" data-has-capture="${hasCapture}" data-has-file="${hasFile}">
   <span class="signature-seal-v3" aria-hidden="true">拾</span>
    <div id="signature-primary" class="signature-memory"${hasMemory?'':' hidden'}>
     <div class="signature-memory-main">
      <strong id="signature-primary-value" class="signature-date"${shownDate?'':' hidden'}>${dateMarkup}</strong>
      ${timeKindMarkup}
     </div>
    ${placeMarkup}
   </div>
   <div id="signature-settings" class="signature-capture"${hasCapture?'':' hidden'}>
    ${camera?`<span class="signature-camera" title="${esc(camera)}">${esc(camera)}</span>`:''}
    ${exposure.length?`<div class="signature-exposure">${exposureMarkup}</div>`:''}
   </div>
   <div id="signature-format" class="signature-file"${hasFile?'':' hidden'}>${fileMarkup}</div>
  </div>
  <button type="button" class="signature-switch" aria-describedby="signature-switch-tip" title="">
   <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="2"/><path d="M3 12h14M7 14h2m3 0h1"/></svg>
  </button>
  <span id="signature-switch-tip" class="signature-switch-tip" role="tooltip"></span>`;
 signature.hidden=false;
 signature.title=title;
 const cameraLine=camera?`<strong class="caption-camera" title="${esc(camera)}">${esc(camera)}</strong>`:'';
 const dateLine=shownDate?`<span class="caption-date">${dateMarkup}${timeKindMarkup}</span>`:'';
 const placeLine=place?`<span class="caption-place" title="${esc(place)}">${esc(place)}</span>`:'';
 const memoryLine=`<div class="caption-memory">${dateLine}${placeLine}</div>`;
 const exposureLine=exposure.length?`<div class="caption-exposure">${exposureMarkup}</div>`:'';
 const filesLine=hasFile?`<div class="caption-files">${fileMarkup}</div>`:'';
 const redMark='<span class="caption-brand caption-red" role="img" aria-label="拾光红色圆形字标"><svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="24" fill="#d21e28"/><text x="24" y="29" text-anchor="middle" fill="white" font-family="Ma Shan Zheng" font-size="20">拾光</text></svg></span>';
 const blueMark='<span class="caption-brand caption-blue" role="img" aria-label="拾光蓝色光学方标"><svg viewBox="0 0 40 40" aria-hidden="true"><path fill="#123cba" d="M0 0h40v40q-20-7-40 0z"/><path d="M10 25h20M20 9v10m-9-7 4 5m14-5-4 5m-7 3c0 6-3 8-7 9m12-9v7q0 2 6 1" stroke="white" stroke-width="1.7" fill="none" stroke-linecap="round"/></svg></span>';
 const galleryMark='<span class="caption-brand caption-gallery-brand" aria-label="拾光相册"><svg viewBox="0 0 36 30" aria-hidden="true"><path d="m4 23 10-16h7l-5 8h8l5-8h5L24 23h-7l5-8h-8l-5 8z" fill="currentColor"/></svg><span>拾光相册</span></span>';
 const ringMark='<span class="caption-brand caption-ring" role="img" aria-label="拾光光圈圆环标"><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15"/><circle cx="18" cy="18" r="10"/><path d="M18 8v6m10 4h-6m-4 10v-6M8 18h6"/><circle cx="18" cy="18" r="3"/></svg></span>';
 signatureLayouts={
  original:signature.querySelector('.signature-v3').innerHTML,
  classic:`<div class="caption-panel caption-left">${cameraLine||'<strong class="caption-camera">拾光相册</strong>'}${memoryLine}</div><div class="caption-panel caption-right caption-leica-lockup">${redMark}<div class="caption-technical">${exposureLine}${filesLine}</div></div>`,
  gallery:`<div class="caption-panel caption-left">${cameraLine||'<strong class="caption-camera">拾光相册</strong>'}${memoryLine}</div><div class="caption-panel caption-right">${galleryMark}${exposureLine}${filesLine}</div>`,
  handwritten:`<div class="caption-panel caption-left"><div class="caption-optical-lockup">${cameraLine||'<strong class="caption-camera">拾光</strong>'}${blueMark}</div>${exposureLine}${filesLine}</div><div class="caption-panel caption-right"><span class="caption-handmark" role="img" aria-label="拾光相册手写字标">拾光相册</span>${memoryLine}</div>`,
  tone:`<div class="caption-panel caption-left"><div class="caption-tone-lockup"><span class="caption-tone-wordmark">拾光相册</span>${ringMark}</div>${cameraLine}</div><div class="caption-panel caption-right">${exposureLine}${memoryLine}${filesLine}</div>`
 };
 applySignatureStyle();
}
function applySignatureStyle(){
 const signature=$('#photo-signature');
 if(!signature)return;
 const style=SIGNATURE_STYLES[signatureStyleIndex];
 signature.dataset.style=style.key;
 const content=signature.querySelector('.signature-v3');
 if(content&&signatureLayouts){
  content.innerHTML=signatureLayouts[style.key];
  content.classList.toggle('signature-designed',style.key!=='original');
 }
 $('#photo-mat').dataset.captionStyle=style.key;
 const next=SIGNATURE_STYLES[(signatureStyleIndex+1)%SIGNATURE_STYLES.length];
 const button=signature.querySelector('.signature-switch');
 if(button)button.setAttribute('aria-label',`点击切换标签样式；当前${style.label}，下一款${next.label}`);
 const tip=signature.querySelector('.signature-switch-tip');
 if(tip)tip.textContent=`点击切换 · ${style.label} ${signatureStyleIndex+1}/5`;
 if(style.key==='tone')updateSignaturePalette();
 const font=style.key==='handwritten'?'Ma Shan Zheng':style.key==='gallery'?'Noto Serif SC':style.key==='classic'?'Ma Shan Zheng':null;
 if(font)document.fonts.load(`20px "${font}"`).then(()=>{
  if(signature.dataset.style===style.key&&!signature.hidden)updateZoom();
 }).catch(()=>{});
}
function updateSignaturePalette(){
 const signature=$('#photo-signature'),img=$('#detail-img');
 if(!signature||signature.dataset.style!=='tone'||!img?.complete||!img.naturalWidth)return;
 const src=img.currentSrc||img.src;
 if(src===signaturePaletteSrc)return;
 signaturePaletteSrc=src;
 let rgb=[61,69,64];
 try{
  // Tiny, same-origin preview sample only; the photo and its metadata stay intact.
  const canvas=document.createElement('canvas');canvas.width=24;canvas.height=24;
  const ctx=canvas.getContext('2d',{willReadFrequently:true});
  ctx.drawImage(img,0,0,24,24);
  const pixels=ctx.getImageData(0,0,24,24).data,bins=new Map();
  for(let i=0;i<pixels.length;i+=4){
   if(pixels[i+3]<128)continue;
   const color=[pixels[i],pixels[i+1],pixels[i+2]];
   const key=color.map(v=>v>>5).join(',');
   const bin=bins.get(key)||{count:0,sum:[0,0,0]};
   bin.count++;color.forEach((v,j)=>bin.sum[j]+=v);bins.set(key,bin);
  }
  const dominant=[...bins.values()].sort((a,b)=>b.count-a.count)[0];
  if(dominant){
   const average=dominant.sum.map(v=>v/dominant.count);
   const gray=average.reduce((a,b)=>a+b,0)/3;
   // Preserve hue while limiting brightness for small, warm-white lettering.
   const muted=average.map(v=>v*.9+gray*.1);
   const scale=Math.min(1,100/Math.max(1,...muted));
   rgb=muted.map(v=>Math.max(18,Math.round(v*scale)));
  }
 }catch(e){/* Unsupported image sources use the neutral dark fallback. */}
 signature.style.setProperty('--signature-tone',`rgb(${rgb.join(',')})`);
 $('#photo-mat').style.setProperty('--signature-tone',`rgb(${rgb.join(',')})`);
}
$('#photo-signature').addEventListener('click',event=>{
 if(!event.target.closest('.signature-switch'))return;
 event.stopPropagation();
 signatureStyleIndex=(signatureStyleIndex+1)%SIGNATURE_STYLES.length;
 applySignatureStyle();
 updateZoom();
});
function namedFaces(photo){return (photo&&photo.faces||[]).filter(f=>f.name&&!f.ignored);}
function visibleFaces(photo){return (photo&&photo.faces||[]);}
function viewerImageSrc(photo, file, id){
 const w=Number(photo&&photo.width)||0, h=Number(photo&&photo.height)||0;
 const format=String(photo&&photo.format||'').toUpperCase();
 const path=String(file&&file.path||'');
 const ext=(path.split('.').pop()||'').toLowerCase();
 const heavyExt=['heic','heif','dng','arw','cr2','nef','raf','rw2','orf','tif','tiff'];
 const browserOk=['jpg','jpeg','png','gif','webp','bmp'];
 const tooBig=(w&&h&&(w*h>12000000 || Math.max(w,h)>4000)) || Number(file&&file.size)>12*1024*1024;
 if(!photo||!photo.in_library) return '/api/thumb/'+id+'?v='+state.thumbRevision;
 if(tooBig || heavyExt.includes(ext) || ['HEIF','TIFF','MPO'].includes(format) || (ext && !browserOk.includes(ext))){
  return '/api/preview/'+id+'?v='+state.thumbRevision;
 }
 return '/api/original/'+id;
}
function faceBox(face){
 let box=face.bbox; if(typeof box==='string'){try{box=JSON.parse(box);}catch(e){box=null;}}
 if(!Array.isArray(box)||box.length<4)return null;
 const [x1,y1,x2,y2,w,h]=box.map(Number);
 if(![x1,y1,x2,y2].every(Number.isFinite))return null;
 return {x1,y1,x2,y2,w:w||0,h:h||0,cx:(x1+x2)/2,cy:(y1+y2)/2,width:Math.max(1,x2-x1),height:Math.max(1,y2-y1)};
}
function faceLabelText(face, alias){
  if(face.ignored) return '+';
  if(face.name) return alias&&face.alias?(face.name+' / '+face.alias):face.name;
  return '+';
}
function rectOverlapArea(a,b){
 const w=Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x));
 const h=Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));
 return w*h;
}
function rectOverlapRatio(a,b){
 const smaller=Math.min(Math.max(1,a.w*a.h),Math.max(1,b.w*b.h));
 return rectOverlapArea(a,b)/smaller;
}
function faceLabelDistance(rect,item){
 const dx=Math.max(item.fx-(rect.x+rect.w),rect.x-(item.fx+item.fw),0);
 const dy=Math.max(item.fy-(rect.y+rect.h),rect.y-(item.fy+item.fh),0);
 return dx+dy;
}
function compareFaceLabelScore(a,b){
 for(let i=0;i<a.length;i++)if(a[i]!==b[i])return a[i]-b[i];
 return 0;
}
function clampFaceLabel(rect, layerW, layerH, pad=6){
  rect.x=Math.min(Math.max(pad, rect.x), Math.max(pad, layerW-rect.w-pad));
  rect.y=Math.min(Math.max(pad, rect.y), Math.max(pad, layerH-rect.h-pad));
  return rect;
}
function faceLabelCandidate(item, side, vertical, width, height, offset, gap=6){
  if(side==='left'||side==='right'){
    return {
      x:side==='left'?item.fx-width-gap:item.fx+item.fw+gap,
      y:item.fy+item.fh/2-height/2+offset,
      w:width,h:height,side,offset,btn:item.btn
    };
  }
  const y=side==='top'?item.fy-height-gap:item.fy+item.fh+gap;
  return {x:item.cx-width/2+offset,y,w:width,h:height,side,offset,btn:item.btn};
}
function faceLabelOffsets(side,width,height,layerW,layerH){
  const sideAxis=side==='left'||side==='right';
  const step=Math.max(12,Math.round((sideAxis?height:width)*.28));
  const span=sideAxis?layerH:layerW;
  const offsets=[0];
  for(let distance=step;distance<=span;distance+=step)offsets.push(-distance,distance);
  return offsets;
}
function faceLabelSide(items, ox, imgW){
  const groupMin=Math.min(...items.map(item=>item.fx));
  const groupMax=Math.max(...items.map(item=>item.fx+item.fw));
  const leftSpace=Math.max(0,groupMin-ox);
  const rightSpace=Math.max(0,ox+imgW-groupMax);
  if(leftSpace!==rightSpace)return leftSpace>rightSpace?'left':'right';
  const leftMin=Math.min(...items.map(item=>item.fx-ox));
  const rightMin=Math.min(...items.map(item=>ox+imgW-item.fx-item.fw));
  return leftMin>=rightMin?'left':'right';
}
function faceLabelSides(items,ox,imgW,vertical){
  const forced=viewer.faceLabelPosition;
  if(forced==='left'||forced==='right')return [forced,forced==='left'?'right':'left'];
  if(forced==='top'||forced==='bottom')return [forced,forced==='top'?'bottom':'top'];
  if(!vertical)return ['bottom','top'];
  const automatic=faceLabelSide(items,ox,imgW);
  return [automatic,automatic==='left'?'right':'left'];
}
function layoutFaceNameButtons(layer, faces, alias){
  const img=$('#detail-img');
  if(!img||!img.naturalWidth||!layer.clientWidth)return;
  const layerW=layer.clientWidth, layerH=layer.clientHeight;
  const imgRect=img.getBoundingClientRect();
  const layerRect=layer.getBoundingClientRect();
  const ox=imgRect.left-layerRect.left;
  const oy=imgRect.top-layerRect.top;
  const imgW=imgRect.width, imgH=imgRect.height;
  const items=[];
  faces.forEach(face=>{
    const box=faceBox(face); if(!box||!box.w||!box.h)return;
    const fx=ox+box.x1/box.w*imgW;
    const fy=oy+box.y1/box.h*imgH;
    const fw=box.width/box.w*imgW;
    const fh=box.height/box.h*imgH;
    const passerby=Boolean(face.ignored);
    items.push({face, passerby, named:!passerby&&Boolean(face.name), label:faceLabelText(face,alias), fx, fy, fw, fh, cx:fx+fw/2, cy:fy+fh/2});
  });
  items.sort((a,b)=>a.cy-b.cy||a.cx-b.cx);
  layer.innerHTML=items.map(it=>{
    const hint=it.named?`${it.label}；点击管理这张脸`:(it.passerby?'路人；点击可重新命名':'命名人物');
    return `<button type="button" class="face-name${it.named?'':' unnamed'}${it.passerby?' passerby':''}" data-face-id="${Number(it.face.id)||''}" data-face-person="${it.face.person_id}" title="${esc(hint)}" aria-label="${esc(hint)}">${esc(it.label)}</button>`;
  }).join('')+'<svg class="face-hover-guide" aria-hidden="true"><path></path><circle r="2.6"></circle></svg><span class="face-hover-box" aria-hidden="true"></span>';
  const buttons=[...layer.querySelectorAll('.face-name')];
  const vertical=faceLabelsVertical();
  const placed=[];
  const gap=6;
  const sides=faceLabelSides(items,ox,imgW,vertical);
  const preferredSide=sides[0];
  const faceRects=items.map(item=>({x:item.fx,y:item.fy,w:item.fw,h:item.fh}));
  buttons.forEach((btn,i)=>{
    const it=items[i];
    it.btn=btn;
    if(it.named)applyFaceLabelProfile(btn,it.label);
    const w=Math.max(18, btn.offsetWidth);
    const h=Math.max(18, btn.offsetHeight);
    const offsetsBySide=new Map(sides.map(side=>[side,faceLabelOffsets(side,w,h,layerW,layerH)]));
    const candidates=new Map();
    for(const side of sides){
      for(const offset of offsetsBySide.get(side)){
        const candidate=faceLabelCandidate(it,side,vertical,w,h,offset,gap);
        clampFaceLabel(candidate,layerW,layerH,4);
        const key=`${side}:${Math.round(candidate.x)}:${Math.round(candidate.y)}`;
        if(!candidates.has(key))candidates.set(key,candidate);
      }
    }
    let chosen=null;let bestScore=null;
    for(const candidate of candidates.values()){
      const labelRatios=placed.map(other=>rectOverlapRatio(candidate,other));
      const maxLabelRatio=labelRatios.length?Math.max(...labelRatios):0;
      const placedOverlap=placed.reduce((sum,other)=>sum+rectOverlapArea(candidate,other),0);
      const faceRatios=faceRects.map((faceRect,faceIndex)=>faceIndex===i?0:rectOverlapRatio(candidate,faceRect));
      const maxFaceRatio=Math.max(0,...faceRatios);
      const otherFaceOverlap=faceRects.reduce((sum,faceRect,faceIndex)=>sum+(faceIndex===i?0:rectOverlapArea(candidate,faceRect)),0);
      const hardLabel=maxLabelRatio>FACE_LABEL_OVERLAP_LIMIT?1:0;
      const hardFace=maxFaceRatio>FACE_LABEL_FACE_OVERLAP_LIMIT?1:0;
      const score=[
        hardLabel||hardFace?1:0,
        hardLabel,
        hardFace,
        maxLabelRatio,
        hardFace?maxFaceRatio:0,
        placedOverlap,
        candidate.side===preferredSide?0:1,
        Math.abs(candidate.offset),
        faceLabelDistance(candidate,it),
        otherFaceOverlap
      ];
      if(!bestScore||compareFaceLabelScore(score,bestScore)<0){
        chosen=candidate;
        bestScore=score;
      }
    }
    chosen=chosen||clampFaceLabel(faceLabelCandidate(it,sides[0],vertical,w,h,0,gap),layerW,layerH,4);
    placed.push(chosen);
    btn.classList.add(chosen.side);
    if(!it.named)btn.setAttribute('title',it.passerby?'路人；点击可重新命名':'命名人物');
  });
  placed.forEach((rect,index)=>{
    const maxRatio=placed.reduce((max,other,otherIndex)=>otherIndex===index?max:Math.max(max,rectOverlapRatio(rect,other)),0);
    rect.btn.dataset.labelOverlapRatio=maxRatio.toFixed(3);
    rect.btn.style.left=Math.round(rect.x)+'px';
    rect.btn.style.top=Math.round(rect.y)+'px';
  });
}
function faceForLabel(button){
  const faceId=Number(button?.dataset.faceId);
  return (state.detail?.faces||[]).find(face=>Number(face.id)===faceId)||null;
}
function showFaceGuide(button){
  const layer=$('#face-name-layer'),img=$('#detail-img'),face=faceForLabel(button),box=faceBox(face);
  const halo=layer?.querySelector('.face-hover-box'),guide=layer?.querySelector('.face-hover-guide');
  if(!layer||!img||!button||!box||!halo||!guide)return;
  const layerRect=layer.getBoundingClientRect(),imageRect=img.getBoundingClientRect(),labelRect=button.getBoundingClientRect();
  const faceRect={
    x:imageRect.left-layerRect.left+box.x1/box.w*imageRect.width,
    y:imageRect.top-layerRect.top+box.y1/box.h*imageRect.height,
    w:box.width/box.w*imageRect.width,
    h:box.height/box.h*imageRect.height
  };
  const pad=4;
  halo.style.left=Math.round(faceRect.x-pad)+'px';halo.style.top=Math.round(faceRect.y-pad)+'px';
  halo.style.width=Math.round(faceRect.w+pad*2)+'px';halo.style.height=Math.round(faceRect.h+pad*2)+'px';
  const label={x:labelRect.left-layerRect.left,y:labelRect.top-layerRect.top,w:labelRect.width,h:labelRect.height};
  const lc={x:label.x+label.w/2,y:label.y+label.h/2},fc={x:faceRect.x+faceRect.w/2,y:faceRect.y+faceRect.h/2};
  let start,end;
  if(Math.abs(fc.x-lc.x)>=Math.abs(fc.y-lc.y)){
    start={x:fc.x>=lc.x?label.x+label.w:label.x,y:lc.y};
    end={x:fc.x>=lc.x?faceRect.x:faceRect.x+faceRect.w,y:Math.max(faceRect.y,Math.min(faceRect.y+faceRect.h,lc.y))};
  }else{
    start={x:lc.x,y:fc.y>=lc.y?label.y+label.h:label.y};
    end={x:Math.max(faceRect.x,Math.min(faceRect.x+faceRect.w,lc.x)),y:fc.y>=lc.y?faceRect.y:faceRect.y+faceRect.h};
  }
  guide.setAttribute('viewBox',`0 0 ${layer.clientWidth} ${layer.clientHeight}`);
  guide.querySelector('path').setAttribute('d',`M ${start.x.toFixed(1)} ${start.y.toFixed(1)} L ${end.x.toFixed(1)} ${end.y.toFixed(1)}`);
  const dot=guide.querySelector('circle');dot.setAttribute('cx',end.x.toFixed(1));dot.setAttribute('cy',end.y.toFixed(1));
  layer.querySelectorAll('.face-name.is-linked').forEach(item=>item.classList.remove('is-linked'));
  button.classList.add('is-linked');layer.classList.add('is-linking');
}
function hideFaceGuide(force=false){
  if(!force&&viewer.faceAction)return;
  const layer=$('#face-name-layer');if(!layer)return;
  layer.classList.remove('is-linking');layer.querySelectorAll('.face-name.is-linked').forEach(item=>item.classList.remove('is-linked'));
}
function renderFaceNames(photo){
 const layer=$('#face-name-layer'); if(!layer)return;
 const show=viewer.faceNames!==false;
 const alias=viewer.faceAlias===true;
  const vertical=faceLabelsVertical();
 layer.hidden=!show;
 layer.classList.toggle('horizontal',!vertical);
  if(!show){layer.innerHTML='';syncViewerTools();return;}
  const faces=visibleFaces(photo);
  if(!$('#detail-img')?.naturalWidth || !layer.clientWidth){
    layer.innerHTML='';
    requestAnimationFrame(()=>{ if(state.detail===photo) layoutFaceNameButtons(layer, faces, alias); });
  }else layoutFaceNameButtons(layer, faces, alias);
  syncViewerTools();
}
function zoomTo(scale){viewer.fit=false;viewer.scale=Math.max(.05,Math.min(4,scale));updateZoom();}
async function displayPhoto(id){
  closeFaceActionPopover();togglePhotoPeoplePopover(false);
  const ticket=renderPhoto.ticket+1;
 setViewerLoading(true);
 if(viewer.loader){viewer.loader.onload=null;viewer.loader.onerror=null;viewer.loader.src='';}
 try{
  if(!await renderPhoto(id))return false;
 }catch(err){
  if(ticket===renderPhoto.ticket)viewerImageFailed(err.message||'照片资料读取失败');
  throw err;
 }
 if(ticket!==renderPhoto.ticket)return false;
 syncPhotoFavorite(state.detail);syncPhotoPlaceButton(state.detail);
 const root=(viewer.context?.directory||'').replace(/[\/]+$/,'').toLowerCase();
 const files=[...state.detail.files].sort((a,b)=>a.excluded-b.excluded||b.exists_now-a.exists_now||a.id-b.id);
 const scoped=files.find(f=>!root||f.path.toLowerCase().startsWith(root+String.fromCharCode(92))||f.path.toLowerCase()===root);
 if(scoped)$('#detail-name').textContent=basename(scoped.path);
 $('#detail-dialog').dataset.photoId=String(id);
 const draft=viewer.drafts.get(id);if(draft)for(const [key,value] of Object.entries(draft))$(key).value=value;
 const file=scoped||files[0];
 const src=viewerImageSrc(state.detail, file, id);
 const applySrc=async(url)=>{
  if(!$('#detail-dialog').open||state.detail?.id!==id||ticket!==renderPhoto.ticket)return false;
  const detailImage=$('#detail-img');
  const changing=Boolean(detailImage.getAttribute('src')&&!detailImage.hidden);
  viewer.fit=true;
  detailImage.classList.remove('viewer-photo-arriving','viewer-photo-forward','viewer-photo-backward');
  if(changing)void detailImage.offsetWidth;
  detailImage.src=url;
  detailImage.hidden=false;
  if(changing){
   detailImage.classList.add('viewer-photo-arriving',viewer.transitionDirection<0?'viewer-photo-backward':'viewer-photo-forward');
  }
  $('#photo-signature').hidden=false;
  $('#face-name-layer').hidden=false;
  renderSignature(state.detail,scoped||files[0]);
  updateZoom(true);
  renderFaceNames(state.detail);
  viewerMessage(draft?'这张照片有尚未保存的补录，已暂存在本页。':!state.detail.in_library?'已排除 · 缓存已清理':'');
  await waitViewerFrames(2);
  if(ticket===renderPhoto.ticket&&$('#detail-dialog').open&&state.detail?.id===id)setViewerLoading(false);
  return true;
 };
 updatePosition();
 const image=new Image();viewer.loader=image;
 await new Promise(resolve=>{
  image.onload=async()=>{try{if(typeof image.decode==='function')await image.decode();}catch(e){}await applySrc(image.src);resolve();};
  image.onerror=()=>{
   if(!src.includes('/api/preview/')){
    const fallback=new Image(); viewer.loader=fallback;
    fallback.onload=async()=>{try{if(typeof fallback.decode==='function')await fallback.decode();}catch(e){}await applySrc(fallback.src);resolve();};
    fallback.onerror=()=>{if(ticket===renderPhoto.ticket)viewerImageFailed('这张图暂时无法显示');resolve();};
    fallback.src='/api/preview/'+id+'?v='+state.thumbRevision;
    return;
   }
   if(ticket===renderPhoto.ticket)viewerImageFailed('这张图暂时无法显示');
   resolve();
  };
  image.src=src;
 });
 return ticket===renderPhoto.ticket;
}
async function loadViewerSequence(id,nextContext,generation){
 const params=new URLSearchParams({...nextContext,sequence:true,limit:String(viewer.window)});
 params.delete('position');
 const position=Number(nextContext&&nextContext.position);
 if(Number.isInteger(position)&&position>=0)params.set('offset',String(Math.max(0,position-Math.floor(viewer.window/2))));
 else params.set('around',String(id));
 let result=await api('/api/photos?'+params);
 if(generation!==viewer.generation)return false;
 if(!result.missing&&!result.ids?.includes(id)&&Number.isInteger(position)&&position>=0){
  params.delete('offset');params.set('around',String(id));
  result=await api('/api/photos?'+params);
  if(generation!==viewer.generation)return false;
 }
 if(result.missing||!result.ids?.includes(id)){viewerMessage('当前范围已变化，请刷新照片列表后再打开。');return false;}
 viewer.ids=result.ids||[];
 viewer.offset=result.offset||0;
 viewer.total=result.total||viewer.ids.length;
 viewer.maxId=result.max_id||nextContext.max_id;
 viewer.index=viewer.target=viewer.ids.indexOf(id);
 viewer.absolute=(viewer.offset||0)+viewer.index;
 viewer.goal=viewer.absolute;
 updatePosition();
 return true;
}
async function openPhoto(id,context=null){
 const wasOpen=$('#detail-dialog').open;
 if(!wasOpen){viewer.returnScroll=scrollY;viewer.openedPhotoId=null;viewer.openedAbsolute=null;viewer.openedContext=null;viewer.openedWaterfallGeneration=null;viewer.exit=null;clearViewerImage();}
 stopSlides();
 const alreadyOpen=wasOpen;
 const generation=++viewer.generation;
 viewer.goal=null; viewer.queued=null;
 if(!alreadyOpen){$('#detail-dialog').classList.add('hide-info');syncViewerTools();}
 const nextContext=context||currentBrowseContext();
 const sameContext=viewer.context&&sameBrowseContext(viewer.context,nextContext);
 const needsSequence=!sameContext||!viewer.ids.includes(id);
 let sequence=null;
 if(needsSequence){
  viewer.context=nextContext;
  const position=Number(nextContext&&nextContext.position);
  viewer.ids=[id];
  viewer.offset=Number.isInteger(position)&&position>=0?position:0;
  viewer.total=Math.max(1,Number(typeof waterfall==='object'&&waterfall.total)||1);
  viewer.maxId=nextContext.max_id;
  viewer.index=viewer.target=0;
  viewer.absolute=viewer.offset;
  viewer.goal=viewer.absolute;
  sequence=loadViewerSequence(id,nextContext,generation);
  viewer.sequencePromise=sequence;
  sequence.then(
   ()=>{if(viewer.sequencePromise===sequence)viewer.sequencePromise=null;},
   ()=>{if(viewer.sequencePromise===sequence)viewer.sequencePromise=null;}
  );
 }
 if(!viewer.ids.includes(id)){viewerMessage('当前范围已变化，请刷新照片列表后再打开。');return false;}
  viewer.index=viewer.target=viewer.ids.indexOf(id);
  viewer.absolute=(viewer.offset||0)+viewer.index;
  if(!alreadyOpen){
   viewer.openedPhotoId=Number(id);
   viewer.openedAbsolute=viewer.absolute;
   viewer.openedContext={...viewer.context};
   viewer.openedWaterfallGeneration=typeof waterfall==='object'?waterfall.generation:null;
  }
 viewer.goal=viewer.absolute;
 const displayed=await displayPhoto(id);
 if(sequence){
  try{await sequence;}catch(err){if(generation===viewer.generation)viewerMessage('照片已打开，连续浏览范围暂时没有载入。');}
 }
 if(generation===viewer.generation)$('#image-viewport').focus({preventScroll:true});
 return Boolean(displayed);
}
async function ensureViewerWindow(absolute, generation=viewer.generation){
 const total=viewer.total||viewer.ids.length;
 const next=Math.max(0,Math.min(Math.max(total-1,0),absolute));
 const local=next-(viewer.offset||0);
 if(local>=0&&local<viewer.ids.length){
  viewer.absolute=next;
  viewer.target=local;
  return local;
 }
 const ctx={...viewer.context, max_id: viewer.maxId || viewer.context.max_id};
 const params=new URLSearchParams({...ctx,sequence:true,limit:String(viewer.window),offset:String(Math.max(0,next-Math.floor(viewer.window/2)))});
 const result=await api('/api/photos?'+params);
 if(generation!==viewer.generation)return null;
 viewer.ids=result.ids||[]; viewer.offset=result.offset||0; viewer.total=result.total||viewer.ids.length; viewer.maxId=result.max_id||viewer.maxId;
 const idx=Math.max(0, Math.min(viewer.ids.length-1, next-viewer.offset));
 viewer.absolute=next;
 viewer.index=viewer.target=idx;
 return idx;
}
async function movePhoto(delta,automatic=false){
 if(!automatic)stopSlides();
 if(viewer.sequencePromise){try{await viewer.sequencePromise;}catch(e){}}
 const total=viewer.total||viewer.ids.length;
 const current=Number.isFinite(viewer.goal)?viewer.goal:(Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.target||0));
 const goal=Math.max(0,Math.min(Math.max(total-1,0),current+delta));
 viewer.goal=goal;
 if(goal===current && delta!==0){viewerMessage(goal===0?'已经是当前范围的第一张。':'已经是当前范围的最后一张。');stopSlides();return;}
 if(goal===current){stopSlides();return;}
 viewer.transitionDirection=Math.sign(delta);
 await consumeViewerGoal(automatic);
}
async function consumeViewerGoal(automatic=false){
 if(viewer.busy)return;
 viewer.busy=true;
 try{
  while($('#detail-dialog').open){
   const generation=viewer.generation;
   const want=Number.isFinite(viewer.goal)?viewer.goal:(Number.isFinite(viewer.absolute)?viewer.absolute:0);
   const local=await ensureViewerWindow(want, generation);
   if(!$('#detail-dialog').open)return;
   if(generation!==viewer.generation){continue;}
   if(local==null){if(generation!==viewer.generation) continue;return;}
   viewer.absolute=want;
   viewer.index=viewer.target=want-(viewer.offset||0);
   const id=viewer.ids[viewer.index];
   if(id==null){viewerMessage('当前范围已变化，请刷新照片列表后再打开。');return;}
   await displayPhoto(id);
   if(!$('#detail-dialog').open)return;
   if(generation!==viewer.generation) continue;
   if(!Number.isFinite(viewer.goal) || viewer.goal===want){
    if(automatic&&viewer.playing&&generation===viewer.generation&&$('#detail-dialog').open){
     if((viewer.absolute||0)>=(viewer.total||1)-1){stopSlides();viewerMessage('已经播放到当前范围的最后一张。');}
     else scheduleSlide();
    }
    break;
   }
  }
 }catch(e){stopSlides();throw e;}
 finally{
  viewer.busy=false;updatePosition();
  if($('#detail-dialog').open && Number.isFinite(viewer.goal) && viewer.goal!==viewer.absolute){consumeViewerGoal(automatic);}
 }
}

buildViewerToolbar();
applyFaceStyle();
syncViewerTools();
document.addEventListener('click',e=>{
 const close=e.target.closest&&e.target.closest('[data-close="detail-dialog"]');
 if(!close)return;
 e.preventDefault();e.stopPropagation();closePhotoViewer();
},true);

$('#viewer-prev').addEventListener('click',action(()=>movePhoto(-1)));
$('#viewer-next').addEventListener('click',action(()=>movePhoto(1)));
$('#zoom-in').addEventListener('click',()=>zoomTo(viewer.scale*1.25));
$('#zoom-out').addEventListener('click',()=>zoomTo(viewer.scale/1.25));
$('#zoom-fit').addEventListener('click',()=>{viewer.fit=true;updateZoom(true);});
$('#zoom-actual').addEventListener('click',()=>zoomTo(1));
$('#detail-img').addEventListener('load',()=>{updateSignaturePalette();updateZoom(true);renderFaceNames(state.detail);});
const faceLayer=$('#face-name-layer');
faceLayer&&faceLayer.addEventListener('pointerdown',e=>{if(e.target.closest('[data-face-person]')){e.stopPropagation();drag=null;}});
faceLayer&&faceLayer.addEventListener('pointerover',e=>{const button=e.target.closest('.face-name');if(button)showFaceGuide(button);});
faceLayer&&faceLayer.addEventListener('pointerout',e=>{const button=e.target.closest('.face-name');if(button&&!button.contains(e.relatedTarget))hideFaceGuide();});
faceLayer&&faceLayer.addEventListener('focusin',e=>{const button=e.target.closest('.face-name');if(button)showFaceGuide(button);});
faceLayer&&faceLayer.addEventListener('focusout',e=>{const button=e.target.closest('.face-name');if(button&&!button.contains(e.relatedTarget))hideFaceGuide();});
faceLayer&&faceLayer.addEventListener('click',e=>{
 const b=e.target.closest('[data-face-person]');
 if(!b)return;
 e.preventDefault();
 e.stopPropagation();
 outsidePhotoDown=false;
 const face=faceForLabel(b);
 if(faceHasUsableName(face))openFaceActionPopover(b,face);
 else if(typeof openQuickName==='function')openQuickName(Number(b.dataset.facePerson));
});
$('#toggle-face-names').addEventListener('click',()=>{viewer.faceNames=!viewer.faceNames;saveViewerPrefs();renderFaceNames(state.detail);});
$('#toggle-face-alias').addEventListener('click',()=>{viewer.faceAlias=!viewer.faceAlias;saveViewerPrefs();renderFaceNames(state.detail);});
$('#toggle-face-dir').addEventListener('click',()=>{viewer.faceVertical=!viewer.faceVertical;saveViewerPrefs();renderFaceNames(state.detail);if(!$('#face-style-popover')?.hidden)applyFaceStyle();});
$('#face-style-button')?.addEventListener('click',e=>{e.stopPropagation();toggleFaceStylePopover();});
$('#photo-people-manage')?.addEventListener('click',e=>{e.stopPropagation();togglePhotoPeoplePopover();});
document.addEventListener('click',e=>{
 if(!e.target.closest('#face-action-popover,.face-name'))closeFaceActionPopover();
 if(!e.target.closest('#photo-people-popover,#photo-people-manage'))togglePhotoPeoplePopover(false);
 if(!e.target.closest('#face-style-popover,#face-style-button,#local-font-dialog'))toggleFaceStylePopover(false);
});
$('#detail-img').addEventListener('dblclick',()=>{if(viewer.fit)zoomTo(1);else{viewer.fit=true;updateZoom(true);}});
function toggleInfo(){$('#detail-dialog').classList.toggle('hide-info');syncViewerTools();}
$('#viewer-info').addEventListener('click',toggleInfo);
$('#close-info').addEventListener('click',toggleInfo);
$('#viewer-play').addEventListener('click',()=>{if(viewer.playing){stopSlides();syncViewerTools();return;}const current=Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.target||0);if(current>=(viewer.total||viewer.ids.length)-1){viewerMessage('已经是最后一张，请先返回前面的照片。');return;}viewer.playing=true;syncViewerTools();scheduleSlide();});
$('#slide-delay').addEventListener('change',()=>{saveViewerPrefs();if(viewer.timer)scheduleSlide();});
$('#detail-dialog').addEventListener('close',()=>{viewer.exit={photoId:Number(state.detail?.id)||0,absolute:Number(viewer.absolute),context:viewer.context?{...viewer.context}:null,fallbackScroll:viewer.returnScroll,openedPhotoId:Number(viewer.openedPhotoId)||0,openedAbsolute:Number(viewer.openedAbsolute),openedContext:viewer.openedContext?{...viewer.openedContext}:null,waterfallGeneration:viewer.openedWaterfallGeneration};clearTimeout(viewer.closingTimer);viewer.closingTimer=null;$('#detail-dialog').classList.remove('viewer-closing');stopSlides();toggleFaceStylePopover(false);togglePhotoPeoplePopover(false);closeFaceActionPopover();viewer.lastPasserbyBatch=null;viewer.generation++;viewer.queued=null;viewer.goal=null;viewer.sequencePromise=null;renderPhoto.ticket++;if(viewer.loader){viewer.loader.onload=null;viewer.loader.onerror=null;viewer.loader.src='';}if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});});
$('#detail-dialog').addEventListener('cancel',e=>{e.preventDefault();closePhotoViewer();});
document.addEventListener('visibilitychange',()=>{if(document.hidden)stopSlides();});
document.addEventListener('keydown',action(async e=>{
 if(!$('#detail-dialog').open||$$('dialog[open]').at(-1)?.id!=='detail-dialog'||e.target.closest('input,textarea,select,[contenteditable="true"]')||e.ctrlKey||e.altKey||e.metaKey)return;
 if(e.key==='Escape'&&(!$('#face-action-popover')?.hidden||!$('#photo-people-popover')?.hidden||!$('#face-style-popover')?.hidden)){e.preventDefault();closeFaceActionPopover();togglePhotoPeoplePopover(false);toggleFaceStylePopover(false);return;}
 const directions={ArrowLeft:-1,ArrowUp:-1,ArrowRight:1,ArrowDown:1};
 if(e.key in directions){e.preventDefault();await movePhoto(directions[e.key]);}
 else if(e.key==='Home'||e.key==='End'){e.preventDefault();const total=viewer.total||viewer.ids.length;const current=Number.isFinite(viewer.absolute)?viewer.absolute:(viewer.offset||0)+(viewer.target||0);await movePhoto(e.key==='Home'?-current:total-1-current);}
}));
let drag=null;const viewport=$('#image-viewport');
viewport.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('.face-name,button,a,input,textarea,select'))return;drag={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(e.pointerId);});
viewport.addEventListener('pointermove',e=>{if(drag){viewport.scrollLeft=drag.left+drag.x-e.clientX;viewport.scrollTop=drag.top+drag.y-e.clientY;}});
viewport.addEventListener('pointerup',()=>drag=null);viewport.addEventListener('pointercancel',()=>drag=null);
new ResizeObserver(()=>{if($('#detail-dialog').open)updateZoom();}).observe(viewport);
// Remember where a gesture began: dragging a zoomed photo onto the background
// must not close it. Buttons, links and the detail drawer keep their own actions.
let outsidePhotoDown=false;
 const keepOpenSelector='#detail-img,button,a,input,textarea,select,.detail-info,.face-name-layer,.face-name,.viewer-tools,.viewer-arrow,.dialog-close,#photo-mat,#photo-signature,.face-style-popover,.people-manage-popover,.face-action-popover';
$('#detail-dialog').addEventListener('pointerdown',e=>{
 outsidePhotoDown=e.button===0&&!e.target.closest(keepOpenSelector);
});
$('#detail-dialog').addEventListener('click',e=>{
 if(!outsidePhotoDown||e.target.closest(keepOpenSelector))return;
 outsidePhotoDown=false;closePhotoViewer();
});
$('#detail-dialog').addEventListener('close',()=>outsidePhotoDown=false);
$('#detail-form').addEventListener('input',()=>{stopSlides();viewer.drafts.set(state.detail.id,Object.fromEntries(['#edit-date','#edit-precision','#edit-place','#edit-notes'].map(k=>[k,$(k).value])));viewerMessage('补录尚未保存；翻图时会暂存在本页，刷新页面会丢失。');});
$('#sort-order').addEventListener('change',action(async()=>{state.sort=$('#sort-order').value;state.offset=0;await loadPhotos();}));
$('#refresh-photos').addEventListener('click',action(()=>loadPhotos()));
$('#browse-directory').addEventListener('click',action(()=>{state.folderTarget='browse';return openFolder(state.directory);}));
