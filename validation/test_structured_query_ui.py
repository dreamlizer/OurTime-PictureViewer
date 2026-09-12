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
    assert '__ourTimeRememberPersonLabel' in init
    assert "app.state.personLabels" in init
    assert "groupCount.textContent=fmt(data.total)+' 张'" in waterfall
    print('STRUCTURED_QUERY_UI_OK')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
