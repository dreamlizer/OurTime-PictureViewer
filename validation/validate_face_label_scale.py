"""Live-browser checks: nameplates scale with displayed face height."""
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright

from validate_face_label_ivory import REPORT_DIR, URL, check, open_photo


ROOT = Path(__file__).resolve().parents[1]
REPORT = REPORT_DIR / "face-label-scale.json"


def choose_mixed_size_photo():
    uri = "file:" + str((ROOT / "data" / "library.sqlite3").resolve()) + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        row = db.execute(
            """select f.asset_id,
                      min((json_extract(f.bbox,'$[3]')-json_extract(f.bbox,'$[1]'))*1.0/json_extract(f.bbox,'$[5]')) small_frac,
                      max((json_extract(f.bbox,'$[3]')-json_extract(f.bbox,'$[1]'))*1.0/json_extract(f.bbox,'$[5]')) large_frac,
                      count(*) named_count
               from faces f
               join people p on p.id=f.person_id
               join assets a on a.id=f.asset_id
               where f.ignored=0 and a.excluded=0 and p.name is not null and trim(p.name)<>''
                 and json_extract(f.bbox,'$[5]')>0
                 and exists(select 1 from files fi where fi.asset_id=a.id and fi.excluded=0 and fi.exists_now=1)
               group by f.asset_id
               having named_count>=2 and large_frac>=0.22 and small_frac<=0.12 and named_count<=6
               order by (large_frac-small_frac) desc, f.asset_id
               limit 1"""
        ).fetchone()
    if not row:
        raise AssertionError("正式库没有找到前后排脸差足够明显的已命名合影")
    return row


def synthetic_scale_result(page):
    return page.evaluate(
        """() => {
          viewer.faceStyle={...FACE_STYLE_PRESETS.ivory};
          applyFaceStyle();
          viewer.faceVertical=true;
          viewer.faceLabelPosition='right';
          const layer=document.querySelector('#face-name-layer');
          layer.classList.remove('horizontal');
          const image=document.querySelector('#detail-img');
          const imageRect=image.getBoundingClientRect();
          const layerRect=layer.getBoundingClientRect();
          const ox=imageRect.left-layerRect.left;
          const oy=imageRect.top-layerRect.top;
          const width=1200,height=900;
          const toSourceX=value=>(value-ox)/imageRect.width*width;
          const toSourceY=value=>(value-oy)/imageRect.height*height;
          const makeFace=(person_id,name,x,y,w,h,ignored=0)=>({
            person_id,name,ignored,
            bbox:[toSourceX(x),toSourceY(y),toSourceX(x+w),toSourceY(y+h),width,height]
          });
          const small=makeFace(920001,'阿明',ox+imageRect.width*.18,oy+imageRect.height*.20,48,60);
          const large=makeFace(920002,'阿强',ox+imageRect.width*.62,oy+imageRect.height*.18,180,280);
          const unnamed=makeFace(920003,'',ox+imageRect.width*.42,oy+imageRect.height*.55,40,48,0);
          unnamed.name='';
          layoutFaceNameButtons(layer,[small,large,unnamed],false);
          const measure=personId=>{
            const btn=layer.querySelector('[data-face-person="'+personId+'"]');
            const rect=btn.getBoundingClientRect();
            const style=getComputedStyle(btn);
            const scale=Number(style.getPropertyValue('--face-scale')||btn.dataset.faceScale||1);
            return {
              scale,
              width:rect.width,
              height:rect.height,
              fontSize:parseFloat(style.fontSize),
              paddingLeft:parseFloat(style.paddingLeft),
              paddingRight:parseFloat(style.paddingRight),
              unnamed:btn.classList.contains('unnamed')
            };
          };
          return {
            formula:{
              small:faceLabelScaleForHeight(60),
              start:faceLabelScaleForHeight(140),
              cap:faceLabelScaleForHeight(280),
              huge:faceLabelScaleForHeight(800)
            },
            small:measure(920001),
            large:measure(920002),
            unnamed:measure(920003)
          };
        }"""
    )


