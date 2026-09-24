"""Live-browser checks: nameplates follow the displayed photo and their text."""
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright

from validate_face_label_ivory import REPORT_DIR, URL, check, open_photo


ROOT = Path(__file__).resolve().parents[1]
REPORT = REPORT_DIR / "face-label-scale.json"
REFERENCE_PHOTO_HEIGHT = 900


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
        raise AssertionError("formal library has no mixed-size named group photo")
    return row


def synthetic_scale_result(page):
    return page.evaluate(
        """() => {
          viewer.faceStyle={...FACE_STYLE_PRESETS.ivory};
          viewer.faceDirMode='auto';
          viewer.faceVertical=true;
          applyFaceStyle();
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
          const small=makeFace(920001,'\u963f\u660e',ox+imageRect.width*.12,oy+imageRect.height*.18,48,60);
          const large=makeFace(920002,'\u963f\u5f3a',ox+imageRect.width*.58,oy+imageRect.height*.16,180,280);
          const latin=makeFace(920004,'Mark Zuckerberg',ox+imageRect.width*.18,oy+imageRect.height*.62,90,110);
          const unnamed=makeFace(920003,'',ox+imageRect.width*.42,oy+imageRect.height*.55,40,48,0);
          unnamed.name='';
          layoutFaceNameButtons(layer,[small,large,latin,unnamed],false);
          const measure=personId=>{
            const btn=layer.querySelector('[data-face-person="'+personId+'"]');
            const rect=btn.getBoundingClientRect();
            const style=getComputedStyle(btn);
            const plate=getComputedStyle(btn, '::before');
            const scale=Number(style.getPropertyValue('--face-scale')||btn.dataset.faceScale||1);
            return {
              text:btn.textContent,
              scale,
              width:rect.width,
              height:rect.height,
              fontSize:parseFloat(style.fontSize),
              paddingLeft:parseFloat(style.paddingLeft),
              paddingRight:parseFloat(style.paddingRight),
              paddingTop:parseFloat(style.paddingTop),
              horizontal:btn.classList.contains('is-horizontal'),
              writingMode:style.writingMode,
              overflow:style.overflow,
              unnamed:btn.classList.contains('unnamed'),
              borderSlice:plate.borderImageSlice||'',
              borderTop:parseFloat(plate.borderTopWidth)||0,
              borderLeft:parseFloat(plate.borderLeftWidth)||0,
              borderImage:(plate.borderImageSource||'') + ' ' + (plate.webkitBorderImage||''),
              scrollWidth:btn.scrollWidth,
              clientWidth:btn.clientWidth
            };
          };
          const displayed=imageRect.height;
          return {
            displayed,
            formula:{
              small:faceLabelScaleForHeight(40*REFERENCE_PHOTO_HEIGHT/displayed, displayed),
              cap:faceLabelScaleForHeight(280*REFERENCE_PHOTO_HEIGHT/displayed, displayed),
              huge:faceLabelScaleForHeight(800*REFERENCE_PHOTO_HEIGHT/displayed, displayed),
              tiny:faceLabelScaleForHeight(28, 220),
              half:faceLabelScaleForHeight(140, 450)
            },
            small:measure(920001),
            large:measure(920002),
            latin:measure(920004),
            unnamed:measure(920003)
          };
        }""".replace("REFERENCE_PHOTO_HEIGHT", str(REFERENCE_PHOTO_HEIGHT))
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
                const sourceH=box&&box.h?box.height/box.h*FACE_LABEL_REFERENCE_PHOTO_HEIGHT:0;
                const rect=btn.getBoundingClientRect();
                const vertical=faceLabelVerticalFor(btn.textContent);
                return {
                  name:btn.textContent,
                  sourceH,
                  vertical,
                  horizontal:btn.classList.contains('is-horizontal'),
                  scale:Number(btn.dataset.faceScale||1),
                  width:rect.width,
                  height:rect.height,
                  fontSize:parseFloat(getComputedStyle(btn).fontSize)
                };
              });
              const verticals=labels.filter(item=>item.vertical);
              const heights=verticals.map(item=>item.sourceH).filter(height=>height>0).sort((a,b)=>a-b);
              const mid=Math.floor(heights.length/2);
              const typical=heights.length?(heights[mid]+heights[Math.floor((heights.length-1)/2)])/2:0;
              const uniform=faceLabelScaleForHeight(typical, imageRect.height);
              return {
                imageHeight:imageRect.height,
                uniform,
                labels:labels.map(item=>({
                  ...item,
                  expected:item.vertical?uniform:faceLabelScaleForHeight(item.sourceH, imageRect.height)
                }))
              };
            }"""
        )
        screenshot = REPORT_DIR / "face-label-scale.png"
        page.locator("#photo-mat").screenshot(path=str(screenshot))
        evidence["screenshot"] = str(screenshot)
        evidence["real"] = {"asset_id": asset_id, "small_frac": small_frac, "large_frac": large_frac, "named_count": named_count, **real}
        synthetic = synthetic_scale_result(page)
        evidence["synthetic"] = synthetic
        photo = synthetic["displayed"] / REFERENCE_PHOTO_HEIGHT
        check(abs(synthetic["formula"]["small"] - photo) < 0.03, "a small face stays at the 1x floor before the photo scale", checks)
        check(abs(synthetic["formula"]["cap"] - 1.6 * photo) < 0.03, "large face caps face scale at 1.6 before photo scale", checks)
        check(abs(synthetic["formula"]["huge"] - 1.6 * photo) < 0.03, "close-up face still caps at 1.6 times photo scale", checks)
        check(abs(synthetic["formula"]["tiny"] - 0.12) < 0.03, "a small face on a much smaller photo follows the photo, down to the 0.12 floor", checks)
        check(abs(synthetic["formula"]["half"] - (450 / REFERENCE_PHOTO_HEIGHT)) < 0.03, "an ordinary face follows the displayed photo height", checks)
        shared = synthetic["small"]["scale"]
        photo = synthetic["displayed"] / REFERENCE_PHOTO_HEIGHT
        raw_small = max(1, 0.4 * 40 / 56)
        raw_large = min(1.6, 0.4 * 280 / 56)
        expected_shared = min(1.6, max(1, (raw_small + raw_large) / 2)) * max(0.12, photo)
        check(
            abs(shared - synthetic["large"]["scale"]) < 0.02
            and abs(shared - expected_shared) < 0.04,
            "vertical labels in one photo share the median face scale",
            checks,
        )
        check(
            synthetic["small"]["width"] > 20
            and synthetic["large"]["width"] > 20
            and abs(synthetic["large"]["width"] / synthetic["small"]["width"] - 1) < 0.18
            and abs(synthetic["large"]["fontSize"] / synthetic["small"]["fontSize"] - 1) < 0.08
            and synthetic["small"]["borderTop"] > synthetic["small"]["fontSize"] * 0.45
            and synthetic["small"]["borderLeft"] > synthetic["small"]["fontSize"] * 0.08
            and abs(synthetic["large"]["borderTop"] / synthetic["small"]["borderTop"] - synthetic["large"]["fontSize"] / synthetic["small"]["fontSize"]) < 0.08,
            "vertical plates and type share one scale and grow with their text",
            checks,
        )
        check(
            synthetic["latin"]["horizontal"] is True
            and synthetic["latin"]["writingMode"].startswith("horizontal")
            and synthetic["latin"]["width"] >= synthetic["latin"]["height"]
            and synthetic["latin"]["width"] > synthetic["latin"]["fontSize"] * 8
            and synthetic["latin"]["scrollWidth"] <= synthetic["latin"]["clientWidth"] + 1
            and "fill" in str(synthetic["latin"]["borderSlice"])
            and "%" not in str(synthetic["latin"]["borderSlice"])
            and synthetic["latin"]["borderLeft"] > synthetic["latin"]["fontSize"] * 0.45
            and synthetic["latin"]["borderTop"] > synthetic["latin"]["fontSize"] * 0.08
            and "1.png" in synthetic["latin"]["borderImage"],
            "a long Latin name stays horizontal and its plate contains the text",
            checks,
        )
        check(
            synthetic["unnamed"]["unnamed"] is True
            and synthetic["unnamed"]["width"] <= 26
            and synthetic["unnamed"]["height"] <= 26,
            "unnamed markers do not scale with the face",
            checks,
        )

        labels = real["labels"]
        check(len(labels) >= 2, "real group photo has at least two named labels", checks)
        check(all(abs(item["scale"] - item["expected"]) < 0.02 for item in labels), "real labels use face height times displayed photo height", checks)
        check(all(item["scale"] <= item["expected"] + 0.02 for item in labels), "real labels do not exceed the calculated scale", checks)
        vertical_scales = {round(item["scale"], 3) for item in labels if item["vertical"]}
        check(len(vertical_scales) <= 1, "real vertical labels share one scale", checks)
        check(not errors, "photo viewer has no page script errors", checks)
        browser.close()

    REPORT.write_text(json.dumps({"checks": checks, "evidence": evidence}, ensure_ascii=False, indent=2), encoding="utf-8")
    print("REPORT", REPORT, "checks", len(checks), "failed", 0, flush=True)


if __name__ == "__main__":
    main()
