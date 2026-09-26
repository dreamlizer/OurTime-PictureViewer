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
const signatureInfo={sections:[],text:'',openTimer:null,closeTimer:null,copyTimer:null};
const faceLabels=window.OurTimeFaceLabels||{};
const FACE_LABEL_PLATES=faceLabels.PLATES||{
  ivory:{vertical:'/api/face-label-bg/1.png',horizontal:'/api/face-label-bg/7.png'},
  tea:{vertical:'/api/face-label-bg/4.png',horizontal:'/api/face-label-bg/8.png'}
};
const faceLabelThemeReadiness=new Map();
const faceLabelEffectiveThemes=new Map();
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
const FACE_STYLE_PRESETS=faceLabels.PRESETS||{
  classic:{
    theme:'classic',
    fontSize:13,
    fontFamily:'kai',
    textColor:'#f6f1e6',
    backgroundColor:'#141812',
    backgroundOpacity:.5,
    radius:10,
    paddingX:3,
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
    backgroundOpacity:.66,
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
// Independent denominators: smaller label area versus the covered face area.
const FACE_LABEL_OVERLAP_LIMIT=.05;
const FACE_LABEL_FACE_OVERLAP_LIMIT=.35;
const FACE_LABEL_BASE_HEIGHT=56;
const FACE_LABEL_FACE_RATIO=.4;
const FACE_LABEL_MAX_SCALE=1.6;
const FACE_LABEL_MIN_SCALE=1;
const FACE_LABEL_MIN_PHOTO_SCALE=.12;
const FACE_LABEL_REFERENCE_PHOTO_HEIGHT=900;
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
// Start legacy installations in Chinese-first auto mode once; subsequent
// explicit direction choices remain persistent and independent of positions.
function migratedViewerPrefs(raw=storedViewerPrefs){
  return faceLabels.normalizeViewerPrefs?faceLabels.normalizeViewerPrefs(raw):raw;
}
const normalizedViewerPrefs=migratedViewerPrefs();
function initialFaceDirMode(){ return normalizedViewerPrefs.faceDirMode||'auto'; }
function initialFaceStyle(){ return {...(normalizedViewerPrefs.faceStyle||DEFAULT_FACE_STYLE)}; }
function viewerCaptureEntityWrite(kind,id,payload,revision){
  return window.__ourTimeApp.captureEntityWrite(kind,id,payload,revision);
}
function viewerWriteCurrent(write){
  return window.__ourTimeApp.entitySessions.current(write.entitySession)
    && Number(state.detail&&state.detail.id)===Number(write.entityId);
}
const viewer={
  ids:[],index:0,target:0,offset:0,total:0,context:null,generation:0,busy:false,
  timer:null,playing:false,scale:1,fit:true,loader:null,loaderCancel:null,presentation:0,loadingToken:0,drafts:new Map(),window:200,
  returnScroll:undefined,openedPhotoId:null,openedAbsolute:null,openedContext:null,openedWaterfallGeneration:null,exit:null,
  sequencePromise:null,loadingTimer:null,transitionDirection:0,closingTimer:null,
  faceNames:normalizedViewerPrefs.faceNames!==false,
  faceAliasMode:normalizedViewerPrefs.faceAliasMode||'off',
  faceDirMode:initialFaceDirMode(),
  faceAlias:Boolean(normalizedViewerPrefs.faceAlias),
  faceVertical:Boolean(normalizedViewerPrefs.faceVertical),
  faceLabelPosition:normalizedViewerPrefs.faceLabelPosition||'auto',
  faceUnnamedMarker:normalizedViewerPrefs.faceUnnamedMarker||'plus',
  faceStyle:initialFaceStyle(),
  faceThemeProbe:0,
  faceRenderGeneration:0
};
function saveViewerPrefs(){
  try{
    const current={
      faceNames:viewer.faceNames!==false,
      faceAliasMode:viewer.faceAliasMode||'off',
      faceDirMode:viewer.faceDirMode||'auto',
      faceDirectionVersion:1,
      faceAlias:viewer.faceAliasMode==='with'||viewer.faceAliasMode==='only',
      faceVertical:viewer.faceDirMode!=='horizontal',
      faceLabelPosition:viewer.faceLabelPosition,
      faceUnnamedMarker:viewer.faceUnnamedMarker,
      faceStyle:viewer.faceStyle,
      slideDelay:Number($('#slide-delay')?.value||normalizedViewerPrefs.slideDelay||5)
    };
    const normalized=migratedViewerPrefs(current);
    localStorage.setItem(VIEWER_PREFS_KEY,JSON.stringify({
      ...current,
      faceAliasMode:normalized.faceAliasMode,
      faceDirMode:normalized.faceDirMode,
      faceStyle:normalized.faceStyle,
      faceUnnamedMarker:normalized.faceUnnamedMarker
    }));
  }catch(e){viewer.preferenceWarning='外观设置暂时无法保存，刷新后可能恢复';}
}
function injectViewerStylesheet(href, key){
  if(document.querySelector('link[data-viewer-style="'+key+'"]'))return;
  const link=document.createElement('link');
  link.rel='stylesheet';
  link.href=href;
  link.dataset.viewerStyle=key;
  document.head.appendChild(link);
}
function injectViewerOverrideCss(){
  injectViewerStylesheet('/viewer-overrides.css','overrides');
  injectViewerStylesheet('/face-labels.css','face-labels');
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
    const saved=Number(normalizedViewerPrefs.slideDelay);
    if([3,5,10].includes(saved))delay.value=String(saved);
    delay.title='幻灯片切换间隔';
  }
  buildFaceStylePopover();
  buildPhotoPeoplePopover();
  buildPhotoPeopleHud();
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
          <small id="face-style-preview-caption">基础比例预览；照片中随显示尺寸缩放</small>
    </div>
    <div class="face-style-group">
      <div class="face-style-group-title">样式</div>
      <div class="face-theme-choices" role="radiogroup" aria-label="人名标签主题">
        <button type="button" data-face-theme-choice="classic" aria-pressed="false"><i>名</i><span>默认</span></button>
        <button type="button" data-face-theme-choice="ivory" aria-pressed="false"><i>笺</i><span>素笺</span></button>
        <button type="button" data-face-theme-choice="tea" aria-pressed="false"><i>墨</i><span>茶棕</span></button>
        <button type="button" data-face-theme-choice="accent" aria-pressed="false"><i>朱</i><span>暗朱</span></button>
      </div>
      <select id="face-theme" hidden aria-hidden="true" tabindex="-1"><option value="classic">默认</option><option value="ivory">素笺</option><option value="tea">茶棕</option><option value="accent">暗朱</option></select>
      <label class="face-style-field face-custom-control face-range-field"><span>字号</span><output id="face-font-size-value"></output><input id="face-font-size" type="range" min="10" max="22" step="1"></label>
      <label class="face-style-field face-custom-control"><span>字体</span><select id="face-font-family"><option value="sans">黑体 / 无衬线</option><option value="serif">宋体 / 衬线</option><option value="kai">楷体</option><option value="fangsong">仿宋</option><option value="other">其他…</option></select></label>
    </div>
    <div class="face-style-group">
      <div class="face-style-group-title">布局</div>
      <div class="face-dir-choices" role="radiogroup" aria-label="文字方向">
        <button type="button" data-face-dir-choice="auto" aria-pressed="false">自动</button>
        <button type="button" data-face-dir-choice="horizontal" aria-pressed="false">横排</button>
        <button type="button" data-face-dir-choice="vertical" aria-pressed="false">竖排</button>
      </div>
      <label class="face-style-field"><span>标签排版</span><select id="face-label-position"><option value="auto">智能排布（默认）</option><option value="manual" disabled>自定义排版</option><option value="left">偏左</option><option value="right">偏右</option><option value="top">偏上</option><option value="bottom">偏下</option></select></label>
      <label class="face-style-field"><span>待命名标记</span><select id="face-unnamed-marker"><option value="plus">默认加号</option><option value="pulse">呼吸绿点</option><option value="ring">静态绿环</option></select></label>
      <button type="button" id="face-label-reset-photo" class="face-label-reset-photo">清除本张自定义排版</button>
      <p id="face-label-layout-status" class="muted" role="status" hidden></p>
    </div>
    <div class="face-style-group">
      <div class="face-style-group-title">外观</div>
      <div class="face-style-colors"><label><span>文字</span><input id="face-text-color" type="color" aria-label="文字颜色"></label><label><span>底色</span><input id="face-bg-color" type="color" aria-label="底色"></label></div>
      <label class="face-style-field face-custom-control face-range-field"><span>底色透明度</span><output id="face-bg-opacity-value"></output><input id="face-bg-opacity" type="range" min="0" max="90" step="1"></label>
      <label class="face-style-field face-custom-control"><span>圆角</span><select id="face-radius"><option value="0">直角</option><option value="4">微圆角</option><option value="10">圆角</option><option value="999">胶囊</option></select></label>
      <label class="face-style-field face-shadow-row"><input id="face-shadow" type="checkbox"><span>文字阴影</span><small>亮背景下更清楚</small></label>
    </div>
    <button type="button" id="face-style-reset" class="face-style-reset">恢复外观默认</button>
    <p id="face-style-status" class="muted" role="status" hidden></p>`;
  body.appendChild(pop);

  const bind=(id,event,fn)=>{const el=$(id);if(el)el.addEventListener(event,fn);};
  bind('#face-style-close','click',()=>toggleFaceStylePopover(false));
  bind('#face-theme','change',e=>applyFacePreset(e.target.value));
  pop.querySelectorAll('[data-face-theme-choice]').forEach(button=>{
    button.addEventListener('click',()=>applyFacePreset(button.dataset.faceThemeChoice));
  });
  pop.querySelectorAll('[data-face-dir-choice]').forEach(button=>{
    button.addEventListener('click',()=>setFaceDirMode(button.dataset.faceDirChoice));
  });
  bind('#face-font-size','input',e=>updateFaceStyle({fontSize:Number(e.target.value)}));
  bind('#face-font-family','change',e=>{
    if(e.target.value==='other'){
      syncFaceFontSelect(e.target,viewer.faceStyle||DEFAULT_FACE_STYLE);
      openLocalFontDialog();
      return;
    }
    updateFaceStyle({fontFamily:e.target.value});
  });
  bind('#face-label-position','change',e=>{void selectFaceLabelPosition(e.target.value);});
  bind('#face-label-reset-photo','click',()=>resetCurrentPhotoFaceLabels());
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
  bind('#face-style-reset','click',()=>restoreFaceAppearanceDefaults());
  buildLocalFontDialog();
  applyFaceStyle();
}
function faceLabelIdentity(face){
  const stateInfo=faceLabelState(face);
  const rawPersonId=face?.person_id;
  const personId=rawPersonId==null||rawPersonId===''?NaN:Number(rawPersonId);
  return {
    state:stateInfo,
    personKey:Number.isInteger(personId)&&personId>0?('person:'+personId):('face:'+(Number(face?.id)||stateInfo.faceId||'unknown'))
  };
}
function faceHasUsableName(face){
  return faceLabelState(face).isNamed;
}
function photoPeopleSummary(photo=state.detail){
  const unique=new Map();
  for(const face of photo?.faces||[]){
    const item=faceLabelIdentity(face);
    if(!unique.has(item.personKey))unique.set(item.personKey,item.state);
  }
  const values=[...unique.values()];
  return {
    named:values.filter(item=>item.kind==='named').length,
    pending:values.filter(item=>item.kind==='pending').length,
    passerby:values.filter(item=>item.kind==='passerby').length
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
function faceKind(face){
  return faceLabelState(face).kind;
}
function buildPhotoPeopleHud(){
  if($('#photo-people-hud'))return;
  const mat=$('#photo-mat');if(!mat)return;
  const hud=document.createElement('div');
  hud.id='photo-people-hud';
  hud.className='photo-people-hud';
  hud.hidden=true;
  hud.setAttribute('aria-label','本照片人物概况');
  hud.innerHTML=`<span data-kind="named" tabindex="0"><b id="hud-named-count">0</b> 已命名</span><span data-kind="pending" tabindex="0"><b id="hud-pending-count">0</b> 待处理</span><span data-kind="passerby" tabindex="0"><b id="hud-passerby-count">0</b> 路人</span>`;
  mat.appendChild(hud);
  hud.addEventListener('pointerover',e=>{
    const chip=e.target.closest('[data-kind]');
    setFaceKindHighlight(chip?chip.dataset.kind:null);
  });
  hud.addEventListener('pointerleave',()=>setFaceKindHighlight(null));
  hud.addEventListener('focusin',e=>{
    const chip=e.target.closest('[data-kind]');
    setFaceKindHighlight(chip?chip.dataset.kind:null);
  });
  hud.addEventListener('focusout',e=>{
    if(!hud.contains(e.relatedTarget))setFaceKindHighlight(null);
  });
}
function setFaceKindHighlight(kind){
  const layer=$('#face-name-layer');
  const hud=$('#photo-people-hud');
  const next=kind||'';
  if(hud){
    hud.dataset.litKind=next;
    hud.querySelectorAll('[data-kind]').forEach(chip=>chip.classList.toggle('is-lit',chip.dataset.kind===next));
  }
  if(!layer)return;
  if(next){
    layer.dataset.litKind=next;
    layer.hidden=false;
  }else{
    delete layer.dataset.litKind;
    layer.hidden=viewer.faceNames===false;
  }
}
function updatePhotoPeoplePopover(){
  const summary=photoPeopleSummary();
  const namedCount=$('#photo-named-count');
  const pendingCount=$('#photo-pending-count');
  const passerbyCount=$('#photo-passerby-count');
  if(namedCount)namedCount.textContent=String(summary.named);
  if(pendingCount)pendingCount.textContent=String(summary.pending);
  if(passerbyCount)passerbyCount.textContent=String(summary.passerby);
  if($('#hud-named-count'))$('#hud-named-count').textContent=String(summary.named);
  if($('#hud-pending-count'))$('#hud-pending-count').textContent=String(summary.pending);
  if($('#hud-passerby-count'))$('#hud-passerby-count').textContent=String(summary.passerby);
  const hud=$('#photo-people-hud');
  if(hud){
    const total=summary.named+summary.pending+summary.passerby;
    hud.hidden=!state.detail||!total;
  }
  const pop=$('#photo-people-popover');if(!pop)return;
  const start=$('#photo-passersby-start');
  if(start){start.disabled=!summary.pending;start.textContent=summary.pending?`将剩余 ${summary.pending} 位设为路人`:'没有待命名人物';}
  const batch=viewer.lastPasserbyBatch;
  const undo=$('#photo-passersby-undo');
  if(undo)undo.hidden=!(batch&&batch.assetId===Number(state.detail?.id)&&batch.personIds.length);
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
  pop.innerHTML=`<div class="face-action-head"><span>这张照片中的</span><b id="face-action-name"></b><button type="button" id="face-action-edit-name" aria-label="修改姓名" title="修改姓名"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="m4 14.7-.7 3 3-.7L15.8 7.5 13.5 5.2 4 14.7Z"/><path d="m12.3 6.4 2.3 2.3"/></svg></button></div><form id="face-action-rename" class="face-action-rename" hidden><label for="face-action-name-input">姓名</label><input id="face-action-name-input" maxlength="100" autocomplete="off"><button type="submit">保存</button><button type="button" id="face-action-rename-cancel">取消</button></form><button type="button" id="face-action-photos" class="face-action-primary">查看此人照片</button><button type="button" id="face-action-split">认错了 · 从此人物移出</button><button type="button" id="face-action-ignore">这张是路人</button><small>“认错了”和“路人”只处理这张照片中的这张脸。</small>`;
  dialog.appendChild(pop);
  $('#face-action-photos').addEventListener('click',action(async()=>{
    const faceId=Number(pop.dataset.faceId),face=(state.detail?.faces||[]).find(item=>Number(item.id)===faceId);if(!face)return;
    const personId=Number(face.person_id);if(!personId)return;
    rememberPersonLabel({id:personId,name:face.name,alias:face.alias||'',confirmed:1,ignored:0});state.person=String(personId);
    const legacy=$('#person-filter');if(legacy)legacy.value=state.person;
    const closed=dialog.open?new Promise(resolve=>dialog.addEventListener('close',resolve,{once:true})):Promise.resolve();
    closeFaceActionPopover();closePhotoViewer();await closed;await setView('timeline');
  }));
  $('#face-action-edit-name').addEventListener('click',e=>{
    e.preventDefault();e.stopPropagation();
    const form=$('#face-action-rename'),input=$('#face-action-name-input');
    form.hidden=!form.hidden;
    if(!form.hidden){input.value=$('#face-action-name').textContent.trim();requestAnimationFrame(()=>{input.focus();input.select();const rect=pop.getBoundingClientRect(),overflow=rect.bottom-(innerHeight-12);if(overflow>0)pop.style.top=Math.max(12,rect.top-overflow)+'px';});}
  });
  $('#face-action-rename-cancel').addEventListener('click',()=>{$('#face-action-rename').hidden=true;$('#face-action-edit-name').focus();});
  $('#face-action-rename').addEventListener('submit',action(async e=>{
    e.preventDefault();
    const faceId=Number(pop.dataset.faceId),face=(state.detail?.faces||[]).find(item=>Number(item.id)===faceId);if(!face)return;
    const name=$('#face-action-name-input').value.trim();if(!name)throw new Error('请输入姓名');
    const personId=Number(face.person_id),write=viewerCaptureEntityWrite('photo',Number(state.detail.id),{face_id:faceId,name,alias:String(face.alias||'')},0);
    const save=e.submitter||$('#face-action-rename button[type="submit"]');save.disabled=true;
    try{
      const result=await window.__ourTimeApp.queueEntityWrite('person',personId,()=>rememberPersonName(personId,write.payload.name,write.payload.alias,write));
      if(!viewerWriteCurrent(write))return;
      if(result?.cancelled)return;
      if(!result?.merged){$('#face-action-name').textContent=name;rememberPersonLabel({id:personId,name,alias:face.alias||'',confirmed:1,ignored:0});toast('姓名已修改');}
      closeFaceActionPopover();
    }catch(err){if(viewerWriteCurrent(write))throw err;}
    finally{if(viewerWriteCurrent(write))save.disabled=false;}
  }));
  $('#face-action-split').addEventListener('click',action(async()=>{
    const faceId=Number(pop.dataset.faceId),face=(state.detail?.faces||[]).find(item=>Number(item.id)===faceId);if(!face)return;
    const oldPerson=Number(face.person_id),oldName=String(face.name||'').trim()||'这个人物';
      const write=viewerCaptureEntityWrite('photo',Number(state.detail.id),{face_id:faceId},0);
      try{
        const result=await window.__ourTimeApp.queueEntityWrite('face',faceId,()=>window.__ourTimeApp.operationRequest(`/api/faces/${faceId}/split`,{method:'POST'},write.operationId));
        if(typeof refreshPersonInLocalState==='function')refreshPersonInLocalState(oldPerson).catch(()=>{});
        if(!viewerWriteCurrent(write))return;
        Object.assign(face,{person_id:Number(result.person_id),name:'',alias:'',confirmed:0,ignored:0});
        closeFaceActionPopover();renderFaceNames(state.detail);renderOpenPhotoPeople();
        toast(`已从“${oldName}”移出；只影响这张照片`);
      }catch(err){if(viewerWriteCurrent(write))throw err;}
  }));
  $('#face-action-ignore').addEventListener('click',action(async()=>{
    const faceId=Number(pop.dataset.faceId),face=(state.detail?.faces||[]).find(item=>Number(item.id)===faceId);if(!face)return;
    const oldPerson=Number(face.person_id),write=viewerCaptureEntityWrite('photo',Number(state.detail.id),{face_id:faceId,ignored:true},0);
    try{
      const result=await window.__ourTimeApp.queueEntityWrite('face',faceId,()=>window.__ourTimeApp.operationRequest(`/api/faces/${faceId}/ignore`,{method:'POST',body:JSON.stringify({ignored:true})},write.operationId));
      if(typeof refreshPersonInLocalState==='function')refreshPersonInLocalState(oldPerson).catch(()=>{});
      if(!viewerWriteCurrent(write))return;
      Object.assign(face,{person_id:Number(result.person_id),name:'路人',alias:'',confirmed:0,ignored:1});
      closeFaceActionPopover();renderFaceNames(state.detail);renderOpenPhotoPeople();toast('这张脸已标为路人');
    }catch(err){if(viewerWriteCurrent(write))throw err;}
  }));
}
function openFaceActionPopover(button,face){
  const pop=$('#face-action-popover');if(!pop)return;
  toggleFaceStylePopover(false);togglePhotoPeoplePopover(false);
  viewer.faceAction={faceId:Number(face.id),button};
  pop.dataset.faceId=String(face.id);$('#face-action-name').textContent=String(face.name||'').trim();$('#face-action-rename').hidden=true;pop.hidden=false;
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
  applyFaceStyle();
  saveViewerPrefs();
  if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function customFaceFontStack(family){
  const clean=String(family||'').replace(/[\u0000-\u001f\u007f]/g,'').trim().slice(0,200);
  return clean?`${JSON.stringify(clean)},"Microsoft YaHei UI","Microsoft YaHei",sans-serif`:faceFontStack('kai');
}
function faceFontStack(kind,customFamily){
  if(faceLabels.fontStack)return faceLabels.fontStack(kind,customFamily||viewer.faceStyle?.customFontFamily);
  if(kind==='custom')return customFaceFontStack(customFamily||viewer.faceStyle?.customFontFamily);
  return '"LXGW WenKai GB Screen","STKaiti","Kaiti SC","KaiTi",serif';
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
  const sample=typeof state!=='undefined'?namedFaces(state.detail).map(face=>faceLabelState(face)).find(item=>item.displayText&&item.displayText!=='+'):null;
  return sample?.displayText||'示例姓名';
}
function syncLocalFontPreviewAppearance(){
  const dialog=$('#local-font-dialog');
  const root=$('#detail-dialog');
  if(!dialog||!root)return;
  ['faceTheme','faceThemeRequested','faceThemeEffective','unnamedMarker'].forEach(name=>{
    if(root.dataset[name]!=null)dialog.dataset[name]=root.dataset[name];
  });
  [...root.style].filter(name=>name.startsWith('--face-')).forEach(name=>{
    dialog.style.setProperty(name, root.style.getPropertyValue(name));
  });
}
function publishFaceLabelTheme(requested, effective, status=null){
  const root=$('#detail-dialog')||document.documentElement;
  root.dataset.faceThemeEffective=effective;
  if(status?.complete||requested===effective)clearFaceLabelFallbackNote();
  else noteFaceLabelFallback(requested, status?.missing||[]);
  syncLocalFontPreviewAppearance();
  if(state.detail)renderFaceNames(state.detail);
}
function updateLocalFontPreview(){
  const family=localFontPicker.selected;
  const preview=$('#local-font-preview-label');
  if(preview){
    preview.textContent=localFontPreviewText();
    preview.style.fontFamily=customFaceFontStack(family);
    const previewVertical=faceLabelVerticalFor(preview.textContent);
    preview.classList.toggle('is-horizontal', !previewVertical);
  }
  syncLocalFontPreviewAppearance();
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
  return {count,long:count>=5};
}
function applyFaceLabelProfile(element,name){
  if(!element)return;
  const profile=faceLabelProfile(name);
  element.classList.toggle('long-name',profile.long);
}
function faceLabelPhotoScale(imageHeight){
  const height=Number(imageHeight);
  if(!(height>0))return 1;
  return height/FACE_LABEL_REFERENCE_PHOTO_HEIGHT;
}
function faceLabelScaleForHeight(faceH, imageHeight){
  const height=Number(faceH);
  const photo=faceLabelPhotoScale(imageHeight);
  const photoScale=Math.max(FACE_LABEL_MIN_PHOTO_SCALE, photo);
  if(!(height>0))return FACE_LABEL_MIN_SCALE*photoScale;
  const faceScale=Math.min(FACE_LABEL_MAX_SCALE, Math.max(FACE_LABEL_MIN_SCALE, FACE_LABEL_FACE_RATIO*height/FACE_LABEL_BASE_HEIGHT));
  return faceScale*photoScale;
}
function isLatinDisplayName(text){
  return faceLabels.isLatinDisplayName ? faceLabels.isLatinDisplayName(text) : false;
}
function faceDirMode(){
  const mode=viewer.faceDirMode||'auto';
  return (mode==='horizontal'||mode==='vertical'||mode==='auto')?mode:'auto';
}
function faceDirLabel(mode=faceDirMode()){
  return mode==='auto'?'自动（中文竖排、英文横排）':mode==='horizontal'?'横排':'竖排';
}
function setFaceDirMode(mode){
  const next=(mode==='horizontal'||mode==='vertical'||mode==='auto')?mode:'auto';
  const changed=faceDirMode()!==next;
  if(changed){
    viewer.faceDirMode=next;
    viewer.faceVertical=next!=='horizontal';
    saveViewerPrefs();
  }
  updateToolVisuals();
  if(changed)renderFaceNames(state.detail);
  applyFaceStyle();
}
function faceLabelsVertical(){
  const mode=faceDirMode();
  if(mode==='horizontal') return false;
  if(mode==='vertical') return true;
  return true;
}
function faceLabelVerticalFor(text){
  return faceLabels.faceLabelVerticalFor
    ? faceLabels.faceLabelVerticalFor(text, faceDirMode())
    : faceDirMode()!=='horizontal' && !isLatinDisplayName(text);
}
function faceLabelPlateUrls(theme){
  const plates=FACE_LABEL_PLATES[theme];
  return plates?[plates.vertical,plates.horizontal]:[];
}
function faceLabelResourceReady(url, force=false){
  const cached=faceLabelThemeReadiness.get(url);
  if(!force && cached)return cached;
  if(cached)faceLabelThemeReadiness.delete(url);
  const promise=new Promise(resolve=>{
    const image=new Image();
    const finish=ok=>{
      if(!ok)faceLabelThemeReadiness.delete(url);
      resolve(ok);
    };
    image.onload=()=>finish(Boolean(image.naturalWidth));
    image.onerror=()=>finish(false);
    image.src=url+(force?'?retry='+Date.now():'');
  });
  faceLabelThemeReadiness.set(url,promise);
  return promise;
}
async function faceLabelThemeStatus(theme, force=false){
  const urls=faceLabelPlateUrls(theme);
  if(!urls.length)return {complete:true,missing:[]};
  const results=await Promise.all(urls.map(url=>faceLabelResourceReady(url,force)));
  return {complete:results.every(Boolean),missing:urls.filter((url,index)=>!results[index])};
}
function noteFaceLabelFallback(theme,missing){
  const status=$('#face-style-status');
  if(!status)return;
  const names={ivory:'素笺',tea:'茶棕'};
  const label=names[theme]||'这个主题';
  status.hidden=false;
  status.replaceChildren();
  status.append(document.createTextNode(label+'底板暂不可用，正在显示默认外观。字号、字体和外观暂时不能调整；方向和排版仍可使用。原来的选择已保留。'));
  const retry=document.createElement('button');
  retry.type='button';
  retry.className='face-style-fallback-retry';
  retry.textContent='再试一次';
  retry.addEventListener('click',()=>ensureFaceLabelThemeAvailable(theme));
  status.append(retry);
}
function clearFaceLabelFallbackNote(){
  const status=$('#face-style-status');
  if(status){status.hidden=true;status.textContent='';}
}
function ensureFaceLabelThemeAvailable(theme){
  const request=++viewer.faceThemeProbe;
  if(!FACE_LABEL_PLATES[theme]){
    faceLabelEffectiveThemes.set(theme,theme);
    publishFaceLabelTheme(theme, theme);
    return;
  }
  faceLabelThemeStatus(theme).then(status=>{
    if(request!==viewer.faceThemeProbe||viewer.faceStyle?.theme!==theme)return;
    const effective=status.complete?theme:'classic';
    const previous=faceLabelEffectiveThemes.get(theme)||theme;
    faceLabelEffectiveThemes.set(theme,effective);
    if(previous!==effective)applyFaceStyle();
    else publishFaceLabelTheme(theme, effective, status);
  });
}
function restoreFaceAppearanceDefaults(){
  if(faceAppearanceLocked()){applyFaceStyle();return;}
  const defaults=faceLabels.appearanceDefaults?faceLabels.appearanceDefaults():{faceStyle:{...DEFAULT_FACE_STYLE},faceUnnamedMarker:'plus'};
  viewer.faceStyle={...defaults.faceStyle};
  viewer.faceUnnamedMarker=defaults.faceUnnamedMarker||'plus';
  applyFaceStyle();
  saveViewerPrefs();
  syncFaceLabelResetButton();
  if(viewer.faceNames!==false&&state.detail)renderFaceNames(state.detail);
}
function faceStyleForTheme(theme){
  const preset=FACE_STYLE_PRESETS[theme]||DEFAULT_FACE_STYLE;
  return {...preset};
}
function activeFaceTheme(){
  const requested=viewer.faceStyle?.theme||'classic';
  const effective=faceLabelEffectiveThemes.get(requested)||requested;
  return {requested,effective};
}
function faceAppearanceLocked(){
  const theme=activeFaceTheme();
  return theme.effective!==theme.requested;
}
function applyFaceStyle(){
  const root=$('#detail-dialog')||document.documentElement;
  const requested=Object.prototype.hasOwnProperty.call(FACE_STYLE_PRESETS,viewer.faceStyle?.theme)?viewer.faceStyle.theme:'classic';
  const effective=faceLabelEffectiveThemes.get(requested)||requested;
  const source=effective===requested?(viewer.faceStyle||DEFAULT_FACE_STYLE):faceStyleForTheme('classic');
  const s={...source,theme:requested};
  if(effective==='tea'&&!TEA_FACE_FONT_KEYS.has(s.fontFamily)){
    s.fontFamily=FACE_STYLE_PRESETS.tea.fontFamily;
    delete s.customFontFamily;
  }
  if(effective==='accent'&&!ACCENT_FACE_FONT_KEYS.has(s.fontFamily)){
    s.fontFamily=FACE_STYLE_PRESETS.accent.fontFamily;
    delete s.customFontFamily;
  }
  root.dataset.faceTheme=effective;
  root.dataset.faceThemeRequested=requested;
  root.dataset.unnamedMarker=FACE_UNNAMED_MARKERS.has(viewer.faceUnnamedMarker)?viewer.faceUnnamedMarker:'plus';
  root.dataset.faceThemeEffective=effective;
  const pop=$('#face-style-popover');
  if(pop)pop.dataset.faceTheme=effective;
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
  const themePlate=FACE_LABEL_PLATES[effective];
  if(themePlate){
    root.style.setProperty('--face-label-image-vertical',`url("${themePlate.vertical}")`);
    root.style.setProperty('--face-label-image-horizontal',`url("${themePlate.horizontal}")`);
    root.style.setProperty('--face-label-image',`url("${themePlate.vertical}")`);
  }else{
    root.style.removeProperty('--face-label-image');
    root.style.removeProperty('--face-label-image-vertical');
    root.style.removeProperty('--face-label-image-horizontal');
  }

  syncLocalFontPreviewAppearance();

  const themeSelect=$('#face-theme'),fontSize=$('#face-font-size'),family=$('#face-font-family'),position=$('#face-label-position'),marker=$('#face-unnamed-marker'),text=$('#face-text-color'),bg=$('#face-bg-color'),op=$('#face-bg-opacity'),radius=$('#face-radius'),shadow=$('#face-shadow');
  if(themeSelect)themeSelect.value=requested;
  document.querySelectorAll('[data-face-theme-choice]').forEach(button=>{
    const selected=button.dataset.faceThemeChoice===requested;
    const shown=faceLabelEffectiveThemes.get(requested)||requested;
    button.setAttribute('aria-pressed',String(selected));
    button.dataset.faceThemeState=selected?(shown===requested?'active':'fallback'):'idle';
  });
  if(fontSize){
    fontSize.min=themePlate?'12':'10';
    fontSize.max=themePlate?'18':'22';
    fontSize.value=String(s.fontSize);
  }
  syncFaceFontSelect(family,s,effective);
  if(position)position.value=currentFaceLabelMode();
  if(marker)marker.value=root.dataset.unnamedMarker;
  const direction=faceDirMode();
  pop?.querySelectorAll('[data-face-dir-choice]').forEach(button=>{
    const selected=button.dataset.faceDirChoice===direction;
    button.setAttribute('aria-pressed',String(selected));
    button.title=selected?faceDirLabel(direction):'改为'+faceDirLabel(button.dataset.faceDirChoice);
  });
  if(text)text.value=s.textColor;
  if(bg)bg.value=s.backgroundColor;
  if(op)op.value=String(Math.round(s.backgroundOpacity*100));
  if(radius)radius.value=String(s.radius);
  if(shadow)shadow.checked=!!s.shadow;
  const imageTheme=Boolean(themePlate);
  const appearanceLocked=effective!==requested;
  const appearanceLockTitle='底板暂不可用，当前显示默认外观，这项暂不能调整';
  const lockedControls=new Set([text,bg,radius,shadow]);
  [fontSize,family,text,bg,op,radius,shadow].forEach(control=>{
    if(!control)return;
    const themeLocked=imageTheme&&(lockedControls.has(control)||(control===family&&effective!=='tea'));
    control.disabled=appearanceLocked||themeLocked;
    if(control!==family){
      if(appearanceLocked)control.title=appearanceLockTitle;
      else control.removeAttribute('title');
    }
  });
  const resetAppearance=$('#face-style-reset');
  if(resetAppearance){
    resetAppearance.disabled=appearanceLocked;
    if(appearanceLocked)resetAppearance.title=appearanceLockTitle;
    else resetAppearance.removeAttribute('title');
  }
  if(pop){
    [fontSize,family,op,radius].forEach(control=>{
      control?.closest('label')?.classList.toggle('is-disabled',Boolean(control.disabled));
    });
    pop.querySelector('.face-style-colors')?.classList.toggle('is-disabled',appearanceLocked||imageTheme);
    pop.querySelector('.face-shadow-row')?.classList.toggle('is-disabled',appearanceLocked||imageTheme);
  }
  if($('#face-font-size-value'))$('#face-font-size-value').textContent=`${s.fontSize}px`;
  if($('#face-bg-opacity-value'))$('#face-bg-opacity-value').textContent=`${Math.round(s.backgroundOpacity*100)}%`;
  if(family){
    family.title=appearanceLocked
      ?appearanceLockTitle
      :effective==='tea'
      ?'茶棕主题：三款本地毛笔字体'
      :effective==='accent'
        ?'暗朱主题：三款本地题签字体'
        :family.disabled?'由当前主题固定':'可选择本机其他字体';
  }
  if($('#face-style-preview-caption')){
    const themeName={classic:'默认',ivory:'素笺',tea:'茶棕',accent:'暗朱'}[effective]||'默认';
    const rawFont=(family&&family.selectedOptions&&family.selectedOptions[0]?family.selectedOptions[0].textContent:'')||'';
    const fontName=rawFont.replace(/（默认）/g,'').split('·')[0].trim();
    $('#face-style-preview-caption').textContent=themeName+(fontName?' · '+fontName:'')+' · 基础比例预览；照片中随显示尺寸缩放';
  }
  const preview=$('#face-style-preview-label');
  if(preview){
    const sample=typeof state!=='undefined'?namedFaces(state.detail).map(face=>faceLabelState(face)).find(item=>item.displayText&&item.displayText!=='+'):null;
    preview.textContent=sample&&sample.displayText?sample.displayText:'示例姓名';
    applyFaceLabelProfile(preview,preview.textContent);
    preview.style.fontSize='';
    preview.style.fontFamily='';
    preview.style.color='';
    preview.style.backgroundColor='';
    preview.style.borderRadius='';
    preview.style.textShadow='';
    const previewVertical=faceLabelVerticalFor(preview.textContent);
    const localPreview=$('#local-font-preview-label');
    if(localPreview){
      localPreview.textContent=localFontPreviewText();
      localPreview.style.fontFamily=localFontPicker.selected?customFaceFontStack(localFontPicker.selected):'';
      localPreview.classList.toggle('is-horizontal', !faceLabelVerticalFor(localPreview.textContent));
    }
    preview.dataset.faceDirection=previewVertical?'vertical':'horizontal';
    preview.classList.toggle('is-horizontal', !previewVertical);
  }
  ensureFaceLabelThemeAvailable(requested);
}
function hexToRgba(hex,alpha){
  const h=String(hex||'#000000').replace('#','');
  const full=h.length===3?h.split('').map(x=>x+x).join(''):h.padEnd(6,'0').slice(0,6);
  const n=parseInt(full,16);if(!Number.isFinite(n))return `rgba(0,0,0,${alpha})`;
  return `rgba(${(n>>16)&255},${(n>>8)&255},${n&255},${alpha})`;
}
function updateFaceStyle(patch){
  if(faceAppearanceLocked()){applyFaceStyle();return;}
  viewer.faceStyle={...viewer.faceStyle,...patch};applyFaceStyle();saveViewerPrefs();
  if(state.detail)requestAnimationFrame(()=>renderFaceNames(state.detail));
}
function toggleFaceStylePopover(force){
  const pop=$('#face-style-popover'),btn=$('#face-style-button');if(!pop)return;
  const open=force==null?pop.hidden:!!force;
  if(open){togglePhotoPeoplePopover(false);closeFaceActionPopover();}
  pop.hidden=!open;if(btn)btn.setAttribute('aria-expanded',String(open));
  if(open){applyFaceStyle();syncFaceLabelResetButton();}
}
function updateToolVisuals(){
  const dir=$('#toggle-face-dir');
  if(dir){
    const vertical=faceLabelsVertical();
    const mode=faceDirMode();
    dir.innerHTML=mode==='horizontal'?iconSvg.horizontal:iconSvg.vertical;
    const next=faceDirLabel(mode);
    dir.title=next;dir.setAttribute('aria-label',next); dir.dataset.dirMode=mode;
  }
  const play=$('#viewer-play');
  if(play){
    play.innerHTML=viewer.playing?iconSvg.pause:iconSvg.play;
    const label=viewer.playing?'暂停幻灯片':'播放幻灯片';play.title=label;play.setAttribute('aria-label',label);
  }
}

function currentBrowseContext(){const mapFilter=state.placeMapFilter;const viewFilter=(state.view==='home'&&!state.homeSnapshotId)?'all':state.view;return {q:state.q,filter:mapFilter?'all':(state.homeSnapshotId?'all':viewFilter),person:state.person,directory:state.directory,sort:state.sort,date_from:state.dateFrom||'',date_to:state.dateTo||'',place:state.place||'',...(state.homeSnapshotId?{recommendation_snapshot:state.homeSnapshotId}:{}),...(mapFilter?{map_cell:mapFilter.cell,map_lat_bucket:mapFilter.lat_bucket,map_lng_bucket:mapFilter.lng_bucket,...('map_west' in mapFilter?{map_west:mapFilter.map_west,map_south:mapFilter.map_south,map_east:mapFilter.map_east,map_north:mapFilter.map_north}:{})}:{}),max_id:state.maxId};}
function browseContextKey(context={}){
 const filter=String(context.filter||'all');
 return JSON.stringify({q:String(context.q||''),filter:filter==='all'?'timeline':filter,person:String(context.person||''),directory:String(context.directory||''),sort:String(context.sort||'date_desc'),date_from:String(context.date_from||''),date_to:String(context.date_to||''),place:String(context.place||''),map_cell:Number(context.map_cell||0),map_lat_bucket:Number(context.map_lat_bucket||0),map_lng_bucket:Number(context.map_lng_bucket||0),map_west:Number(context.map_west||0),map_south:Number(context.map_south||0),map_east:Number(context.map_east||0),map_north:Number(context.map_north||0),nearby:Number(context.nearby||0),radius_m:Number(context.radius_m||0),max_id:Number(context.max_id||0),recommendation_snapshot:String(context.recommendation_snapshot||'')});
}
function sameBrowseContext(a,b){return browseContextKey(a)===browseContextKey(b);}
function contextLabel(c){const person=state.people.find(p=>String(p.id)===c.person);const filter=c.filter||'';const heading=c.nearby?`当前位置 ${Number(c.radius_m)||100} 米内`:c.map_cell?'地图定位点':c.person?(person?.name||'人物 '+c.person):filter.startsWith('year:')?(filter.slice(5)==='unknown'?'时间未记录':filter.slice(5)+' 年'):filter.startsWith('group:')?groupTitle(filter)[1]:filter.startsWith('place:')?(filter.slice(6)==='unknown'?'地点未记录':filter.slice(6)):titles[filter]?.[0]||'照片';return [heading,c.directory?basename(c.directory):'',c.place?prettyPlace(c.place):'',c.date_from||c.date_to?((c.date_from||'')+(c.date_to&&c.date_to!==c.date_from?' ~ '+c.date_to:'')):'',c.q?'搜索：'+c.q:''].filter(Boolean).join(' · ');}
function viewerMessage(text){$('#viewer-message').textContent=text;}
function seedViewerFromThumb(id){
 const img=$('#detail-img');
 if(!img)return false;
 const card=document.querySelector('#photo-grid [data-photo="'+id+'"] img');
 const src=card&&(card.currentSrc||card.getAttribute('src'));
 if(!src)return false;
 img.src=src;img.hidden=false;return true;
}
function clearViewerImage(){
 hideSignatureInfo();
 signatureInfo.sections=[];signatureInfo.text='';
 const img=$('#detail-img'),signature=$('#photo-signature'),faces=$('#face-name-layer');
 if(img){img.removeAttribute('src');img.hidden=true;img.style.width='';img.style.height='';}
 if(signature){signature.hidden=true;signature.replaceChildren();}
 if(faces){faces.hidden=true;faces.replaceChildren();}
 clearFaceNames();
 const favorite=$('#photo-favorite');if(favorite)favorite.hidden=true;
 const placeMap=$('#photo-place-map');if(placeMap)placeMap.hidden=true;
}
function setViewerWritePending(pending){
 const controls=$$('#detail-form button,#exclude-photo,#restore-photo,#photo-favorite');
 for(const control of controls){
  if(pending){if(!control.disabled){control.dataset.viewerPendingDisabled='1';control.disabled=true;}}
  else if(control.dataset.viewerPendingDisabled==='1'){delete control.dataset.viewerPendingDisabled;control.disabled=false;}
 }
}
function setViewerLoading(loading,token=0){
 const dialog=$('#detail-dialog'),img=$('#detail-img'),signature=$('#photo-signature');
 const host=document.querySelector('#detail-dialog .dialog-shell')||document.querySelector('.viewer-stage');
 if(!dialog)return;
 let indicator=$('#viewer-loading');
 if(!indicator&&host){
  indicator=document.createElement('div');
  indicator.id='viewer-loading';
  indicator.setAttribute('aria-live','polite');
  host.appendChild(indicator);
 }else if(indicator&&host&&indicator.parentElement!==host){
  host.appendChild(indicator);
 }
 if(indicator)indicator.textContent='加载中';
 clearTimeout(viewer.loadingTimer);viewer.loadingTimer=null;
 viewer.loadingToken=loading?token:0;
 dialog.classList.remove('is-switching','is-wait');
 dialog.classList.toggle('is-loading',Boolean(loading));
 if(loading){
  if(indicator)indicator.hidden=true;
  viewer.loadingTimer=setTimeout(()=>{if(viewer.loadingToken!==token)return;const late=$('#viewer-loading');if(late)late.hidden=false;dialog.classList.add('is-wait');},220);
  setViewerWritePending(true);
 }else{
  dialog.classList.remove('is-wait');
  if(indicator)indicator.hidden=true;
  if(img)img.hidden=!img.getAttribute('src');
  if(signature)signature.hidden=!signature.childElementCount;
  setViewerWritePending(false);
 }
}
function viewerImageFailed(message){
 const img=$('#detail-img');
 if(!img||img.hidden||!img.getAttribute('src'))clearViewerImage();
 setViewerLoading(false);
  viewerMessage(message);
}
function restoreDisplayedPosition(){
 const shown=Number(state.detail?.id),local=viewer.ids.indexOf(shown);
 if(local>=0){viewer.index=viewer.target=local;viewer.absolute=(viewer.offset||0)+local;viewer.goal=viewer.absolute;updatePosition();}
}
function waitViewerFrames(count=2){return new Promise(resolve=>{const next=()=>count--<=0?resolve():requestAnimationFrame(next);next();});}
function closePhotoViewer(){
 hideSignatureInfo();
 const dialog=$('#detail-dialog');
 if(!dialog||!dialog.open)return;
 const personDialog=$('#person-dialog');
 const returning=personDialog?.dataset.photoReturn==='1'&&personDialog.open;
 const body=returning?$('.person-body'):null;
 const returnScroll=Number(personDialog?.dataset.photoReturnScroll||0);
 if(personDialog)personDialog.inert=false;
 if(body)requestAnimationFrame(()=>{if(personDialog.open)body.scrollTop=returnScroll;});
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
 pressTool('#toggle-face-alias', faceAliasMode()!=='off');
 pressTool('#toggle-face-dir', faceDirMode()!=='horizontal');
 pressTool('#viewer-info', !$('#detail-dialog').classList.contains('hide-info'));
 pressTool('#viewer-play', viewer.playing);
 const aliasBtn=$('#toggle-face-alias'); if(aliasBtn){ aliasBtn.disabled=viewer.faceNames===false; const am=faceAliasMode(); aliasBtn.title=am==='off'?'显示姓名和别名':am==='with'?'只显示别名':'只显示姓名'; aliasBtn.setAttribute('aria-label', aliasBtn.title); aliasBtn.dataset.aliasMode=am; }
 const dirBtn=$('#toggle-face-dir'); if(dirBtn) dirBtn.disabled=viewer.faceNames===false;
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
function signatureWidthForPhoto(width){
 const signature=$('#photo-signature');
 const frame=signature?.dataset.style==='tone'?12:0;
 return signature&&!signature.hidden?Math.max(width,Math.min(280,$('#image-viewport').clientWidth-frame)):width;
}
let signatureTextMeasure=null;
function alignSignatureTextInk(content){
 if(!content)return;
 const left=content.querySelector('.signature-memory:not([hidden]),.caption-left');
 const right=content.querySelector('.signature-file:not([hidden]),.caption-right')||content.querySelector('.signature-capture:not([hidden])');
 if(!left||!right)return;
 left.style.paddingBottom='0px';right.style.paddingBottom='0px';
 const naturalHeight=parseFloat(getComputedStyle(content).height);
 const scale=naturalHeight?content.getBoundingClientRect().height/naturalHeight:1;
 if(!scale)return;
 signatureTextMeasure ||= document.createElement('canvas').getContext('2d');
 const inkBottom=group=>{
  const walker=document.createTreeWalker(group,NodeFilter.SHOW_TEXT);
  let node,bottom=null;
  while((node=walker.nextNode())){
   if(!node.textContent.trim()||node.parentElement.closest('svg'))continue;
   const range=document.createRange();range.selectNodeContents(node);
   const rects=[...range.getClientRects()].filter(r=>r.width&&r.height);
   if(!rects.length)continue;
   const style=getComputedStyle(node.parentElement);
   signatureTextMeasure.font=`${style.fontStyle} ${style.fontWeight} ${style.fontSize} ${style.fontFamily}`;
   const metrics=signatureTextMeasure.measureText(node.textContent);
   // The Range includes the font's descender space; the drawn glyphs often
   // do not (especially Latin digits beside Chinese place names).
   const inset=(metrics.fontBoundingBoxDescent??metrics.actualBoundingBoxDescent)-metrics.actualBoundingBoxDescent;
   const edge=Math.max(...rects.map(r=>r.bottom))-inset*scale;
   bottom=bottom===null?edge:Math.max(bottom,edge);
  }
  return bottom;
 };
 const a=inkBottom(left),b=inkBottom(right);
 if(a===null||b===null)return;
 // Use real font metrics to raise the lower ink edge, while preserving grid
 // alignment and letting the corrected natural height participate in photo fit.
 const target=Math.min(a,b);
 left.style.paddingBottom=Math.max(0,(a-target)/scale).toFixed(3)+'px';
 right.style.paddingBottom=Math.max(0,(b-target)/scale).toFixed(3)+'px';
}
function applySignatureMode(mode,width){
 const dialog=$('#detail-dialog');
 if(!dialog)return 0;
 dialog.dataset.signatureMode=mode;
 dialog.dataset.signatureSmall=String(signatureWidthForPhoto(width)<380);
 const content=$('#photo-signature .signature-v3');
 if(Number.isFinite(width))$('#photo-mat').style.width=signatureWidthForPhoto(width)+'px';
 alignSignatureTextInk(content);
 // Reserve exactly the natural caption height. A mode-specific minimum put
 // all unused space below the text and made narrow photographs look bottom-heavy.
 // Computed height excludes the dialog's opening scale animation.
 const height=Math.ceil(content?parseFloat(getComputedStyle(content).height)||content.offsetHeight:0);
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
  positionSignatureInfo();
  if(reset){area.scrollTop=0;area.scrollLeft=0;}
  if(state.detail&&viewer.faceNames!==false){
   const detailId=Number(state.detail.id)||0;
   const shownId=Number($('#detail-dialog').dataset.photoId)||0;
   if(detailId&&detailId===shownId)requestAnimationFrame(()=>renderFaceNames(state.detail));
  }
}
function renderSignature(a,file){
 const signature=$('#photo-signature');if(!signature)return;
 const tags=a.metadata?.ExifTool||{};
 const tag=name=>Object.entries(tags).find(([key])=>key.split(':').at(-1)===name)?.[1];
 const clean=value=>String(value||'').replace(/\0/g,'').trim();
 const number=name=>{const raw=tag(name);const n=Number(raw);return Number.isFinite(n)&&n>0?n:null;};
 const cameraTag=name=>Object.entries(tags).find(([key,value])=>key.split(':').at(-1)===name&&clean(value))?.[1];
 const ifd=a.metadata?.IFD0||{};
 const make=clean(cameraTag('Make'))||clean(ifd.Make)||clean(ifd['271']);
 const model=clean(cameraTag('Model'))||clean(ifd.Model)||clean(ifd['272']);
 const camera=model?(make&&model.toLowerCase().startsWith(make.toLowerCase())?model:[make,model].filter(Boolean).join(' ')):clean(a.camera)||make;
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
 const supplemented=timeKind==='补录时间';
 const visibleKind=supplemented?'':timeKind;
 const timeKindMarkup=shownDate&&visibleKind&&visibleKind!=='拍摄时间'?`<small id="signature-primary-kind">${esc(visibleKind)}</small>`:'';
 const dateEditable=Boolean(shownDate)&&(timeKind==='文件时间参考'||supplemented);
 const dateEditMarkup=dateEditable?`<button type="button" class="signature-date-edit" aria-label="修改时间">✎</button>`:'';
 const exposureMarkup=exposure.map(value=>`<span class="signature-exposure-token">${esc(value)}</span>`).join('');
 const fileMarkup=[[dimensions,'dimensions'],[format,'format-token'],[fileSize,'size-token']].filter(([value])=>value).map(([value,kind])=>`<span class="signature-file-token signature-${kind}">${esc(value)}</span>`).join('');
 const placeMarkup=place?`<span id="signature-place" class="signature-place" title="${esc(place)}">${esc(place)}</span>`:'';

 signature.innerHTML=`
  <div class="signature-v3 ${hasMemory?'has-memory':''} ${hasCapture?'has-capture':''} ${hasFile?'has-file':''}" data-has-memory="${hasMemory}" data-has-capture="${hasCapture}" data-has-file="${hasFile}">
   <span class="signature-seal-v3" aria-hidden="true">拾</span>
    <div id="signature-primary" class="signature-memory"${hasMemory?'':' hidden'}>
     <div class="signature-memory-main">
      <strong id="signature-primary-value" class="signature-date"${shownDate?'':' hidden'}>${dateMarkup}${dateEditMarkup}</strong>
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
  <button type="button" class="signature-switch" title="">
   <svg viewBox="0 0 20 20" aria-hidden="true"><rect x="3" y="4" width="14" height="12" rx="2"/><path d="M3 12h14M7 14h2m3 0h1"/></svg>
  </button>`;
 signature.hidden=false;
 signature.removeAttribute('title');
 signature.classList.toggle('can-edit-file-date',dateEditable);
 setSignatureInfo(a,file,{rawDate,timeKind,place,camera,exposure,dimensions,format,fileSize,filename,tags});
 const cameraLine=camera?`<strong class="caption-camera" title="${esc(camera)}">${esc(camera)}</strong>`:'';
 const dateLine=shownDate?`<span class="caption-date">${dateMarkup}${dateEditMarkup}${timeKindMarkup}</span>`:'';
 const placeLine=place?`<span class="caption-place" title="${esc(place)}">${esc(place)}</span>`:'';
 const memoryLine=`<div class="caption-memory">${dateLine}${placeLine}</div>`;
 const exposureLine=exposure.length?`<div class="caption-exposure">${exposureMarkup}</div>`:'';
 const filesLine=hasFile?`<div class="caption-files">${fileMarkup}</div>`:'';
 const redMark='<span class="caption-brand caption-red" role="img" aria-label="拾光红色圆形字标"><svg viewBox="0 0 48 48" aria-hidden="true"><circle cx="24" cy="24" r="24" fill="#d21e28"/><text x="24" y="29" text-anchor="middle" fill="white" font-family="Ma Shan Zheng" font-size="20">拾光</text></svg></span>';
 const blueMark='<span class="caption-brand caption-blue" role="img" aria-label="拾光蓝色光学方标"><svg viewBox="0 0 40 40" aria-hidden="true"><path fill="#123cba" d="M0 0h40v40q-20-7-40 0z"/><path d="M10 25h20M20 9v10m-9-7 4 5m14-5-4 5m-7 3c0 6-3 8-7 9m12-9v7q0 2 6 1" stroke="white" stroke-width="1.7" fill="none" stroke-linecap="round"/></svg></span>';
 const galleryMark=`<span class="caption-brand caption-gallery-brand" aria-label="拾光图形标识"><svg viewBox="0 0 36 30" aria-hidden="true"><path d="m4 23 10-16h7l-5 8h8l5-8h5L24 23h-7l5-8h-8l-5 8z" fill="currentColor"/></svg>${camera?'<span>拾光相册</span>':''}</span>`;
 const handMark='<span class="caption-handmark" role="img" aria-label="拾光相册手写字标">拾光相册</span>';
 const ringMark='<span class="caption-brand caption-ring" role="img" aria-label="拾光光圈圆环标"><svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15"/><circle cx="18" cy="18" r="10"/><path d="M18 8v6m10 4h-6m-4 10v-6M8 18h6"/><circle cx="18" cy="18" r="3"/></svg></span>';
 signatureLayouts={
  original:signature.querySelector('.signature-v3').innerHTML,
  classic:`<div class="caption-panel caption-left">${cameraLine||'<strong class="caption-camera">拾光相册</strong>'}${memoryLine}</div><div class="caption-panel caption-right caption-leica-lockup">${redMark}<div class="caption-technical">${exposureLine}${filesLine}</div></div>`,
  gallery:`<div class="caption-panel caption-left">${cameraLine||'<strong class="caption-camera">拾光相册</strong>'}${memoryLine}</div><div class="caption-panel caption-right">${galleryMark}${exposureLine}${filesLine}</div>`,
  handwritten:`<div class="caption-panel caption-left"><div class="caption-optical-lockup">${cameraLine||'<strong class="caption-camera">拾光相册</strong>'}${blueMark}</div>${exposureLine}${filesLine}</div><div class="caption-panel caption-right">${camera?handMark:''}${memoryLine}</div>`,
  tone:`<div class="caption-panel caption-left"><div class="caption-tone-lockup">${cameraLine||'<span class="caption-tone-wordmark">拾光相册</span>'}${ringMark}</div></div><div class="caption-panel caption-right">${exposureLine}${memoryLine}${filesLine}</div>`
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
  content.querySelectorAll('[title]').forEach(node=>node.removeAttribute('title'));
 }
 $('#photo-mat').dataset.captionStyle=style.key;
 const next=SIGNATURE_STYLES[(signatureStyleIndex+1)%SIGNATURE_STYLES.length];
 const button=signature.querySelector('.signature-switch');
 if(button)button.setAttribute('aria-label',`点击切换标签样式；当前${style.label}，下一款${next.label}`);
 if(style.key==='tone')updateSignaturePalette();
 const font=style.key==='handwritten'?'Ma Shan Zheng':style.key==='gallery'?'Noto Serif SC':style.key==='classic'?'Ma Shan Zheng':null;
 if(font)document.fonts.load(`20px "${font}"`).then(()=>{
  if(signature.dataset.style===style.key&&!signature.hidden)updateZoom();
 }).catch(()=>{});
}
function setSignatureInfo(photo,file,values){
 hideSignatureInfo();
 const {rawDate,timeKind,place,camera,exposure,dimensions,fileSize,filename,tags}=values;
 const tag=name=>Object.entries(tags).find(([key])=>key.split(':').at(-1)===name)?.[1];
 const finite=value=>value!==null&&value!==undefined&&String(value).trim()!==''&&Number.isFinite(Number(value))?Number(value):null;
 const coordinate=(value,ref,limit)=>{
  let n=finite(value);if(n===null||Math.abs(n)>limit)return null;
  if(/^[SW]$/i.test(String(ref||'')))n=-Math.abs(n);
  return n;
 };
 const latitude=coordinate(photo.latitude??tag('GPSLatitude'),photo.latitude==null?tag('GPSLatitudeRef'):null,90);
 const longitude=coordinate(photo.longitude??tag('GPSLongitude'),photo.longitude==null?tag('GPSLongitudeRef'):null,180);
 let altitude=finite(tag('GPSAltitude'));
 if(altitude!==null&&Number(tag('GPSAltitudeRef'))===1)altitude=-Math.abs(altitude);
 const path=String(file?.path||'');
 const split=Math.max(path.lastIndexOf('\\'),path.lastIndexOf('/'));
 let directory=split>=0?path.slice(0,split):'';
 if(/^[A-Za-z]:$/.test(directory))directory+='\\';
 if(split===0&&path.startsWith('/'))directory='/';
 const size=finite(file?.size);
const capture=[
  [timeKind==='补录时间'?'时间':timeKind,rawDate.replace('T',' ')],
  ['相机',camera],
  ['参数',exposure.join(' · ')]
];
 const gps=[
  latitude===null?'':`${Math.abs(latitude).toFixed(6)}°${latitude<0?'S':'N'}`,
  longitude===null?'':`${Math.abs(longitude).toFixed(6)}°${longitude<0?'W':'E'}`,
  altitude===null?'':`海拔 ${Number(altitude.toFixed(2))} m`
 ].filter(Boolean).join('  ');
 const location=[
  ['地点',place],['GPS',gps]
 ];
 const files=[
  ['图像', [dimensions,size===null?'':fileSize||`${size.toLocaleString('zh-CN')} 字节`].filter(Boolean).join(' · ')],
  ['文件名',filename],['文件夹',directory]
 ];
 if(file?.modified_at&&String(file.modified_at)!==rawDate)files.push(['文件修改',String(file.modified_at).replace('T',' ')]);
 const copies=[...new Set((photo.files||[]).map(f=>f.path).filter(p=>p&&p!==path))];
 if(copies.length)files.push(['其他位置',copies.join('\n')]);
 let sections=[['拍摄',capture],['地点与 GPS',location],['文件',files]];
 if(photo.notes)sections.push(['照片说明',[['备注',String(photo.notes)]]]);
 // Keep the visible card and clipboard identical; empty fields never reserve space.
 sections=sections.map(([heading,rows])=>[heading,rows.map(([key,value])=>[key,String(value??'').trim()]).filter(([,value])=>value)]).filter(([,rows])=>rows.length);
 signatureInfo.sections=sections;
 signatureInfo.text=sections.length?['照片信息',...sections.map(([,rows])=>rows.map(([key,value])=>key+'：'+value.replace(/\r?\n/g,'\r\n  ')).join('\r\n'))].join('\r\n\r\n'):'';
 const pop=ensureSignatureInfo();
 pop.querySelector('.signature-info-body').innerHTML=sections.map(([,rows])=>`<dl class="signature-info-group">${rows.map(([key,value])=>`<div class="signature-info-row${key==='GPS'?' signature-info-gps':''}"><dt>${esc(key)}</dt><dd>${esc(value)}</dd></div>`).join('')}</dl>`).join('');
 pop.querySelector('.signature-info-status').textContent='';
 const caption=$('#photo-signature');
 caption.tabIndex=0;caption.setAttribute('aria-label','照片标签，查看完整照片信息');
 caption.setAttribute('aria-haspopup','dialog');caption.setAttribute('aria-controls','signature-info-popover');
}
function ensureSignatureInfo(){
 let pop=$('#signature-info-popover');if(pop)return pop;
 pop=document.createElement('div');pop.id='signature-info-popover';pop.className='signature-info-popover';pop.hidden=true;
 pop.setAttribute('role','dialog');pop.setAttribute('aria-modal','false');pop.setAttribute('aria-labelledby','signature-info-heading');pop.tabIndex=-1;
 pop.innerHTML='<header><h2 id="signature-info-heading">照片信息</h2><button type="button" class="signature-info-close" aria-label="关闭照片信息">×</button></header><div class="signature-info-body"></div><footer><button type="button" class="signature-info-copy"><svg viewBox="0 0 20 20" aria-hidden="true"><rect x="7" y="6" width="9" height="11" rx="1.5"/><path d="M12 6V3H4v11h3"/></svg><span>复制</span></button><span class="signature-info-status" role="status" aria-live="polite"></span></footer>';
 $('#detail-dialog').appendChild(pop);
 pop.addEventListener('pointerenter',()=>{clearTimeout(signatureInfo.closeTimer);});
 pop.addEventListener('pointerleave',()=>scheduleSignatureInfoClose());
 pop.addEventListener('focusin',()=>clearTimeout(signatureInfo.closeTimer));
 pop.addEventListener('focusout',()=>scheduleSignatureInfoClose());
 pop.addEventListener('keydown',event=>{
  event.stopPropagation();
  if(event.key==='Escape'){event.preventDefault();$('#photo-signature').focus({preventScroll:true});hideSignatureInfo();}
 });
 pop.querySelector('.signature-info-close').addEventListener('click',()=>{$('#photo-signature').focus({preventScroll:true});hideSignatureInfo();});
 pop.querySelector('.signature-info-copy').addEventListener('click',async()=>{
  const text=signatureInfo.text,status=pop.querySelector('.signature-info-status');
  clearTimeout(signatureInfo.copyTimer);
  try{
   await navigator.clipboard.writeText(text);
   if(text!==signatureInfo.text)return;
   status.textContent='已复制';
   signatureInfo.copyTimer=setTimeout(()=>status.textContent='',2400);
  }catch(e){if(text===signatureInfo.text)status.textContent='复制失败，可选中文字后复制';}
 });
 return pop;
}
function positionSignatureInfo(){
 const pop=$('#signature-info-popover'),caption=$('#photo-signature');
 if(!pop||pop.hidden||caption.hidden)return;
 const anchor=caption.getBoundingClientRect();
 const above=Math.max(0,anchor.top-18),below=Math.max(0,innerHeight-anchor.bottom-18);
 const useAbove=above>=Math.min(360,below);
 const room=useAbove?above:below;
 pop.style.width='max-content';
 pop.style.maxWidth=Math.min(390,innerWidth-24)+'px';
 pop.style.maxHeight=Math.min(460,Math.max(180,room),innerHeight-24)+'px';
 const box=pop.getBoundingClientRect();
 pop.style.left=Math.max(12,Math.min(anchor.left+8,innerWidth-box.width-12))+'px';
 pop.style.top=Math.max(12,Math.min(useAbove?anchor.top-box.height-6:anchor.bottom+6,innerHeight-box.height-12))+'px';
}
function showSignatureInfo(){
 clearTimeout(signatureInfo.openTimer);clearTimeout(signatureInfo.closeTimer);
 signatureInfo.openTimer=null;
 if(!signatureInfo.text||$('#photo-signature').hidden||!$('#detail-dialog').open)return;
 const pop=ensureSignatureInfo();
 if(pop.hidden)pop.querySelector('.signature-info-status').textContent='';
 pop.hidden=false;
 $('#photo-signature').setAttribute('aria-expanded','true');
 positionSignatureInfo();
}
function hideSignatureInfo(){
 clearTimeout(signatureInfo.openTimer);clearTimeout(signatureInfo.closeTimer);clearTimeout(signatureInfo.copyTimer);
 signatureInfo.openTimer=null;
 const pop=$('#signature-info-popover');if(pop)pop.hidden=true;
 $('#photo-signature')?.setAttribute('aria-expanded','false');
}
function scheduleSignatureInfoClose(){
 clearTimeout(signatureInfo.openTimer);clearTimeout(signatureInfo.closeTimer);
 signatureInfo.openTimer=null;
 signatureInfo.closeTimer=setTimeout(()=>{
  const pop=$('#signature-info-popover'),caption=$('#photo-signature');
  const focused=document.activeElement;
  if(pop?.matches(':hover')||caption.matches(':hover')||((pop?.contains(focused)||caption===focused)&&focused.matches(':focus-visible')))return;
  hideSignatureInfo();
 },260);
}
$('#photo-signature').addEventListener('pointerover',event=>{
 if(event.pointerType==='touch'||event.target.closest('button')||signatureInfo.openTimer)return;
 clearTimeout(signatureInfo.closeTimer);
 signatureInfo.openTimer=setTimeout(()=>{signatureInfo.openTimer=null;showSignatureInfo();},200);
});
$('#photo-signature').addEventListener('pointerleave',scheduleSignatureInfoClose);
$('#photo-signature').addEventListener('focusin',event=>{if(event.target===$('#photo-signature'))showSignatureInfo();});
$('#photo-signature').addEventListener('focusout',scheduleSignatureInfoClose);
$('#photo-signature').addEventListener('keydown',event=>{
 if(event.target!==$('#photo-signature'))return;
 if(event.key==='Enter'||event.key===' '){event.preventDefault();event.stopPropagation();showSignatureInfo();$('#signature-info-popover')?.focus();}
});
$('#photo-signature').addEventListener('click',event=>{if(!event.target.closest('button'))showSignatureInfo();});
$('#detail-dialog').addEventListener('close',hideSignatureInfo);
document.addEventListener('keydown',event=>{
 const pop=$('#signature-info-popover');
 if(event.key==='Escape'&&pop&&!pop.hidden&&$('#detail-dialog').open&&$$('dialog[open]').at(-1)?.id==='detail-dialog'){event.preventDefault();event.stopImmediatePropagation();$('#photo-signature').focus({preventScroll:true});hideSignatureInfo();}
},true);
$('#image-viewport').addEventListener('scroll',()=>{
 const pop=$('#signature-info-popover');if(!pop||pop.hidden)return;
 const anchor=$('#photo-signature').getBoundingClientRect();
 if(anchor.bottom<0||anchor.top>innerHeight)hideSignatureInfo();else positionSignatureInfo();
},{passive:true});
window.addEventListener('resize',positionSignatureInfo);
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
 const edit=event.target.closest('.signature-date-edit');
 if(edit){event.preventDefault();event.stopPropagation();beginFileDateEdit(state.detail);return;}
 if(event.target.closest('.signature-date-input'))return;
 if(!event.target.closest('.signature-switch'))return;
 event.stopPropagation();
 signatureStyleIndex=(signatureStyleIndex+1)%SIGNATURE_STYLES.length;
 applySignatureStyle();
 updateZoom();
});
function fileDateValue(photo){
 const raw=String(photo?.effective_date||'').trim();
 const match=raw.match(/^(\d{4}-\d{2}-\d{2})/);
 return match?match[1]:'';
}
function beginFileDateEdit(photo){
 const signature=$('#photo-signature');
 const date=signature?.querySelector('#signature-primary-value, .caption-date');
 if(!signature||!date||date.querySelector('.signature-date-input')||signature.dataset.dateSaving==='true')return;
 const input=document.createElement('input');
 input.type='date';
 input.className='signature-date-input';
 input.value=fileDateValue(photo);
 input.setAttribute('aria-label','输入拍摄日期');
 let closed=false;
 const finish=async save=>{
  if(closed||!date.contains(input))return;
  closed=true;
  input.remove();
  if(!save)return;
  const value=input.value;
  if(!value||value===fileDateValue(photo))return;
  signature.dataset.dateSaving='true';
  try{
   const receipt=await editPhotoMetadata({ids:[Number(photo.id)],manual_date:value,manual_precision:'日'});
   viewerMessage(photoEditMessage(receipt));
   await openPhoto(Number(photo.id),viewer.context);
  }catch(error){
   viewerMessage(error.message||'日期没有保存');
   renderSignature(state.detail,viewer.file||state.detail?.files?.[0]);
  }finally{
   delete signature.dataset.dateSaving;
  }
 };
 input.addEventListener('keydown',event=>{
  if(event.key==='Enter'){event.preventDefault();void finish(true);}
  else if(event.key==='Escape'){event.preventDefault();event.stopPropagation();void finish(false);}
 });
 input.addEventListener('blur',()=>void finish(true));
 date.append(input);
 input.focus();
}
function faceLabelState(face, aliasMode=faceAliasMode()){
  return faceLabels.resolveFaceLabelState
    ? faceLabels.resolveFaceLabelState(face, aliasMode)
    : {kind:face?.ignored?'passerby':(face?.name?'named':'pending'),isNamed:Boolean(face?.name&&!face?.ignored),displayText:String(face?.name||'+'),accessibleText:String(face?.name||'待命名'),faceId:Number(face?.id)||null,personId:Number(face?.person_id)||null};
}
function namedFaces(photo){return (photo&&photo.faces||[]).filter(face=>faceLabelState(face).isNamed);}
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
function faceAliasMode(){
  const mode=viewer.faceAliasMode||'off';
  return (mode==='off'||mode==='with'||mode==='only')?mode:'off';
}
function faceLabelText(face, alias){
  const mode=alias===true?'with':(alias===false?'off':(alias||faceAliasMode()));
  return faceLabelState(face,mode).displayText;
}
const faceLabelCommittedPositions=new Map();
const faceLabelOptimisticPositions=new Map();
const faceLabelWriteRevisions=new Map();
const faceLabelPendingCounts=new Map();
const faceLabelPendingWrites=new Set();
let faceLabelDrag=null;
let suppressFaceLabelEditUntil=0;
function storedFaceLabelPosition(face){
  const x=face?.label_x_ratio,y=face?.label_y_ratio;
  if(x===null||x===undefined||y===null||y===undefined)return null;
  const nx=Number(x),ny=Number(y);
  return Number.isFinite(nx)&&Number.isFinite(ny)&&nx>=0&&nx<=1&&ny>=0&&ny<=1
    ?{x:nx,y:ny}:null;
}
function effectiveFaceLabelPosition(face){
  return faceLabelOptimisticPositions.get(Number(face?.id))||storedFaceLabelPosition(face);
}
// Choice is per photo. Old database overrides automatically count as custom;
// choosing an automatic layout never deletes those overrides.
const faceLabelPhotoModes=new Map();
function currentFaceLabelMode(){
  const hasManual=(state.detail?.faces||[]).some(face=>Boolean(effectiveFaceLabelPosition(face)));
  const selected=faceLabelPhotoModes.get(Number(state.detail?.id));
  if(selected==='manual')return hasManual?'manual':'auto';
  return selected || (hasManual?'manual':'auto');
}
function syncFaceLabelResetButton(){
  const button=$('#face-label-reset-photo');
  const hasManual=(state.detail?.faces||[]).some(face=>Boolean(effectiveFaceLabelPosition(face)));
  if(button){
    button.disabled=!hasManual;
    button.title=hasManual?'仅此按钮会删除已保存的手动位置；切换排版不会删除':'本张照片没有自定义排版';
  }
  const position=$('#face-label-position');
  if(position){
    const custom=position.querySelector('option[value="manual"]');
    if(custom)custom.disabled=!hasManual;
    position.value=currentFaceLabelMode();
  }
}
function applyFaceLabelPositionToState(faceId,position){
  const face=(state.detail?.faces||[]).find(item=>Number(item.id)===Number(faceId));
  if(!face)return;
  face.label_x_ratio=position?position.x:null;
  face.label_y_ratio=position?position.y:null;
}
async function persistFaceLabelPosition(faceId,position){
  const photoId=Number(state.detail?.id),fid=Number(faceId);
  if(!photoId||!fid)return;
  const revision=(faceLabelWriteRevisions.get(fid)||0)+1;
  faceLabelWriteRevisions.set(fid,revision);
  if(!faceLabelCommittedPositions.has(fid)){
    const face=(state.detail?.faces||[]).find(item=>Number(item.id)===fid);
    faceLabelCommittedPositions.set(fid,storedFaceLabelPosition(face));
  }
  faceLabelOptimisticPositions.set(fid,position);
  applyFaceLabelPositionToState(fid,position);
  faceLabelPhotoModes.set(photoId,'manual');
  faceLabelPendingCounts.set(fid,(faceLabelPendingCounts.get(fid)||0)+1);
  syncFaceLabelResetButton();
  const payload={x_ratio:position.x,y_ratio:position.y};
  const write=viewerCaptureEntityWrite('faceLabel',fid,payload,revision);
  let pending;
  pending=window.__ourTimeApp.queueEntityWrite(
    'face-label',fid,
    ()=>window.__ourTimeApp.operationRequest(
      `/api/photos/${photoId}/faces/${fid}/label-position`,
      {method:'PUT',body:JSON.stringify({...write.payload,operation_id:write.operationId})},
      write.operationId
    )
  ).then(receipt=>{
    const saved={x:Number(receipt.x_ratio),y:Number(receipt.y_ratio)};
    faceLabelCommittedPositions.set(fid,saved);
    if(faceLabelWriteRevisions.get(fid)===revision){
      faceLabelOptimisticPositions.set(fid,saved);
      applyFaceLabelPositionToState(fid,saved);
    }
  }).catch(error=>{
    if(faceLabelWriteRevisions.get(fid)===revision){
      const fallback=faceLabelCommittedPositions.get(fid)||null;
      if(fallback)faceLabelOptimisticPositions.set(fid,fallback);
      else faceLabelOptimisticPositions.delete(fid);
      applyFaceLabelPositionToState(fid,fallback);
      if(Number(state.detail?.id)===photoId)renderFaceNames(state.detail);
      toast('标签位置未保存，已恢复原位',true);
    }
  }).finally(()=>{
    const remaining=Math.max(0,(faceLabelPendingCounts.get(fid)||1)-1);
    if(remaining)faceLabelPendingCounts.set(fid,remaining);
    else{
      faceLabelPendingCounts.delete(fid);
      if(faceLabelWriteRevisions.get(fid)===revision){
        faceLabelOptimisticPositions.delete(fid);
      }
    }
    faceLabelPendingWrites.delete(pending);
    syncFaceLabelResetButton();
  });
  faceLabelPendingWrites.add(pending);
}
async function resetCurrentPhotoFaceLabels(){
  const photoId=Number(state.detail?.id);
  if(!photoId)return false;
  const button=$('#face-label-reset-photo');
  if(button)button.disabled=true;
  await Promise.allSettled([...faceLabelPendingWrites]);
  try{
    await window.__ourTimeApp.operationRequest(
      `/api/photos/${photoId}/face-labels/reset`,{method:'POST'}
    );
    if(Number(state.detail?.id)===photoId){
      for(const face of state.detail?.faces||[]){
        const fid=Number(face.id);
        faceLabelCommittedPositions.set(fid,null);
        faceLabelOptimisticPositions.delete(fid);
        face.label_x_ratio=null;
        face.label_y_ratio=null;
      }
      faceLabelPhotoModes.set(photoId,'auto');
      renderFaceNames(state.detail);
      toast('已清除本张自定义排版，恢复智能排布');
    }
    return true;
  }catch(error){
    toast(error.message||'恢复自动排列失败',true);
    return false;
  }finally{
    syncFaceLabelResetButton();
  }
}
async function selectFaceLabelPosition(value){
  const next=value==='manual'?'manual':FACE_LABEL_POSITIONS.has(value)?value:'auto';
  const hasManual=(state.detail?.faces||[]).some(face=>Boolean(effectiveFaceLabelPosition(face)));
  if(next==='manual'&&!hasManual){syncFaceLabelResetButton();return;}
  faceLabelPhotoModes.set(Number(state.detail?.id),next);
  if(state.detail)renderFaceNames(state.detail);
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
  const forced=currentFaceLabelMode();
  if(forced==='left'||forced==='right')return [forced,forced==='left'?'right':'left'];
  if(forced==='top'||forced==='bottom')return [forced,forced==='top'?'bottom':'top'];
  if(!vertical)return ['bottom','top'];
  return ['left','right'];
}
function faceLabelEyeRegion(item){
  // Only bounding boxes are stored; this is an estimated eye band, not landmarks.
  return {x:item.fx+item.fw*.18,y:item.fy+item.fh*.22,w:item.fw*.64,h:item.fh*.30};
}
function placeSmartVerticalLabels(geometries, items, placed, bounds){
  const faces=items.map(it=>({x:it.fx,y:it.fy,w:it.fw,h:it.fh}));
  const eyeRegions=items.map(faceLabelEyeRegion);
  const heads=items.map(it=>({x:it.fx-it.fw*.12,y:it.fy-it.fh*.25,w:it.fw*1.24,h:it.fh*1.33}));
  const pending=geometries.filter(g=>g.smart);
  const candidates=new Map();
  for(const g of pending){
    const it=g.it,head=heads[g.index];
    const idealGap=Math.max(8,Math.min(20,it.fw*.18));
    const list=[];
    // Keep labels near the head row; avoid distant above/below detours.
    // Additional gaps retract toward the owner: flush to the face box, then
    // at most 10% / 18% into its edge, still outside the central facial area.
    const gaps=[idealGap,4,-it.fw*.12,-it.fw*.22,-it.fw*.30];
    for(const side of ['left','right'])for(const gap of gaps)for(const shift of [0,-.3,.3,-.6,.6]){
      const rect={x:side==='left'?head.x-g.w-gap:head.x+head.w+gap,
        y:it.cy+it.fh*.1-g.h/2+shift*it.fh,w:g.w,h:g.h,side,btn:g.btn};
      rect.outside=rect.x<bounds.x||rect.x+rect.w>bounds.x+bounds.w;
      rect.shift=Math.abs(shift);
      rect.x=Math.max(bounds.x,Math.min(bounds.x+bounds.w-g.w,rect.x));
      rect.y=Math.max(bounds.y,Math.min(bounds.y+bounds.h-g.h,rect.y));
      // Score the exact integer coordinates that will be painted.
      rect.x=Math.round(rect.x);rect.y=Math.round(rect.y);
      const own=faceLabelDistance(rect,it);
      const others=items.filter(other=>other!==it).map(other=>faceLabelDistance(rect,other));
      const ambiguity=others.length?Math.max(0,own-Math.min(...others)):0;
      const faceArea=faces.reduce((s,f)=>s+rectOverlapArea(rect,f),0);
      const ownArea=rectOverlapArea(rect,faces[g.index]);
      const otherArea=Math.max(0,faceArea-ownArea);
      const headArea=heads.reduce((s,f)=>s+rectOverlapArea(rect,f),0);
      // Keep breathing room when available. In a narrow gap, protect the
      // neighbour more strongly than the owner's cheek edge and retract until
      // the label is at least as close to its owner as to another face.
      rect.attachment=8*otherArea/(g.w*g.h)+ownArea/(g.w*g.h)
        +4*ambiguity/it.fw+.25*Math.abs(gap-idealGap)/it.fw;
      rect.eyeArea=eyeRegions.reduce((s,f)=>s+rectOverlapArea(rect,f),0);
      rect.faceRatio=Math.max(0,...faces.map(f=>rectOverlapArea(rect,f)/(f.w*f.h)));
      rect.base=[faceArea,headArea,ambiguity,
        Math.abs(rect.y+g.h/2-(it.cy+it.fh*.1))*.2+Math.abs(gap-idealGap)+(side==='left'?0:6)];
      list.push(rect);
    }
    candidates.set(g,list);
  }
  // Constrained labels get first choice. Stable ties make redraws deterministic.
  pending.sort((a,b)=>candidates.get(a).filter(c=>!c.base[0]&&!c.base[1]).length-
    candidates.get(b).filter(c=>!c.base[0]&&!c.base[1]).length||a.index-b.index);
  const chosen=new Map();
  function score(rect,g){
    const others=[...placed,...[...chosen].filter(([other])=>other!==g).map(([,r])=>r)];
    const overlap=others.reduce((s,other)=>s+rectOverlapArea(rect,other),0);
    const labelRatio=Math.max(0,...others.map(other=>rectOverlapArea(rect,other)/Math.min(rect.w*rect.h,other.w*other.h)));
    const excess=Math.max(0,labelRatio-FACE_LABEL_OVERLAP_LIMIT);
    return [excess>0?1:0,excess,rect.eyeArea>0?1:0,rect.eyeArea,
      Math.max(0,rect.faceRatio-FACE_LABEL_FACE_OVERLAP_LIMIT),Number(rect.outside),
      rect.side==='left'?0:1,rect.shift,rect.attachment,rect.base[0]+overlap,rect.base[1],rect.base[3]];
  }
  for(let pass=0;pass<8;pass++)for(const g of pending){
    let best=null,bestScore=null;
    for(const rect of candidates.get(g)){
      const value=score(rect,g);
      if(!bestScore||compareFaceLabelScore(value,bestScore)<0){best=rect;bestScore=value;}
    }
    chosen.set(g,best);
  }
  // Resolve the group jointly when local choices trap neighbours. Only safe,
  // <=5%-overlap candidates enter the beam; this also allows an outer person
  // to switch right instead of forcing the whole row into left-side slots.
  const safe=new Map(pending.map(g=>[g,candidates.get(g).filter(r=>!r.outside && !r.eyeArea
    && r.faceRatio<=FACE_LABEL_FACE_OVERLAP_LIMIT
    && placed.every(o=>rectOverlapRatio(r,o)<=FACE_LABEL_OVERLAP_LIMIT))]));
  const preferRight=new Set(pending.filter(g=>{
    const it=g.it;
    const row=items.filter(o=>o!==it && o.fy<it.fy+it.fh && o.fy+o.fh>it.fy);
    // Outer edge of a face row (not the whole photo): open space on the right
    // can release a crowded inside gap without moving the label up/down.
    return !row.some(o=>o.cx>it.cx)
      && row.some(o=>o.cx<it.cx && it.fx-(o.fx+o.fw)<it.fw*3+g.w)
      && safe.get(g).some(r=>r.side==='right' && !r.shift && r.faceRatio<=.15);
  }));
  const order=[...pending].sort((a,b)=>safe.get(a).length-safe.get(b).length||a.index-b.index);
  let beam=[{rects:new Map(),cost:0}];
  for(const g of order){
    const next=[];
    for(const state of beam)for(const rect of safe.get(g)){
      if([...state.rects.values()].some(o=>rectOverlapRatio(rect,o)>FACE_LABEL_OVERLAP_LIMIT))continue;
      const preferred=preferRight.has(g)?'right':'left';
      const cost=state.cost+(rect.side===preferred?0:4)+rect.shift*10+rect.attachment;
      next.push({rects:new Map([...state.rects,[g,rect]]),cost});
    }
    next.sort((a,b)=>a.cost-b.cost);
    beam=next.slice(0,96);
    if(!beam.length)break;
  }
  if(beam.length && beam[0].rects.size===pending.length){
    chosen.clear();for(const [g,rect] of beam[0].rects)chosen.set(g,rect);
  }
  for(const [g,rect] of chosen){g.btn.classList.add(rect.side);placed.push(rect);}
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
    const stateInfo=faceLabelState(face, alias);
    const passerby=stateInfo.kind==='passerby';
    const kind=stateInfo.kind;
    items.push({face, passerby, kind, named:stateInfo.isNamed, label:stateInfo.displayText, fx, fy, fw, fh, cx:fx+fw/2, cy:fy+fh/2});
  });
  items.sort((a,b)=>a.cy-b.cy||a.cx-b.cx);
  layer.innerHTML=items.map(it=>{
    const label=it.label;
    const named=it.named;
    const vertical=named&&faceLabelVerticalFor(label);
    it.vertical=vertical;
    const hint=named?`${label}；拖动调整位置，双击管理这张脸`:(it.passerby?'路人；拖动调整位置，单击或双击可重新命名':'拖动调整位置，单击或双击命名人物');
    return `<button type="button" class="face-name${named?'':' unnamed'}${it.passerby?' passerby':''}${named&&!vertical?' is-horizontal':''}" data-face-id="${Number(it.face.id)||''}" data-face-person="${it.face.person_id}" data-face-kind="${it.kind}" data-face-direction="${vertical?'vertical':'horizontal'}" aria-label="${esc(hint)}">${esc(label)}</button>`;
  }).join('')+'<svg class="face-hover-guide" aria-hidden="true"><path></path><circle r="2.6"></circle></svg><span class="face-hover-box" aria-hidden="true"></span>';
  const buttons=[...layer.querySelectorAll('.face-name')];
  const vertical=faceLabelsVertical();
  const placed=[];
  const gap=6;
  const sides=faceLabelSides(items,ox,imgW,vertical);
  const preferredSide=sides[0];
  const faceRects=items.map(item=>({x:item.fx,y:item.fy,w:item.fw,h:item.fh}));
  const mode=currentFaceLabelMode();
  const sourceHeights=items.filter(it=>it.named&&it.vertical).map(it=>{
    const box=faceBox(it.face);
    return box&&box.h?box.height/box.h*FACE_LABEL_REFERENCE_PHOTO_HEIGHT:0;
  }).filter(height=>height>0).sort((a,b)=>a-b);
  const middle=Math.floor(sourceHeights.length/2);
  const typicalSourceHeight=sourceHeights.length?(sourceHeights[middle]+sourceHeights[Math.floor((sourceHeights.length-1)/2)])/2:0;
  const uniformScale=faceLabelScaleForHeight(typicalSourceHeight, imgH);
  const geometries=buttons.map((btn,i)=>{
    const it=items[i];
    it.btn=btn;
    if(it.named){
      applyFaceLabelProfile(btn,it.label);
      const box=faceBox(it.face);
      const sourceHeight=box&&box.h?box.height/box.h*FACE_LABEL_REFERENCE_PHOTO_HEIGHT:0;
      const scale=it.vertical?uniformScale:faceLabelScaleForHeight(sourceHeight, imgH);
      btn.style.setProperty('--face-scale',scale.toFixed(3));
      btn.dataset.faceScale=scale.toFixed(3);
      btn.dataset.faceDirection=it.vertical?'vertical':'horizontal';
      btn.classList.toggle('is-horizontal', it.vertical===false);
    }
    const w=Math.max(18, btn.offsetWidth);
    const h=Math.max(18, btn.offsetHeight);
    const fid=Number(it.face.id);
    if(!faceLabelPendingCounts.has(fid)){
      faceLabelCommittedPositions.set(fid,storedFaceLabelPosition(it.face));
    }
    const manual=mode==='manual'?effectiveFaceLabelPosition(it.face):null;
    return {btn,it,w,h,index:i,manual,smart:!manual&&it.named&&it.vertical&&(mode==='auto'||mode==='manual')};
  });
  geometries.filter(item=>item.manual).forEach(item=>{
    const {btn,w,h,manual}=item;
    const chosen=clampFaceLabel({
      x:ox+manual.x*imgW-w/2,
      y:oy+manual.y*imgH-h/2,
      w,h,btn,manual:true
    },layerW,layerH,4);
    btn.dataset.labelManual='true';
    btn.classList.add('manual');
    placed.push(chosen);
  });
  geometries.filter(item=>!item.manual).forEach(item=>{
    if(item.smart)return;
    const {btn,it,w,h,index:i}=item;
    const offsetsBySide=new Map(sides.map(side=>[side,faceLabelOffsets(side,w,h,layerW,layerH)]));
    const candidates=new Map();
    for(const side of sides){
      for(const offset of offsetsBySide.get(side)){
        const candidate=faceLabelCandidate(it,side,it.vertical,w,h,offset,gap);
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
      const faceRatios=faceRects.map(faceRect=>rectOverlapArea(candidate,faceRect)/(faceRect.w*faceRect.h));
      const maxFaceRatio=Math.max(0,...faceRatios);
      const eyeArea=items.reduce((sum,face)=>sum+rectOverlapArea(candidate,faceLabelEyeRegion(face)),0);
      const otherFaceOverlap=faceRects.reduce((sum,faceRect,faceIndex)=>sum+(faceIndex===i?0:rectOverlapArea(candidate,faceRect)),0);
      const hardLabel=maxLabelRatio>FACE_LABEL_OVERLAP_LIMIT?1:0;
      const hardFace=maxFaceRatio>FACE_LABEL_FACE_OVERLAP_LIMIT?1:0;
      const score=[
        hardLabel,
        hardLabel?maxLabelRatio:0,
        eyeArea>0?1:0,
        eyeArea,
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
    chosen=chosen||clampFaceLabel(faceLabelCandidate(it,sides[0],it.vertical,w,h,0,gap),layerW,layerH,4);
    chosen.btn=btn;
    placed.push(chosen);
    btn.classList.add(chosen.side);
    if(!it.named)btn.setAttribute('title',it.passerby?'路人；拖动调整位置，单击或双击可重新命名':'拖动调整位置，单击或双击命名人物');
  });
  placeSmartVerticalLabels(geometries,items,placed,{x:Math.max(4,ox+4),y:Math.max(4,oy+4),
    w:Math.max(1,Math.min(layerW-4,ox+imgW-4)-Math.max(4,ox+4)),
    h:Math.max(1,Math.min(layerH-4,oy+imgH-4)-Math.max(4,oy+4))});
  let layoutLabelRatio=0,layoutFaceRatio=0,layoutEyeArea=0;
  placed.forEach((rect,index)=>{
    rect.x=Math.round(rect.x);rect.y=Math.round(rect.y);
  });
  placed.forEach((rect,index)=>{
    const maxRatio=placed.reduce((max,other,otherIndex)=>otherIndex===index?max:Math.max(max,rectOverlapRatio(rect,other)),0);
    layoutLabelRatio=Math.max(layoutLabelRatio,maxRatio);
    layoutFaceRatio=Math.max(layoutFaceRatio,...faceRects.map(f=>rectOverlapArea(rect,f)/(f.w*f.h)));
    layoutEyeArea+=items.reduce((sum,f)=>sum+rectOverlapArea(rect,faceLabelEyeRegion(f)),0);
    rect.btn.dataset.labelOverlapRatio=maxRatio.toFixed(3);
    rect.btn.style.left=Math.round(rect.x)+'px';
    rect.btn.style.top=Math.round(rect.y)+'px';
  });
  const issues=[];
  if(layoutLabelRatio>FACE_LABEL_OVERLAP_LIMIT)issues.push(`标签重叠 ${(layoutLabelRatio*100).toFixed(1)}%，超过 5%`);
  if(layoutFaceRatio>FACE_LABEL_FACE_OVERLAP_LIMIT)issues.push('人脸遮挡超过 35%');
  if(layoutEyeArea>0)issues.push('覆盖估算眼部区域');
  layer.dataset.layoutValid=String(!issues.length);
  const layoutStatus=$('#face-label-layout-status');
  if(layoutStatus){
    layoutStatus.hidden=!issues.length;
    layoutStatus.textContent=issues.length?`${issues.join('；')}。${mode==='manual'?'已保留自定义位置，可拖动调整。':'当前空间未找到同时满足限制的排布，请拖动微调。'}`:'';
  }
  syncFaceLabelResetButton();
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
function clearFaceNames(){
  faceLabelDrag=null;
  const layer=$('#face-name-layer');
  if(!layer)return;
  layer.classList.remove('is-linking');
  layer.querySelectorAll('.face-name.is-linked').forEach(item=>item.classList.remove('is-linked'));
  layer.replaceChildren();
  layer.hidden=true;
}
function renderFaceNames(photo){
 const layer=$('#face-name-layer'); if(!layer)return;
 if(faceLabelDrag)return;
 const show=viewer.faceNames!==false;
 const alias=faceAliasMode();
 const generation=++viewer.faceRenderGeneration;
 const photoId=Number(photo&&photo.id)||0;
 if(!show||!photoId){layer.innerHTML='';layer.hidden=true;syncViewerTools();updatePhotoPeoplePopover();return;}
 layer.hidden=false;
 layer.classList.toggle('horizontal', faceDirMode()==='horizontal');
  const faces=visibleFaces(photo);
  if(!$('#detail-img')?.naturalWidth || !layer.clientWidth){
    layer.innerHTML='';
    requestAnimationFrame(()=>{ if(generation!==viewer.faceRenderGeneration||Number(state.detail&&state.detail.id)!==photoId)return; layoutFaceNameButtons(layer, faces, alias); });
  }else layoutFaceNameButtons(layer, faces, alias);
  syncViewerTools();
  updatePhotoPeoplePopover();
}
function zoomTo(scale){viewer.fit=false;viewer.scale=Math.max(.05,Math.min(4,scale));updateZoom();}
async function displayPhoto(id){
  closeFaceActionPopover();togglePhotoPeoplePopover(false);setFaceKindHighlight(null);
 const request=++viewer.presentation;
 setViewerLoading(true,request);
 if(viewer.loaderCancel)viewer.loaderCancel();
 let prepared;
 try{prepared=await window.__ourTimeApp.preparePhotoDetail(id);}
 catch(err){
  if(request===viewer.presentation){
   const visible=Boolean($('#detail-img')?.getAttribute('src')&&!$('#detail-img')?.hidden);
   if(visible)restoreDisplayedPosition();
   viewerImageFailed(visible?'下一张未能载入；仍可继续浏览或重试。':'照片资料读取失败');
  }
  return false;
 }
 if(!prepared||request!==viewer.presentation||!$('#detail-dialog').open)return false;
 const candidate=prepared.detail;
 const root=(viewer.context?.directory||'').replace(/[\/]+$/,'').toLowerCase();
 const files=[...candidate.files].sort((a,b)=>a.excluded-b.excluded||b.exists_now-a.exists_now||a.id-b.id);
 const scoped=files.find(f=>!root||f.path.toLowerCase().startsWith(root+String.fromCharCode(92))||f.path.toLowerCase()===root);
 const file=scoped||files[0];
 const src=viewerImageSrc(candidate,file,id);
 const load=async url=>new Promise(resolve=>{
  const image=new Image();let settled=false;
  const finish=value=>{if(settled)return;settled=true;if(viewer.loader===image){viewer.loader=null;viewer.loaderCancel=null;}resolve(value);};
  viewer.loader=image;viewer.loaderCancel=()=>{image.onload=null;image.onerror=null;image.src='';finish(null);};
  image.onload=async()=>{try{if(typeof image.decode==='function')await image.decode();}catch(e){}finish(image.currentSrc||image.src);};
  image.onerror=()=>finish(false);image.src=url;
 });
 let ready=await load(src);
 if(ready===false&&!src.includes('/api/preview/'))ready=await load('/api/preview/'+id+'?v='+state.thumbRevision);
 if(request!==viewer.presentation||!$('#detail-dialog').open)return false;
 if(!ready){restoreDisplayedPosition();viewerImageFailed('下一张未能载入；仍可继续浏览或重试。');return false;}
 // Until this point the old image, detail, entity session and draft remain the
 // active object.  Commit all visible state only after the new image decodes.
 if(!await renderPhoto(id,prepared)||request!==viewer.presentation)return false;
 syncPhotoFavorite(state.detail);syncPhotoPlaceButton(state.detail);
 if(scoped)$('#detail-name').textContent=basename(scoped.path);
 $('#detail-dialog').dataset.photoId=String(id);
 const draft=viewer.drafts.get(id);if(draft)for(const [key,value] of Object.entries(draft))$(key).value=value;
 const applySrc=async(url)=>{
  if(!$('#detail-dialog').open||state.detail?.id!==id||request!==viewer.presentation)return false;
  const detailImage=$('#detail-img');
  const changing=Boolean(detailImage.getAttribute('src')&&!detailImage.hidden);
  viewer.fit=true;
  detailImage.classList.remove('viewer-photo-arriving','viewer-photo-forward','viewer-photo-backward');
  if(changing)void detailImage.offsetWidth;
  clearFaceNames();
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
  viewerMessage(draft?'这张照片有尚未保存的补录，已暂存在本页。':!state.detail.in_library?'此照片已退出展示':'');
  await waitViewerFrames(2);
  if(request===viewer.presentation&&$('#detail-dialog').open&&state.detail?.id===id)setViewerLoading(false);
  return true;
 };
 updatePosition();
 await applySrc(ready);
 return request===viewer.presentation;
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
 if(!wasOpen){viewer.returnScroll=scrollY;viewer.openedPhotoId=null;viewer.openedAbsolute=null;viewer.openedContext=null;viewer.openedWaterfallGeneration=null;viewer.exit=null;clearFaceNames();state.detail=null;delete $('#detail-dialog').dataset.photoId;if(!seedViewerFromThumb(id))clearViewerImage();}
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
  if(!alreadyOpen)showDialog('#detail-dialog');
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
 // An intent is current from the key/click, not only after the next decoder
 // starts.  This invalidates a slow intermediate candidate before it can commit.
 if(viewer.busy) viewer.presentation++;
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
$('#detail-img').addEventListener('load',()=>{
  updateSignaturePalette();
  updateZoom(true);
  // Seed thumbs can load before displayPhoto commits. Never paint labels from a
  // stale state.detail (previous photo) onto the image now on screen.
  const detailId=Number(state.detail&&state.detail.id)||0;
  const shownId=Number($('#detail-dialog').dataset.photoId)||0;
  if(detailId&&detailId===shownId)renderFaceNames(state.detail);
});
const faceLayer=$('#face-name-layer');
let faceLabelEditTimer=null;
function clearFaceLabelEditTimer(){
 clearTimeout(faceLabelEditTimer);
 faceLabelEditTimer=null;
}
function openFaceLabelEditor(button){
 const face=faceForLabel(button);
 if(faceHasUsableName(face))openFaceActionPopover(button,face);
 else if(typeof openQuickName==='function')openQuickName(Number(button.dataset.facePerson));
}
function scheduleUnidentifiedFaceLabelEditor(button){
 clearFaceLabelEditTimer();
 const photoId=Number(state.detail?.id),faceId=Number(button.dataset.faceId);
 faceLabelEditTimer=setTimeout(()=>{
   faceLabelEditTimer=null;
   if(
     performance.now()<suppressFaceLabelEditUntil
     ||Number(state.detail?.id)!==photoId
     ||!$('#detail-dialog')?.open
   )return;
   const current=faceLayer?.querySelector(`[data-face-id="${faceId}"]`);
   if(current&&!faceHasUsableName(faceForLabel(current)))openFaceLabelEditor(current);
 },220);
}
function finishFaceLabelDrag(event,cancelled=false){
 const current=faceLabelDrag;
 if(!current||event.pointerId!==current.pointerId)return;
 faceLabelDrag=null;
 current.button.classList.remove('is-dragging');
 try{current.button.releasePointerCapture(event.pointerId);}catch(error){}
 if(cancelled||!current.moved){
   if(cancelled){
     current.button.style.left=current.startLeft+'px';
     current.button.style.top=current.startTop+'px';
   }
   return;
 }
 suppressFaceLabelEditUntil=performance.now()+550;
 const image=$('#detail-img'),layer=$('#face-name-layer');
 const imageRect=image.getBoundingClientRect(),layerRect=layer.getBoundingClientRect();
 const left=parseFloat(current.button.style.left)||0;
 const top=parseFloat(current.button.style.top)||0;
 const x=Math.max(0,Math.min(1,(left+current.button.offsetWidth/2-(imageRect.left-layerRect.left))/imageRect.width));
 const y=Math.max(0,Math.min(1,(top+current.button.offsetHeight/2-(imageRect.top-layerRect.top))/imageRect.height));
 current.button.dataset.labelManual='true';
 current.button.classList.add('manual');
 void persistFaceLabelPosition(current.faceId,{x,y});
}
faceLayer&&faceLayer.addEventListener('pointerdown',e=>{
 const button=e.target.closest('[data-face-person]');
 if(!button||e.button!==0)return;
 e.stopPropagation();
 drag=null;
 const startLeft=parseFloat(button.style.left)||0,startTop=parseFloat(button.style.top)||0;
 faceLabelDrag={
   button,
   faceId:Number(button.dataset.faceId),
   pointerId:e.pointerId,
   startX:e.clientX,startY:e.clientY,startLeft,startTop,moved:false
 };
 button.setPointerCapture(e.pointerId);
});
faceLayer&&faceLayer.addEventListener('pointermove',e=>{
 const current=faceLabelDrag;
 if(!current||e.pointerId!==current.pointerId)return;
 const dx=e.clientX-current.startX,dy=e.clientY-current.startY;
 if(!current.moved&&Math.hypot(dx,dy)<5)return;
 current.moved=true;
 e.preventDefault();
 current.button.classList.add('is-dragging');
 const pad=4;
 const maxLeft=Math.max(pad,faceLayer.clientWidth-current.button.offsetWidth-pad);
 const maxTop=Math.max(pad,faceLayer.clientHeight-current.button.offsetHeight-pad);
 current.button.style.left=Math.min(Math.max(pad,current.startLeft+dx),maxLeft)+'px';
 current.button.style.top=Math.min(Math.max(pad,current.startTop+dy),maxTop)+'px';
 showFaceGuide(current.button);
});
faceLayer&&faceLayer.addEventListener('pointerup',e=>finishFaceLabelDrag(e));
faceLayer&&faceLayer.addEventListener('pointercancel',e=>finishFaceLabelDrag(e,true));
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
 if(performance.now()<suppressFaceLabelEditUntil)return;
 const face=faceForLabel(b);
 if(e.detail===0)openFaceLabelEditor(b);
 else if(faceHasUsableName(face))showFaceGuide(b);
 else scheduleUnidentifiedFaceLabelEditor(b);
});
faceLayer&&faceLayer.addEventListener('dblclick',e=>{
 const b=e.target.closest('[data-face-person]');
 if(!b)return;
 e.preventDefault();
 e.stopPropagation();
 outsidePhotoDown=false;
 clearFaceLabelEditTimer();
 if(performance.now()<suppressFaceLabelEditUntil)return;
 openFaceLabelEditor(b);
});
$('#toggle-face-names').addEventListener('click',()=>{viewer.faceNames=!viewer.faceNames;saveViewerPrefs();renderFaceNames(state.detail);});
$('#toggle-face-alias').addEventListener('click',()=>{const order=['off','with','only']; const cur=faceAliasMode(); viewer.faceAliasMode=order[(order.indexOf(cur)+1)%3]; viewer.faceAlias=viewer.faceAliasMode!=='off'; saveViewerPrefs();renderFaceNames(state.detail);});
$('#toggle-face-dir').addEventListener('click',()=>{const order=['auto','horizontal','vertical']; setFaceDirMode(order[(order.indexOf(faceDirMode())+1)%3]);});
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
$('#detail-dialog').addEventListener('close',()=>{clearFaceLabelEditTimer();viewer.exit={photoId:Number(state.detail?.id)||0,absolute:Number(viewer.absolute),context:viewer.context?{...viewer.context}:null,fallbackScroll:viewer.returnScroll,openedPhotoId:Number(viewer.openedPhotoId)||0,openedAbsolute:Number(viewer.openedAbsolute),openedContext:viewer.openedContext?{...viewer.openedContext}:null,waterfallGeneration:viewer.openedWaterfallGeneration};clearTimeout(viewer.closingTimer);viewer.closingTimer=null;$('#detail-dialog').classList.remove('viewer-closing');stopSlides();toggleFaceStylePopover(false);togglePhotoPeoplePopover(false);closeFaceActionPopover();clearFaceNames();state.detail=null;delete $('#detail-dialog').dataset.photoId;viewer.lastPasserbyBatch=null;viewer.generation++;viewer.presentation++;viewer.queued=null;viewer.goal=null;viewer.sequencePromise=null;renderPhoto.ticket++;if(viewer.loaderCancel)viewer.loaderCancel();setViewerLoading(false);if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});});
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
viewport.addEventListener('pointerdown',e=>{if(e.button!==0||e.target.closest('#photo-signature,.face-name,button,a,input,textarea,select'))return;drag={x:e.clientX,y:e.clientY,left:viewport.scrollLeft,top:viewport.scrollTop};viewport.setPointerCapture(e.pointerId);});
viewport.addEventListener('pointermove',e=>{if(drag){viewport.scrollLeft=drag.left+drag.x-e.clientX;viewport.scrollTop=drag.top+drag.y-e.clientY;}});
viewport.addEventListener('pointerup',()=>drag=null);viewport.addEventListener('pointercancel',()=>drag=null);
new ResizeObserver(()=>{if($('#detail-dialog').open)updateZoom();}).observe(viewport);
// Remember where a gesture began: dragging a zoomed photo onto the background
// must not close it. Buttons, links and the detail drawer keep their own actions.
let outsidePhotoDown=false;
 const keepOpenSelector='#detail-img,button,a,input,textarea,select,.detail-info,.face-name-layer,.face-name,.viewer-tools,.viewer-arrow,.dialog-close,#photo-mat,#photo-signature,#photo-people-hud,.signature-info-popover,.face-style-popover,.people-manage-popover,.face-action-popover';
$('#detail-dialog').addEventListener('pointerdown',e=>{
 outsidePhotoDown=e.button===0&&!e.target.closest(keepOpenSelector);
});
$('#detail-dialog').addEventListener('click',e=>{
 if(!outsidePhotoDown||e.target.closest(keepOpenSelector))return;
 outsidePhotoDown=false;closePhotoViewer();
});
$('#detail-dialog').addEventListener('close',()=>outsidePhotoDown=false);
$('#detail-form').addEventListener('input',()=>{stopSlides();const id=Number(state.detail.id);window.__ourTimeApp.draftRevision.photo.set(id,(window.__ourTimeApp.draftRevision.photo.get(id)||0)+1);viewer.drafts.set(id,Object.fromEntries(['#edit-date','#edit-precision','#edit-place','#edit-notes'].map(k=>[k,$(k).value])));viewerMessage('补录尚未保存；翻图时会暂存在本页，刷新页面会丢失。');});
$('#browse-directory').addEventListener('click',action(()=>{state.folderTarget='browse';return openFolder(state.directory);}));