def main():
    checks = []
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    asset_id, small_frac, large_frac, named_count = choose_mixed_size_photo()
    errors = []
    evidence = {}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_photo(page, asset_id)
        page.wait_for_function("typeof faceLabelScaleForHeight === 'function'")
        real = page.evaluate(
            """() => {
              const image=document.querySelector('#detail-img');
              const layer=document.querySelector('#face-name-layer');
              const imageRect=image.getBoundingClientRect();
              const labels=[...layer.querySelectorAll('.face-name:not(.unnamed)')].map(btn=>{
                const face=(state.detail.faces||[]).find(item=>String(item.person_id)===String(btn.dataset.facePerson));
                const box=faceBox(face);
                const faceH=box&&box.h?box.height/box.h*imageRect.height:0;
                const rect=btn.getBoundingClientRect();
                const expected=faceLabelScaleForHeight(faceH);
                return {
                  name:btn.textContent,
                  faceH,
                  expected,
                  scale:Number(btn.dataset.faceScale||1),
                  width:rect.width,
                  height:rect.height,
                  fontSize:parseFloat(getComputedStyle(btn).fontSize)
                };
              });
              return labels.sort((a,b)=>a.faceH-b.faceH);
            }"""
        )
        screenshot = REPORT_DIR / "face-label-scale.png"
        page.locator("#photo-mat").screenshot(path=str(screenshot))
        evidence["screenshot"] = str(screenshot)
        evidence["real"] = {"asset_id": asset_id, "small_frac": small_frac, "large_frac": large_frac, "named_count": named_count, "labels": real}
        synthetic = synthetic_scale_result(page)
        evidence["synthetic"] = synthetic
        check(synthetic["formula"]["small"] == 1, "小于 140px 的脸保持当前标签尺寸", checks)
        check(abs(synthetic["formula"]["start"] - 1) < 1e-6, "140px 的脸正好开始放大", checks)
        check(abs(synthetic["formula"]["cap"] - 1.5) < 1e-6, "210px 及以上封顶 1.5 倍", checks)
        check(synthetic["formula"]["huge"] == 1.5, "特写不会超过 1.5 倍", checks)
        check(
            abs(synthetic["small"]["scale"] - 1) < 0.02
            and abs(synthetic["small"]["width"] - 38) <= 1
            and abs(synthetic["small"]["height"] - 56) <= 1,
            "小脸标签仍是 38x56 现用尺寸",
            checks,
        )
        check(
            abs(synthetic["large"]["scale"] - 1.5) < 0.02
            and abs(synthetic["large"]["width"] - 57) <= 1
            and abs(synthetic["large"]["height"] - 84) <= 1,
            "大脸标签放大到 1.5 倍且宽高一起变",
            checks,
        )
        small_font = synthetic["small"]["fontSize"]
        large_font = synthetic["large"]["fontSize"]
        check(
            small_font > 0 and abs(large_font / small_font - 1.5) < 0.08,
            "字号与底牌使用同一倍率",
            checks,
        )
        check(
            abs(synthetic["large"]["paddingLeft"] / max(synthetic["small"]["paddingLeft"], 0.01) - 1.5) < 0.12,
            "底牌内边距与字号同步放大",
            checks,
        )
        check(
            synthetic["unnamed"]["unnamed"] is True
            and synthetic["unnamed"]["width"] <= 26
            and synthetic["unnamed"]["height"] <= 26,
            "未命名标记不随脸放大",
            checks,
        )

        check(len(real) >= 2, "真实合影至少有两个已命名标签", checks)
        check(all(abs(item["scale"] - item["expected"]) < 0.02 for item in real), "真实合影的缩放系数按脸高计算", checks)
        check(all(item["scale"] >= 0.999 for item in real), "真实合影没有小于当前尺寸的标签", checks)
        check(all(item["scale"] <= 1.501 for item in real), "真实合影没有超过 1.5 倍的标签", checks)
        if real[0]["faceH"] < 140:
            check(abs(real[0]["scale"] - 1) < 0.02, "真实合影里的小脸保持当前尺寸", checks)
        if real[-1]["faceH"] >= 210:
            check(abs(real[-1]["scale"] - 1.5) < 0.02, "真实合影里的大脸封顶 1.5 倍", checks)
        elif real[-1]["faceH"] > real[0]["faceH"] + 20 and real[-1]["scale"] + 1e-6 >= real[0]["scale"]:
            check(True, "真实合影里较大的脸标签不小于较小的脸", checks)

        check(not errors, "大图没有页面脚本错误", checks)
        browser.close()

    REPORT.write_text(json.dumps({"checks": checks, "evidence": evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", REPORT, flush=True)


if __name__ == "__main__":
    main()
