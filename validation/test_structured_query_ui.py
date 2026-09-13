from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / 'web'


def read(name):
    return (WEB / name).read_text(encoding='utf-8')


def main():
    app = read('app.js')
    html = read('index.html')
    ui = read('home-query-ui.js')
    init = read('home-query-init.js')
    css = read('home-query-ui.css')
    waterfall = read('waterfall.js')
    assert "$('#clear-people-selection').addEventListener('click',()=>setPeopleMerging(false));" in app
    assert 'function rememberPersonLabel' in app
    assert 'rememberPersonLabel(person)' in app
    assert "if(view==='people')return '人物档案';" in app
    assert "people:['人物档案','人物档案']" in app
    assert 'organizeNav.open=organize;' in app
    people = html[html.find('<section id="people-view"'):html.find('<section id="passersby-view"')]
    assert '<h2>人物档案</h2>' not in people
    assert 'id="people-merge-toggle"' in people
    assert 'id="group-result-count"' in html
    assert 'body.is-group-query .view-chrome .toolbar > .section-heading { display: none; }' in css
    assert 'body.is-people-view .view-chrome .toolbar > .section-heading { display: none; }' in css
    assert 'resolvePersonLabel' in ui
    assert 'rememberPersonLabel: rememberLabel' in ui
    assert 'data-ot="group"' in ui and '>合影人数</button>' in ui
    assert 'data-ot="group-min"' in ui and 'data-ot="group-max"' in ui
    assert "min + '-' + max" in ui and "'upto' + max" in ui and "min + 'plus'" in ui
    assert "'合影人数：' + groupLabel(s.group)" in ui
    assert 'applyGroup: async (group' in init
    assert 'data-ot="close"' in ui
    assert "if (destroyed) return;" in ui
    assert '.ot-home-popover .ot-home-close' in css
    assert '.ot-home-range-fields' in css and '.ot-home-number-field' in css
    assert '.ot-home-ui .ot-home-batch .ot-home-button { height: 42px; min-height: 42px; }' in css
    assert '[data-ot="cancel-selection"]' in css
    assert 'body.is-home-view #library-view > .view-chrome { padding-bottom: 6px; }' in css
    assert "if(view==='places'||isPhotoPlaceView(view))return $('#places-view');" in app
    assert 'function folderNormalize(' in app
    assert "list.dataset.currentPath=current;" in app
    assert 'async function loadExclusionRules(viewToken=null)' in app
    assert '__ourTimeRememberPersonLabel' in init
    assert "app.state.personLabels" in init
    assert 'function syncGroupResultCount' in app
    assert 'syncGroupResultCount(view,{clear:true})' in app
    assert 'syncGroupResultCount(state.view,{clear:true})' in app
    assert 'syncGroupResultCount(state.view,{clear:true})' in waterfall
    assert 'syncGroupResultCount(state.view,{total:data.total})' in waterfall
    assert "const groupCount=$('#group-result-count')" not in waterfall
    print('STRUCTURED_QUERY_UI_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
