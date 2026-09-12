"""Live-browser checks for unnamed-person markers and label collision limits."""
import json
import sqlite3
from pathlib import Path

from playwright.sync_api import sync_playwright

from validate_face_label_ivory import REPORT_DIR, URL, check, open_photo


ROOT = Path(__file__).resolve().parents[1]
REPORT = REPORT_DIR / "face-marker-collision.json"


def choose_mixed_photo():
    uri = "file:" + str((ROOT / "data" / "library.sqlite3").resolve()) + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        row = db.execute(
            """select f.asset_id,
                      sum(case when p.name is not null and trim(p.name)<>'' then 1 else 0 end) named_count,
                      sum(case when p.name is null or trim(p.name)='' then 1 else 0 end) unnamed_count,
                      count(*) face_count
               from faces f
               join people p on p.id=f.person_id
               join assets a on a.id=f.asset_id
               where f.ignored=0 and a.excluded=0
                 and exists(select 1 from files fi where fi.asset_id=a.id and fi.excluded=0 and fi.exists_now=1)
               group by f.asset_id
               having named_count>=2 and unnamed_count>=1 and face_count<=8
               order by face_count desc,f.asset_id
               limit 1"""
        ).fetchone()
    if not row:
        raise AssertionError("正式库没有找到同时含两位已命名人物和待命名人物的照片")
    return row


def synthetic_collision_result(page, vertical):
    return page.evaluate(
        """vertical => {
          viewer.faceStyle={...(vertical?FACE_STYLE_PRESETS.tea:FACE_STYLE_PRESETS.classic)};
          applyFaceStyle();
          viewer.faceVertical=vertical;
          const layer=document.querySelector('#face-name-layer');
          layer.classList.toggle('horizontal',!vertical);
          const width=Number(state.detail?.width)||2000;
          const height=Number(state.detail?.height)||1400;
          const names=['小明','欧阳明','欧阳娜娜','王芳','李晓华','陈嘉禾','周明','赵小雨','孙文博','许惠英'];
          const faces=names.map((name,index)=>{
            const column=(index%3)-1;
            const row=Math.floor(index/3)-1;
            const cx=width*(.5+column*.006);
            const cy=height*(.42+row*.006);
            const fw=width*.075;
            const fh=height*.14;
            return {
              person_id:900000+index,
              name,
              ignored:0,
              bbox:[cx-fw/2,cy-fh/2,cx+fw/2,cy+fh/2,width,height]
            };
          });
          layoutFaceNameButtons(layer,faces,false);
          const labels=[...layer.querySelectorAll('.face-name')].map(label=>{
            const rect=label.getBoundingClientRect();
            const layerRect=layer.getBoundingClientRect();
            return {
              x:rect.left-layerRect.left,
              y:rect.top-layerRect.top,
              w:rect.width,
              h:rect.height,
              reported:Number(label.dataset.labelOverlapRatio)
            };
          });
          const overlap=(a,b)=>{
            const w=Math.max(0,Math.min(a.x+a.w,b.x+b.w)-Math.max(a.x,b.x));
            const h=Math.max(0,Math.min(a.y+a.h,b.y+b.h)-Math.max(a.y,b.y));
            return w*h/Math.min(a.w*a.h,b.w*b.h);
          };
          let maximum=0;
          labels.forEach((label,index)=>labels.forEach((other,otherIndex)=>{
            if(index<otherIndex)maximum=Math.max(maximum,overlap(label,other));
          }));
          return {
            count:labels.length,
            maximum,
            reportedMaximum:Math.max(...labels.map(label=>label.reported)),
            inside:labels.every(label=>label.x>=3&&label.y>=3&&label.x+label.w<=layer.clientWidth-3&&label.y+label.h<=layer.clientHeight-3)
          };
        }""",
        vertical,
    )


def synthetic_face_overlap_result(page, overlap_pixels):
    return page.evaluate(
        """overlapPixels => {
          viewer.faceStyle={...FACE_STYLE_PRESETS.tea};
          viewer.faceVertical=true;
          viewer.faceLabelPosition='left';
          applyFaceStyle();
          const layer=document.querySelector('#face-name-layer');
          layer.classList.remove('horizontal');
          const image=document.querySelector('#detail-img');
          const imageRect=image.getBoundingClientRect();
          const layerRect=layer.getBoundingClientRect();
          const ox=imageRect.left-layerRect.left;
          const oy=imageRect.top-layerRect.top;
          const width=1000,height=800;
          const toSourceX=value=>(value-ox)/imageRect.width*width;
          const toSourceY=value=>(value-oy)/imageRect.height*height;
          const faceWidth=50,faceHeight=60;
          const firstX=ox+imageRect.width*.38;
          const faceY=oy+imageRect.height*.42;
          const secondX=firstX+faceWidth+38+6-overlapPixels;
          const makeFace=(person_id,name,x)=>({
            person_id,name,ignored:0,
            bbox:[
              toSourceX(x),toSourceY(faceY),
              toSourceX(x+faceWidth),toSourceY(faceY+faceHeight),
              width,height
            ]
          });
          const faces=[
            makeFace(910001,'甲乙',firstX),
            makeFace(910002,'丙丁',secondX)
          ];
          layoutFaceNameButtons(layer,faces,false);
          const label=layer.querySelector('[data-face-person="910002"]');
          const labelRect=label.getBoundingClientRect();
          const actualLabel={
            x:labelRect.left-layerRect.left,y:labelRect.top-layerRect.top,
            w:labelRect.width,h:labelRect.height
          };
          const firstFace={x:firstX,y:faceY,w:faceWidth,h:faceHeight};
          const secondFace={x:secondX,y:faceY,w:faceWidth,h:faceHeight};
          const initialLabel={
            x:secondX-actualLabel.w-6,
            y:faceY+faceHeight/2-actualLabel.h/2,
            w:actualLabel.w,h:actualLabel.h
          };
          return {
            initialRatio:rectOverlapRatio(initialLabel,firstFace),
            finalRatio:rectOverlapRatio(actualLabel,firstFace),
            centerOffset:
              actualLabel.y+actualLabel.h/2-(secondFace.y+secondFace.h/2),
            side:label.classList.contains('left')?'left':'right'
          };
        }""",
        overlap_pixels,
    )


