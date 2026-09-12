#!/usr/bin/env python3
"""Isolated UI contract checks. Loads an inline, in-memory browser fixture; starts NO server.
No application backend, photo library, POST, scan, model, data/ or service control.
Requires preinstalled Python Playwright and a usable Chromium browser.
"""
from __future__ import annotations
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def run() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', help='Existing Chromium/Chrome/Edge executable; never installs one.')
    parser.add_argument('--output', default=str(ROOT / 'evidence' / 'browser-checks.local.json'))
    args = parser.parse_args()
    try:
        from playwright.sync_api import sync_playwright, expect
    except ImportError:
        print('SKIP: Python Playwright is not installed. No installation was attempted.')
        return 2
    markup = (ROOT / 'tests' / 'fixture.html').read_text(encoding='utf-8')
    css = (ROOT / 'files' / 'web' / 'home-query-ui.css').read_text(encoding='utf-8')
    js = (ROOT / 'files' / 'web' / 'home-query-ui.js').read_text(encoding='utf-8')
    markup = markup.replace('<link rel="stylesheet" href="../files/web/home-query-ui.css">', '<style>' + css + '</style>')
    markup = markup.replace('<script src="../files/web/home-query-ui.js"></script>', '<script>' + js + '</script>')
    bridge_js = (ROOT / 'files' / 'web' / 'home-query-bridge.js').read_text(encoding='utf-8')
    markup = markup.replace('<script src="../files/web/home-query-bridge.js"></script>', '<script>' + bridge_js + '</script>')
    checks = []
    errors = []
    non_get = []
    external = []
    started = time.time()
    try:
        with sync_playwright() as pw:
            executable = args.browser or shutil.which('chromium') or shutil.which('google-chrome') or shutil.which('msedge')
            kwargs = {'headless': True}
            if executable:
                kwargs['executable_path'] = executable
            browser = pw.chromium.launch(**kwargs)
            browser_version = browser.version
            def newpage(width=1280):
                page = browser.new_page(viewport={'width': width, 'height': 900}, locale='zh-CN')
                page.on('pageerror', lambda err: errors.append(str(err)))
                def request(req):
                    if req.method not in ('GET', 'HEAD'):
                        non_get.append(req.method + ' ' + req.url)
                    if not req.url.startswith('about:'):
                        external.append(req.url)
                page.on('request', request)
                page.set_content(markup, wait_until='load')
                page.wait_for_function('!!window.ui')
                return page
            def el(page, name):
                return page.locator(f'[data-ot="{name}"]')
            def state(page):
                return page.evaluate('fixture.state')
            def count(page, name):
                return page.evaluate('(name)=>calls(name).length', name)
            def click(page, name):
                el(page, name).click()
                page.wait_for_timeout(35)
            def case(name, fn):
                page = newpage()
                try:
                    fn(page)
                    checks.append({'name': name, 'status': 'PASS'})
                    print('PASS', name)
                except Exception as exc:
                    checks.append({'name': name, 'status': 'FAIL', 'error': str(exc)})
                    print('FAIL', name, str(exc))
                finally:
                    page.close()
            def default(p):
                expect(p.locator('dialog')).not_to_be_visible()
                expect(el(p, 'chips')).not_to_be_visible()
                expect(el(p, 'batch')).not_to_be_visible()
                expect(el(p, 'notice')).not_to_be_visible()
                expect(el(p, 'search')).to_be_visible()
                assert p.locator('input[type=date]').count() == 0
                assert len(p.locator('.ot-home-query > *').all()) == 4
                assert p.locator('#sentinel').evaluate('(e)=>e.getBoundingClientRect().height') == 42
            case('default_hidden_states_and_four_controls', default)
            def draft_cancel(p):
                click(p,'filters'); expect(p.locator('dialog')).to_be_visible()
                el(p,'person').select_option('11');el(p,'directory').fill('X:\\fixture\\one')
                assert count(p,'applyQuery') == 0
                click(p,'cancel')
                assert state(p)['query'] == {'q':'','person':'','directory':''}
                expect(p.locator('dialog')).not_to_be_visible()
            case('draft_changes_do_not_apply_on_cancel', draft_cancel)
            def atomic(p):
                p.evaluate("mutate({sort:'name_desc',query:{q:'fixture keyword',person:'',directory:''}})")
                click(p,'filters'); el(p,'person').select_option('11');el(p,'directory').fill('X:\\fixture\\one')
                click(p,'apply'); p.wait_for_timeout(50)
                assert count(p,'applyQuery') == 1
                assert state(p)['sort'] == 'name_desc'
                assert state(p)['query'] == {'q':'fixture keyword','person':'11','directory':'X:\\fixture\\one'}
                expect(p.locator('dialog')).not_to_be_visible()
                assert p.locator('[data-condition]').count() == 4
            case('atomic_filters_one_query_preserve_search_and_sort', atomic)
            def clear(p):
                p.evaluate("mutate({sort:'name_asc',query:{q:'word',person:'11',directory:'X:/fixture'}})")
                p.locator('[data-condition=person]').click();p.wait_for_timeout(50)
                assert state(p)['query']['directory'] == 'X:/fixture'
                assert state(p)['query']['person'] == ''
                p.locator('[data-condition=all]').click();p.wait_for_timeout(50)
                assert state(p)['query'] == {'q':'','person':'','directory':''}
                assert state(p)['sort'] == 'name_asc'
                expect(el(p,'chips')).not_to_be_visible()
            case('individual_and_all_clear_leave_sort_unchanged', clear)
            def reset(p):
                p.evaluate("mutate({query:{q:'word',person:'11',directory:'X:/fixture'}})")
                click(p,'filters');click(p,'reset-draft')
                assert state(p)['query']['person']=='11'
                click(p,'apply')
                assert state(p)['query']=={'q':'word','person':'','directory':''}
            case('reset_is_draft_only_and_keeps_keyword', reset)
            def folder(p):
                p.evaluate("fixture.folderResult='Y:/fixture/a';fixture.folderDelay=20")
                click(p,'filters');click(p,'choose-folder');p.wait_for_timeout(60)
                expect(el(p,'directory')).to_have_value('Y:/fixture/a')
                assert count(p,'applyQuery')==0
                click(p,'cancel'); assert state(p)['query']['directory']==''
            case('folder_picker_only_updates_draft_no_scan', folder)
            def cancel_picker(p):
                p.evaluate("mutate({query:{q:'',person:'',directory:'X:/existing'}});fixture.folderResult=null")
                click(p,'filters');click(p,'choose-folder')
                expect(el(p,'directory')).to_have_value('X:/existing')
            case('canceled_folder_picker_preserves_draft_path',cancel_picker)
            def people_search(p):
                click(p,'filters');el(p,'people-search').fill('人物乙');p.wait_for_timeout(50)
                assert el(p,'person').locator('option').count()==2
                el(p,'people-search').press('Enter');p.wait_for_timeout(50)
                expect(p.locator('dialog')).to_be_visible()
                assert count(p,'applyQuery')==0
            case('local_people_search_does_not_search_photos_or_submit',people_search)
            def ime(p):
                el(p,'search').dispatch_event('compositionstart')
                el(p,'search').fill('中');p.wait_for_timeout(400)
                assert count(p,'applyQuery')==0
                el(p,'search').fill('中文');el(p,'search').dispatch_event('compositionend');p.wait_for_timeout(450)
                assert count(p,'applyQuery')==1 and state(p)['query']['q']=='中文'
                el(p,'search').press('Enter');p.wait_for_timeout(50)
                assert count(p,'applyQuery')==1
            case('IME_and_debounce_no_duplicate_search',ime)
            def flush(p):
                el(p,'search').fill('pending');click(p,'filters')
                assert state(p)['query']['q']=='pending' and count(p,'applyQuery')==1
                expect(p.locator('dialog')).to_be_visible()
            case('opening_filter_flushes_pending_keyword_first',flush)
            def sort(p):
                p.evaluate("mutate({query:{q:'word',person:'12',directory:'X:/fixture'}})")
                el(p,'sort').select_option('date_asc');p.wait_for_timeout(80)
                assert state(p)['sort']=='date_asc' and state(p)['query']['person']=='12'
                assert count(p,'applySort')==1 and count(p,'applyQuery')==0
            case('sorting_and_filters_are_independent',sort)
            def batch(p):
                p.evaluate("mutate({query:{q:'word',person:'11',directory:''}})")
                click(p,'select');expect(el(p,'search')).not_to_be_visible();expect(el(p,'batch')).to_be_visible()
                expect(el(p,'edit')).to_be_disabled();click(p,'visible')
                expect(el(p,'selected-count')).to_have_text('已选择 3 张')
                assert p.locator('[data-condition=person]').is_disabled()
                click(p,'edit');click(p,'exclude')
                assert count(p,'editSelected')==1 and count(p,'excludeSelected')==1
                click(p,'clear-selection');assert state(p)['selectedCount']==0
                click(p,'cancel-selection');assert not state(p)['selecting']
                expect(el(p,'search')).to_be_visible()
            case('batch_selection_delegates_existing_actions_no_delete',batch)
            def restore(p):
                p.evaluate("mutate({selectionKind:'restore',selecting:true,selectedCount:2})")
                expect(el(p,'restore')).to_be_visible();expect(el(p,'exclude')).not_to_be_visible();expect(el(p,'edit')).not_to_be_visible()
                click(p,'restore');assert count(p,'restoreSelected')==1
            case('excluded_context_exposes_restore_not_exclude',restore)
            def query_error(p):
                p.evaluate('fixture.failQuery=true');click(p,'filters');el(p,'person').select_option('11');click(p,'apply')
                expect(p.locator('dialog')).to_be_visible();expect(el(p,'filter-error')).to_contain_text('Synthetic query failure')
                assert state(p)['query']['person']==''
                click(p,'cancel');expect(p.locator('dialog')).not_to_be_visible()
            case('failed_apply_keeps_drawer_and_authoritative_state',query_error)
            def busy(p):
                p.evaluate('fixture.delay=350');click(p,'filters');el(p,'person').select_option('11');click(p,'apply')
                expect(el(p,'apply')).to_be_disabled();expect(el(p,'cancel')).to_be_disabled()
                p.keyboard.press('Escape');expect(p.locator('dialog')).to_be_visible()
                p.wait_for_timeout(420);assert count(p,'applyQuery')==1
                expect(p.locator('dialog')).not_to_be_visible()
            case('busy_guard_prevents_double_apply_and_ambiguous_cancel',busy)
            def late_people(p):
                p.evaluate('fixture.peopleDelay=250');click(p,'filters')
                p.evaluate("mutate({active:false,scopeKey:'people'})");p.wait_for_timeout(320)
                expect(p.locator('dialog')).not_to_be_visible();expect(p.locator('.ot-home-ui')).not_to_be_visible()
                assert count(p,'applyQuery')==0
            case('navigation_closes_drawer_and_discards_late_people',late_people)
            def late_folder(p):
                p.evaluate("fixture.folderDelay=220;fixture.folderResult='X:/late'")
                click(p,'filters');click(p,'choose-folder');click(p,'cancel');click(p,'filters');p.wait_for_timeout(280)
                expect(el(p,'directory')).to_have_value('')
            case('late_folder_response_cannot_write_reopened_drawer',late_folder)
            def stale(p):
                click(p,'filters');el(p,'person').select_option('11')
                p.evaluate("mutate({query:{q:'externally changed',person:'',directory:''}})")
                click(p,'apply');expect(el(p,'filter-error')).to_contain_text('当前查询范围已变化')
                assert count(p,'applyQuery')==0
            case('stale_draft_is_rejected_instead_of_overwriting_state',stale)
            def unknown(p):
                p.evaluate("mutate({query:{q:'',person:'11,999',directory:''}});fixture.failPeople=true")
                click(p,'filters');p.wait_for_timeout(80)
                expect(el(p,'person')).to_have_value('11,999')
                el(p,'directory').fill('X:/fixture');click(p,'apply')
                assert state(p)['query']['person']=='11,999'
            case('failed_people_load_preserves_unknown_or_multi_person_id',unknown)
            def cap(p):
                p.evaluate("fixture.people=Array.from({length:5201},(_,i)=>({id:String(i+1),label:'测试人物 '+(i+1)}))")
                click(p,'filters');p.wait_for_timeout(60)
                assert el(p,'person').locator('option').count()==201
                expect(el(p,'people-status')).to_contain_text('5201')
                el(p,'people-search').fill('测试人物 5201')
                assert el(p,'person').locator('option').count()==2
            case('large_people_catalog_is_locally_searchable_and_bounded',cap)
            def latest(p):
                p.evaluate('fixture.delay=550')
                el(p,'search').fill('first');p.wait_for_timeout(360)
                el(p,'search').fill('latest');p.wait_for_timeout(1350)
                assert state(p)['query']['q']=='latest' and count(p,'applyQuery')==2
                expect(el(p,'search')).to_have_value('latest')
            case('slow_search_serializes_and_preserves_latest_typed_text',latest)
            def destroy(p):
                click(p,'filters');p.evaluate('ui.destroy()')
                assert p.locator('.ot-home-ui').count()==0 and p.locator('.ot-home-filter-dialog').count()==0
                p.evaluate("ui=OurTimeHomeUI.mount({host:document.querySelector('#home-host'),adapter:fixtureAdapter})")
                expect(el(p,'search')).to_be_visible()
            case('destroy_is_clean_and_allows_explicit_remount',destroy)
            def invalid(p):
                result=p.evaluate("""()=>{const h=document.createElement('div');document.body.append(h);try{OurTimeHomeUI.mount({host:h,adapter:{}})}catch(e){return {error:e.message,children:h.childElementCount,dialogs:document.querySelectorAll('.ot-home-filter-dialog').length}}}""")
                assert 'missing' in result['error'] and result['children']==0 and result['dialogs']==1
            case('missing_adapter_fails_before_mutating_host',invalid)
            def xss(p):
                p.evaluate("fixture.people=[{id:'77',label:'<img src=x onerror=alert(1)>'}];mutate({query:{q:'<svg onload=alert(1)>',person:'77',directory:'X:/<b>fixture</b>'}})")
                click(p,'filters');p.wait_for_timeout(60)
                assert p.locator('.ot-home-ui img,.ot-home-filter-dialog img').count()==0
                expect(el(p,'person')).to_contain_text('<img src=x onerror=alert(1)>')
                assert p.locator('.ot-home-chip svg').count()==0
            case('untrusted_names_keywords_and_paths_are_plain_text',xss)
            def invalid_sort(p):
                result=p.evaluate("""()=>{const h=document.createElement('div');try{OurTimeHomeUI.mount({host:h,adapter:{...fixtureAdapter,readState:()=>({...fixture.state,sort:'bogus'})}})}catch(e){return {error:e.message,children:h.childElementCount}}}""")
                assert 'Unsupported' in result['error'] and result['children']==0
            case('unknown_sort_fails_closed_instead_of_silent_remapping',invalid_sort)
            def focus(p):
                click(p,'filters');expect(el(p,'people-search')).to_be_focused()
                p.keyboard.press('Escape');expect(el(p,'filters')).to_be_focused()
                click(p,'filters');p.mouse.click(10,450);expect(p.locator('dialog')).not_to_be_visible()
            case('native_dialog_focus_Escape_and_backdrop_close',focus)
            def refresh(p):
                p.evaluate('mutate({refreshAvailable:true})');expect(el(p,'notice')).to_be_visible()
                click(p,'refresh');assert count(p,'refresh')==1;expect(el(p,'notice')).not_to_be_visible()
                p.evaluate('mutate({refreshAvailable:true,selecting:true})');expect(el(p,'notice')).not_to_be_visible()
            case('refresh_only_when_host_has_real_updates_and_not_selecting',refresh)
            def path(p):
                p.evaluate("mutate({sort:'name_desc'})");click(p,'filters')
                el(p,'directory').fill('  X:\\Test Folder\\Sub\\  ');click(p,'apply')
                assert state(p)['query']['directory']=='X:\\Test Folder\\Sub\\'
                assert state(p)['sort']=='name_desc'
            case('directory_preserves_internal_path_and_never_changes_sort',path)
            def nav_search(p):
                el(p,'search').fill('pending');p.evaluate("mutate({scopeKey:'years',active:false})");p.wait_for_timeout(400)
                assert count(p,'applyQuery')==0
            case('navigation_cancels_not_yet_applied_keyword',nav_search)
            def layouts(p):
                for width in (1440,1280,768,390,320):
                    p.set_viewport_size({'width':width,'height':900})
                    for batch in (False,True):
                        p.evaluate('(selecting)=>mutate({selecting,selectedCount:3})',batch)
                        assert p.evaluate('document.documentElement.scrollWidth<=innerWidth'), (width,batch,'page overflow')
                        assert p.locator('#sentinel').evaluate('(e)=>e.getBoundingClientRect().height')==42
                    p.evaluate('mutate({selecting:false})');click(p,'filters')
                    rect=p.locator('dialog').bounding_box(); assert rect and abs(rect['x']+rect['width']-width)<=1
                    for selector in ['apply','cancel','directory']:
                        box=el(p,selector).bounding_box()
                        assert box and box['x']>=0 and box['x']+box['width']<=width+1,(width,selector)
                    click(p,'cancel')
            case('responsive_1440_1280_768_390_320_and_caption_scope',layouts)
            def setup_bridge(p):
                p.evaluate("""() => {
                  window.bs={view:'timeline',q:'seed',person:'11',directory:'X:/seed',sort:'name_desc',selecting:false,selected:new Set(),detail:{sentinel:true}};
                  window.br=[];window.brFail=false;window.brDelay=0;window.brNotify=0;
                  window.bi={q:document.createElement('input'),person:document.createElement('select'),directory:document.createElement('input'),sort:document.createElement('select')};
                  bi.person.add(new Option('test','12'));bi.sort.add(new Option('sort','name_desc'));
                  window.bbindings={source:bs,isActive:s=>s.view==='timeline',scopeKey:s=>s.view,legacyInputs:bi,
                    async reloadPhotos(info){br.push(info.reason);await new Promise(r=>setTimeout(r,brDelay));if(brFail)throw Error('fixture reload failed')},
                    getPeople:()=>[{id:'11',label:'test'}],setSelecting:on=>{bs.selecting=on},selectVisible:()=>{bs.selected.add(8)},
                    clearSelection:()=>bs.selected.clear(),editSelected:()=>br.push('edit'),excludeSelected:()=>br.push('exclude'),
                    onStateChange:()=>{brNotify++}};
                  window.bridge=OurTimeHomeBridge.create(bbindings);
                  window.bctx=()=>({expectedScopeKey:bs.view,expectedQuery:{q:bs.q,person:bs.person,directory:bs.directory},signal:new AbortController().signal});
                }""")
            def bridge_atomic(p):
                setup_bridge(p)
                p.evaluate("async()=>{await bridge.applyQuery({q:'next',person:'12',directory:'Y:/next'},bctx())}")
                actual=p.evaluate("({q:bs.q,person:bs.person,directory:bs.directory,sort:bs.sort,detail:bs.detail,br})")
                assert actual=={'q':'next','person':'12','directory':'Y:/next','sort':'name_desc','detail':{'sentinel':True},'br':['query']}
            case('bridge_atomic_flat_state_update_one_existing_reload',bridge_atomic)
            def bridge_inputs(p):
                setup_bridge(p)
                p.evaluate("window.inputEvents=0;Object.values(bi).forEach(e=>e.addEventListener('change',()=>inputEvents++))")
                p.evaluate("async()=>{await bridge.applyQuery({q:'next',person:'12',directory:'Y:/next'},bctx())}")
                assert p.evaluate("({q:bi.q.value,person:bi.person.value,events:inputEvents})")=={'q':'next','person':'12','events':0}
            case('bridge_legacy_values_sync_without_duplicate_change_events',bridge_inputs)
            def bridge_fail(p):
                setup_bridge(p);p.evaluate('brFail=true')
                result=p.evaluate("async()=>{try{await bridge.applyQuery({q:'bad',person:'12',directory:'Y:/bad'},bctx())}catch(e){return {q:bs.q,person:bs.person,directory:bs.directory,sort:bs.sort,error:e.message}}}")
                assert result=={'q':'seed','person':'11','directory':'X:/seed','sort':'name_desc','error':'fixture reload failed'}
            case('bridge_failed_query_rolls_back_its_own_applied_fields',bridge_fail)
            def bridge_stale(p):
                setup_bridge(p)
                result=p.evaluate("async()=>{const c=bctx();bs.q='changed';try{await bridge.applyQuery({q:'bad',person:'',directory:''},c)}catch(e){return {q:bs.q,reloads:br.length,error:e.message}}}")
                assert result['q']=='changed' and result['reloads']==0 and '更新' in result['error']
            case('bridge_stale_query_rejected_before_mutation_or_reload',bridge_stale)
            def bridge_navigation(p):
                setup_bridge(p)
                result=p.evaluate("async()=>{const c=bctx();bs.view='people';try{await bridge.applySort('date_desc',c)}catch(e){return {sort:bs.sort,reloads:br.length}}}")
                assert result=={'sort':'name_desc','reloads':0}
            case('bridge_inactive_scope_blocks_changes',bridge_navigation)
            def bridge_external(p):
                setup_bridge(p)
                result=p.evaluate("async()=>{brFail=true;brDelay=100;const task=bridge.applyQuery({q:'next',person:'12',directory:'Y:/next'},bctx());bs.q='external';try{await task}catch(e){return {q:bs.q,person:bs.person,directory:bs.directory}}}")
                assert result['q']=='external'
            case('bridge_rollback_does_not_overwrite_newer_external_query',bridge_external)
            def bridge_sort(p):
                setup_bridge(p)
                p.evaluate("async()=>await bridge.applySort('date_asc',bctx())")
                assert p.evaluate("({q:bs.q,person:bs.person,directory:bs.directory,sort:bs.sort,br})")=={'q':'seed','person':'11','directory':'X:/seed','sort':'date_asc','br':['sort']}
            case('bridge_sort_uses_existing_reload_preserves_query',bridge_sort)
            def bridge_selection(p):
                setup_bridge(p)
                result=p.evaluate("async()=>{let guarded=false;try{await bridge.excludeSelected(bctx())}catch(e){guarded=true}await bridge.setSelecting(true,bctx());await bridge.selectVisible(bctx());await bridge.excludeSelected(bctx());return {guarded,count:bridge.readState().selectedCount,br}}")
                assert result=={'guarded':True,'count':1,'br':['exclude']}
            case('bridge_selection_reuses_existing_set_and_guards_empty_action',bridge_selection)
            def bridge_full_ui(p):
                setup_bridge(p)
                p.evaluate("""()=>{ui.destroy();bbindings.onStateChange=()=>window.ui?.sync();bridge=OurTimeHomeBridge.create(bbindings);ui=OurTimeHomeUI.mount({host:document.querySelector('#home-host'),adapter:bridge});}""")
                click(p,'filters');el(p,'directory').fill('Y:/integrated');click(p,'apply')
                assert p.evaluate('bs.directory')=='Y:/integrated' and p.evaluate('bs.sort')=='name_desc'
                assert p.evaluate('br.length')==1
            case('new_UI_and_bridge_work_together_against_flat_state_contract',bridge_full_ui)
            checks.append({'name':'no_browser_errors_or_external_or_write_requests','status':'PASS' if not errors and not non_get and not external else 'FAIL'})
            browser.close()
    except Exception as exc:
        checks.append({'name':'test_environment','status':'FAIL','error':str(exc)})
    result={
      'suite':'OurTime homepage Phase 1 isolated module contract',
      'scope':'Synthetic adapter + in-memory fixture only; NOT real repo/backend/live-library verification.',
      'browser':locals().get('browser_version','unavailable'),
      'elapsed_seconds':round(time.time()-started,2),
      'checks':checks,'page_errors':errors,'non_get_requests':non_get,'external_requests':external,
      'pass_count':sum(c['status']=='PASS' for c in checks),
      'fail_count':sum(c['status']=='FAIL' for c in checks)
    }
    output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('pass_count','fail_count','elapsed_seconds')},ensure_ascii=False))
    return 1 if result['fail_count'] else 0

if __name__=='__main__':
    sys.exit(run())
