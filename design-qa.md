# 地点详情与返回地图 visual QA

- source visual truth: `C:\Users\A\AppData\Local\Temp\codex-clipboard-63ba98a9-578b-43aa-9139-52bfc83a98ff.png`
- implementation screenshot: `G:\Trea\照片浏览器\validation\reports\photo-place-radius-20260913\place-detail-compact.png`
- combined comparison: `G:\Trea\照片浏览器\validation\reports\photo-place-radius-20260913\place-detail-comparison.png`
- viewport: 1440 x 900 CSS px, deviceScaleFactor 1
- source pixels: 1532 x 444; implementation pixels: 1440 x 900; comparison pixels: 1440 x 1385
- density normalization: source screenshot was proportionally scaled to 1440 px width in the combined comparison; the title region remained uncropped
- state: 从地点地图进入一个具体 GPS 地点后的照片列表

## Findings

- No actionable P0/P1/P2 mismatch remains for the requested change.
- The old inline location form and its `地图里没有，将手填保存` message are gone. The title row now contains one restrained circular return control, the place name, and the existing pencil control.
- The implementation intentionally does not pixel-match the old form shown in the source: that form is the rejected before-state. The comparison target is the user's requested simplification and reuse of the existing GPS radius editor.

## Required fidelity surfaces

- Fonts and typography: inherited the active product type scale and weight; the place name remains the strongest element in the row.
- Spacing and layout rhythm: the new controls align to the title center without adding a second toolbar; the photo grid starts immediately below the sticky masthead.
- Colors and visual tokens: both controls reuse the existing green, border, panel, hover, and focus tokens.
- Image quality and asset fidelity: no new raster asset was needed; existing product icons and real thumbnails are reused.
- Copy and content: removed the rejected explanatory copy; return labels distinguish `返回地点照片` from `返回大图`.

## Interaction evidence

- Opened place detail, scrolled the photo list, and confirmed the title plus both controls remain fixed at the top.
- Opened the pencil action and confirmed the existing location-name + 1–500 meter radius editor appears.
- Returned from the radius editor to the same place detail instead of opening the large-photo viewer.
- Returned from place detail to the map and confirmed latitude, longitude, and fractional zoom were restored to the pre-entry values.
- Checked the 390 px editor state for horizontal overflow.
- Browser page errors: none in the full isolated flow.
- Codex in-app browser capture was unavailable (`nodeRepl.fetch request failed`), so the project validation's Chrome browser fallback supplied the rendered screenshots and interaction evidence.

## Full-view and focused comparison

- Full view: the rejected form/card block has been removed and the photo content moves up, producing the requested simpler hierarchy.
- Focused title region: return affordance, title, and pencil are visible, aligned, and clearly separated. A separate crop was unnecessary because the combined image keeps this region readable at original implementation density.

## Comparison history

- First rendered comparison found no P0/P1/P2 visual defect.
- The first interaction run exposed a non-visual API mismatch for fractional map zoom. The endpoint was corrected to accept the map's real zoom value, and the full flow then passed.

## Implementation checklist

- [x] Remove the old place refine form and rejected copy.
- [x] Add sticky title-level back and edit controls.
- [x] Reuse the existing GPS name-and-radius editor.
- [x] Preserve editor return mode and updated place title.
- [x] Restore exact map center and fractional zoom.
- [x] Verify desktop, scrolling, and narrow viewport behavior.

final result: passed