def main():
    checks = []
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    asset_id, named_count, unnamed_count, face_count = choose_mixed_photo()

    errors = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="chrome", headless=True)
        page = browser.new_page(viewport={"width": 1500, "height": 1000}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        open_photo(page, asset_id)
        if page.locator("#face-style-popover").is_hidden():
            page.click("#face-style-button")

        marker_options = page.locator("#face-unnamed-marker option").all_text_contents()
        check(
            marker_options == ["默认加号", "呼吸绿点", "静态绿环"],
            "人名标签菜单提供三种待命名标记",
            checks,
        )
        check(
            page.locator("#face-unnamed-marker").input_value() == "plus"
            and page.locator("#detail-dialog").get_attribute("data-unnamed-marker") == "plus",
            "待命名标记默认使用加号",
            checks,
        )
        unnamed = page.locator("#face-name-layer .face-name.unnamed").first
        check(unnamed.is_visible(), "真实照片中待命名人物标记可见", checks)
        plus_style = unnamed.evaluate(
            """element => ({
              text:element.textContent,
              opacity:getComputedStyle(element).opacity,
              background:getComputedStyle(element).backgroundColor,
              color:getComputedStyle(element).color
            })"""
        )
        check(
            plus_style["text"] == "+"
            and float(plus_style["opacity"]) >= 0.75
            and plus_style["background"] != "rgba(0, 0, 0, 0)",
            "默认加号提高了对比度，不再因过度透明而难找",
            checks,
        )

        page.select_option("#face-unnamed-marker", "pulse")
        page.wait_for_function(
            "() => document.querySelector('#detail-dialog')?.dataset.unnamedMarker==='pulse'"
        )
        pulse_style = unnamed.evaluate(
            """element => {
              const dot=getComputedStyle(element,'::before');
              return {background:dot.backgroundColor,animation:dot.animationName,width:dot.width};
            }"""
        )
        check(
            pulse_style["background"] == "rgb(114, 167, 123)"
            and pulse_style["animation"] == "face-unnamed-pulse"
            and pulse_style["width"] == "10px",
            "呼吸绿点使用克制的绿色和轻微闪动",
            checks,
        )
        page.screenshot(path=str(REPORT_DIR / "face-marker-pulse.png"), full_page=False)

        page.select_option("#face-unnamed-marker", "ring")
        page.wait_for_function(
            "() => document.querySelector('#detail-dialog')?.dataset.unnamedMarker==='ring'"
        )
        ring_style = unnamed.evaluate(
            """element => {
              const ring=getComputedStyle(element,'::before');
              return {border:ring.borderStyle,width:ring.width,animation:ring.animationName};
            }"""
        )
        check(
            ring_style["border"] == "solid"
            and ring_style["width"] == "12px"
            and ring_style["animation"] == "none",
            "静态绿环提供无动画的清晰替代方案",
            checks,
        )
        check(
            page.evaluate(
                """() => {
                  const saved=JSON.parse(localStorage.getItem('ourtime.viewer.preferences.v2')||'{}');
                  return saved.faceUnnamedMarker==='ring';
                }"""
            ),
            "待命名标记选择会保存",
            checks,
        )

        quick_ignore_requests = []

        def fulfill_quick_ignore(route):
            quick_ignore_requests.append(json.loads(route.request.post_data or "{}"))
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"ok":true,"ignored":true}',
            )

        synthetic_person_id = 990001
        page.route(f"**/api/people/{synthetic_person_id}/ignore", fulfill_quick_ignore)
        page.evaluate(
            """personId => {
              const width=Number(state.detail?.width)||1600;
              const height=Number(state.detail?.height)||1200;
              const face={
                id:personId,person_id:personId,name:'',alias:'',ignored:0,
                bbox:[width*.18,height*.22,width*.25,height*.36,width,height]
              };
              state.detail.faces.push(face);
              state.people.push({id:personId,name:'',alias:'',ignored:0,confirmed:0});
              renderFaceNames(state.detail);
            }""",
            synthetic_person_id,
        )
        synthetic_marker = page.locator(
            f'#face-name-layer [data-face-person="{synthetic_person_id}"]'
        )
        synthetic_marker.click()
        check(
            page.locator("#quick-name-dialog").get_attribute("open") is not None
            and page.locator("#quick-ignore-person").is_visible(),
            "点击照片中的待命名标记后，可直接选择“这是路人”",
            checks,
        )
        page.click("#quick-ignore-person")
        page.wait_for_function("!document.querySelector('#quick-name-dialog').open")
        passerby_marker = page.locator(
            f'#face-name-layer [data-face-person="{synthetic_person_id}"]'
        )
        passerby_style = passerby_marker.evaluate(
            """element => {
              const pseudo=getComputedStyle(element,'::before');
              const style=getComputedStyle(element);
              return {
                text:element.textContent,
                className:element.className,
                opacity:Number(style.opacity),
                width:style.width,
                pseudoContent:pseudo.content,
                pseudoAnimation:pseudo.animationName
              };
            }"""
        )
        check(
            quick_ignore_requests == [{"ignored": True}],
            "快捷路人操作调用人物组级 ignore 接口",
            checks,
        )
        check(
            passerby_style["text"] == "+"
            and "passerby" in passerby_style["className"]
            and passerby_style["opacity"] < float(plus_style["opacity"])
            and passerby_style["width"] == "22px"
            and passerby_style["pseudoAnimation"] == "none",
            "路人无论选了绿点或绿环，都只显示更淡的小加号",
            checks,
        )
        check(
            page.evaluate(
                """personId => {
                  const face=state.detail.faces.find(item=>Number(item.person_id)===personId);
                  return Boolean(face&&face.ignored);
                }""",
                synthetic_person_id,
            ),
            "标为路人后当前照片立即更新，无需重新打开",
            checks,
        )
        passerby_marker.click()
        check(
            page.locator("#quick-ignore-person").is_hidden()
            and page.locator("#quick-name-title").text_content() == "给这位路人命名",
            "淡色路人加号仍可重新命名，但不重复显示路人操作",
            checks,
        )
        page.locator('[data-close="quick-name-dialog"]').last.click()
        page.screenshot(path=str(REPORT_DIR / "face-marker-passerby.png"), full_page=False)

        tolerated_face_overlap = synthetic_face_overlap_result(page, 4)
        check(
            0 < tolerated_face_overlap["initialRatio"] <= 0.25
            and abs(tolerated_face_overlap["centerOffset"]) <= 1
            and tolerated_face_overlap["side"] == "left",
            "标签擦到其他人脸不超过 25% 时仍贴近本人居中显示",
            checks,
        )
        forced_face_overlap = synthetic_face_overlap_result(page, 12)
        check(
            forced_face_overlap["initialRatio"] > 0.25
            and forced_face_overlap["finalRatio"] <= 0.25
            and abs(forced_face_overlap["centerOffset"]) > 1,
            "标签与其他人脸重叠超过 25% 时仍会强制排开",
            checks,
        )

        vertical_result = synthetic_collision_result(page, True)
        check(
            vertical_result["count"] == 10
            and vertical_result["inside"]
            and vertical_result["maximum"] <= 0.12
            and vertical_result["reportedMaximum"] <= 0.12,
            "十个密集竖排标签被强制排开，重叠面积不超过 12%",
            checks,
        )
        page.screenshot(path=str(REPORT_DIR / "face-label-collision-vertical.png"), full_page=False)

        horizontal_result = synthetic_collision_result(page, False)
        check(
            horizontal_result["count"] == 10
            and horizontal_result["inside"]
            and horizontal_result["maximum"] <= 0.12
            and horizontal_result["reportedMaximum"] <= 0.12,
            "十个密集横排标签同样受 12% 重叠阈值约束",
            checks,
        )

        # The settings popover is intentionally closed at this point. Reset the
        # saved preference without turning this cleanup step into a visibility
        # assertion for a hidden control.
        page.evaluate(
            """() => {
              const select=document.querySelector('#face-unnamed-marker');
              select.value='plus';
              select.dispatchEvent(new Event('change',{bubbles:true}));
            }"""
        )
        browser.close()

    check(not errors, "标记与防碰撞真实浏览器验证没有 JavaScript 错误", checks)
    payload = {
        "passed": True,
        "asset_id": asset_id,
        "real_face_counts": {
            "named": named_count,
            "unnamed": unnamed_count,
            "total": face_count,
        },
        "overlap_limit": 0.12,
        "face_overlap_limit": 0.25,
        "tolerated_face_overlap": tolerated_face_overlap,
        "forced_face_overlap": forced_face_overlap,
        "vertical": vertical_result,
        "horizontal": horizontal_result,
        "checks": checks,
        "screenshots": [
            "face-marker-pulse.png",
            "face-marker-passerby.png",
            "face-label-collision-vertical.png",
        ],
    }
    REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print("FACE_MARKER_COLLISION_OK", len(checks), "checks", flush=True)


if __name__ == "__main__":
    main()
