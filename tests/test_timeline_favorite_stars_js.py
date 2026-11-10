"""Timeline favorite stars (Phase 4, 2026-11): the real canvas module under Node.

The star is derived at draw time from the host's acknowledged asset record.
It never crosses the badges an item already shows (MD, Hidden/Muted, the
link badge), the label gives way to it, and it dims with the item.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(script):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is required for the JavaScript behavior probe")
    p = subprocess.run([node, "--input-type=module", "-e", script],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    assert p.returncode == 0, p.stderr


HARNESS = (
    "const mod=await import(" + json.dumps((ROOT / "web/js/editor_timeline_canvas.js").as_uri()) + ");"
    "const {THEME}=await import(" + json.dumps((ROOT / "web/js/editor_theme.js").as_uri()) + ");"
    + r"""
const {strict:assert}=await import('node:assert');
function context(){
 const ctx={ops:[],fillStyle:'',strokeStyle:'',globalAlpha:1,font:'',textAlign:'left',textBaseline:'alphabetic',lineWidth:1,stack:[]};
 ctx.save=()=>ctx.stack.push([ctx.fillStyle,ctx.globalAlpha,ctx.font,ctx.textAlign,ctx.textBaseline]);
 ctx.restore=()=>{[ctx.fillStyle,ctx.globalAlpha,ctx.font,ctx.textAlign,ctx.textBaseline]=ctx.stack.pop();};
 ctx.rect=(...a)=>ctx.ops.push({op:'rect',a});
 ctx.fillRect=(...a)=>ctx.ops.push({op:'fillRect',a,fill:ctx.fillStyle});
 ctx.fillText=(text,x,y)=>ctx.ops.push({op:'text',text,x,y,fill:ctx.fillStyle,alpha:ctx.globalAlpha,align:ctx.textAlign});
 ctx.measureText=(t)=>({width:String(t).length*6});
 for(const n of ['beginPath','clip','moveTo','lineTo','closePath','fill','stroke','strokeRect','drawImage','clearRect','arc'])ctx[n]=()=>{};
 return ctx;
}
function makeHost({clips=[],audio=[],guides=[],assets={},starsOn=true,linked=new Set(),layout}={}){
 const byPath=Object.fromEntries(Object.values(assets).map(a=>[a.path,a]));
 const target={
  activeScene:{clips,audio_tracks:audio,guide_frames:guides},
  _trackLayout:layout||[{type:'video',laneIndex:0},{type:'motion_driver',laneIndex:0},{type:'audio',laneIndex:0},{type:'guides',laneIndex:0}],
  _scaleTimeline:1,_labelW:100,totalFrames:1000,
  _trackY:i=>20+i*40,_trackH:()=>40,_frameToX:f=>100+f*2,
  _clipMatchesTrackEntry:(c,e)=>(c.lane||'video')===e.type,
  _isLaneHidden:()=>false,_timelineLaneAccent:()=>'#fff',_drawTimelineItemRail:()=>{},
  _getOrLoadThumbStrip:()=>null,_getOrLoadWaveform:()=>null,_displayVideoSource:()=>null,
  _mediaTimelineFrames:()=>100,_effectiveFps:24,_waveformAccentColor:()=>'#fff',
  _formatClipTimelineLabel:()=>'clip-label',_formatAudioTimelineLabel:()=>'audio-label',
  _canvasSansFont:(s,w)=>`${w} ${s}px sans`,_canvasMonoFont:(s,w)=>`${w} ${s}px mono`,
  _getAssetForSourcePath:p=>byPath[p]||null,_getGuideAsset:g=>assets[g.asset_id]||null,
  _isSelected:()=>false,_guidesLayoutIdx(){return this._trackLayout.findIndex(e=>e.type==='guides');},
  _linkGroupForItem:({id})=>linked.has(id)?{id:'g'}:null,_linkGroupLabel:()=>'A',_isLinkGroupLocked:()=>false,
  _timelineFavoriteStarsEnabled:()=>starsOn,
 };
 target._drawMutedOverlay=(ctx,...a)=>mod._drawMutedOverlay(target,ctx,...a);
 return new Proxy(target,{get:(t,p)=>(p in t?t[p]:(typeof p==='string'&&p.startsWith('_')?()=>undefined:undefined))});
}
const stars=(ctx)=>ctx.ops.filter(o=>o.op==='text'&&o.text==='★');
const fav={asset_id:'fav',path:'media/fav.mp4',favorite:true};
const plain={asset_id:'plain',path:'media/plain.mp4',favorite:false};
const gone={asset_id:'gone',path:'media/gone.mp4',favorite:true,missing:true};
const favImg={asset_id:'favimg',path:'media/fav.png',favorite:true};
const assets={fav,plain,gone,favimg:favImg};
function clip(id,start,end,extra={}){return {clip_id:id,source_path:fav.path,timeline_start_frame:start,timeline_end_frame:end,...extra};}
function drawClips(opts){const ctx=context();mod._drawClips(makeHost({assets,...opts}),ctx,2000);return ctx;}
function drawGuides(opts,width=2000){const ctx=context();mod._drawGuideMarkers(makeHost({assets,...opts}),ctx,width);return ctx;}
"""
)


def test_a_favorite_clip_audio_item_and_guide_draw_the_theme_star():
    run(HARNESS + r"""
let ctx=drawClips({clips:[clip('c1',0,100)]});
assert.equal(stars(ctx).length,1);
assert.equal(stars(ctx)[0].fill,THEME.favoriteStar);
assert.equal(stars(ctx)[0].alpha,1);
ctx=drawClips({clips:[clip('c1',0,100,{source_path:plain.path})]});
assert.equal(stars(ctx).length,0,'a non-favorite draws no star');
ctx=drawClips({clips:[clip('c1',0,100,{source_path:gone.path})]});
assert.equal(stars(ctx).length,0,'a missing asset draws no star');
ctx=drawClips({audio:[{track_id:'a1',source_path:fav.path,lane_index:0,timeline_start_frame:0,timeline_end_frame:100}]});
assert.equal(stars(ctx).length,1,'audio');
ctx=drawGuides({guides:[{frame_index:10,asset_id:'favimg'}]});
assert.equal(stars(ctx).length,1,'guide');
assert.equal(stars(ctx)[0].fill,THEME.favoriteStar);
""")


def test_the_setting_off_hides_every_star():
    run(HARNESS + r"""
let ctx=drawClips({starsOn:false,clips:[clip('c1',0,100)],
 audio:[{track_id:'a1',source_path:fav.path,lane_index:0,timeline_start_frame:0,timeline_end_frame:100}]});
assert.equal(stars(ctx).length,0);
ctx=drawGuides({starsOn:false,guides:[{frame_index:10,asset_id:'favimg'}]});
assert.equal(stars(ctx).length,0);
""")


def test_the_star_never_crosses_a_badge_and_skips_narrow_items():
    run(HARNESS + r"""
// Too narrow (20 px).
assert.equal(stars(drawClips({clips:[clip('c1',0,10)]})).length,0);
// Hidden badge spans up to 44 px from the left: a 60 px clip has no room.
assert.equal(stars(drawClips({clips:[clip('c1',0,30,{muted:true})]})).length,0);
// A wide hidden clip draws it, dimmed with the clip.
let ctx=drawClips({clips:[clip('c1',0,100,{muted:true})]});
assert.equal(stars(ctx).length,1);assert.equal(stars(ctx)[0].alpha,0.3);
// Motion Driver: "MD" spans x1+4..x1+22.
assert.equal(stars(drawClips({clips:[clip('m1',0,15,{lane:'motion_driver'})]})).length,0);
ctx=drawClips({clips:[clip('m1',0,100,{lane:'motion_driver'})]});
assert.equal(stars(ctx).length,1);
const mdText=ctx.ops.find(o=>o.op==='text'&&o.text==='MD');
assert(stars(ctx)[0].x-6>mdText.x+9,'clear of MD');
// Linked: the star sits left of the link badge.
ctx=drawClips({linked:new Set(['c1']),clips:[clip('c1',0,100)]});
const link=ctx.ops.find(o=>o.op==='text'&&o.text==='A');
assert.equal(stars(ctx).length,1);
assert(stars(ctx)[0].x<link.x,'left of the link badge');
// A short linked clip skips the star rather than spilling past its left edge.
ctx=drawClips({linked:new Set(['c1']),clips:[clip('c1',0,16)]});
assert.equal(stars(ctx).length,0);
""")


def test_the_label_gives_way_to_the_star():
    run(HARNESS + r"""
const ctx=drawClips({clips:[clip('c1',0,100)]});
const labelAt=ctx.ops.findIndex(o=>o.op==='text'&&o.text==='clip-label');
const labelClip=ctx.ops.slice(0,labelAt).filter(o=>o.op==='rect').pop();
const star=stars(ctx)[0];
const starLeft=star.x-11/2;
assert(labelClip.a[0]+labelClip.a[2]<=starLeft-2+0.001,`label ends at ${labelClip.a[0]+labelClip.a[2]}, star at ${starLeft}`);
// Without a star the label runs to the item's right padding.
const plainCtx=drawClips({clips:[clip('c1',0,100,{source_path:plain.path})]});
const plainAt=plainCtx.ops.findIndex(o=>o.op==='text'&&o.text==='clip-label');
const plainClip=plainCtx.ops.slice(0,plainAt).filter(o=>o.op==='rect').pop();
assert.equal(plainClip.a[0]+plainClip.a[2],100+200-4);
""")


def test_a_guide_star_follows_its_label_and_dims_when_hidden():
    run(HARNESS + r"""
let ctx=drawGuides({guides:[{frame_index:10,asset_id:'favimg'}]});
const marker=100+10*2;
assert(stars(ctx)[0].x>marker,'label right of the marker');
// Near the right edge the label flips left of the marker; the star with it.
ctx=drawGuides({guides:[{frame_index:440,asset_id:'favimg'}]},1000);
const flipped=100+440*2;
assert.equal(stars(ctx).length,1);
assert(stars(ctx)[0].x<flipped,'star follows the flipped label');
const label=ctx.ops.find(o=>o.op==='text'&&o.text==='f440');
assert(stars(ctx)[0].x>label.x,'star after the frame text');
ctx=drawGuides({guides:[{frame_index:10,asset_id:'favimg',muted:true}]});
assert.equal(stars(ctx)[0].alpha,0.42);
""")


def test_reference_items_and_the_gallery_share_one_star_color():
    canvas = (ROOT / "web/js/editor_timeline_canvas.js").read_text(encoding="utf-8")
    gallery = (ROOT / "web/js/shared_asset_gallery.js").read_text(encoding="utf-8")
    assert "const FAVORITE_STAR_COLOR = THEME.favoriteStar;" in gallery
    assert "THEME.favoriteStar" in canvas
    # Reference lane items stage Library members, not asset placements.
    start = canvas.index("export function _drawClips(")
    reference_part = canvas[canvas.index("TRACK_TYPE.REFERENCE", start):]
    assert "favoriteStarGeometry(" not in reference_part.split("export function", 1)[0]


def test_audio_dims_its_star_and_gives_way_unless_a_tall_item_keeps_the_label_below():
    run(HARNESS + r"""
const audio = (extra={}) => [{track_id:'a1',source_path:fav.path,lane_index:0,timeline_start_frame:0,timeline_end_frame:100,...extra}];
let ctx=drawClips({audio:audio({muted:true})});
assert.equal(stars(ctx)[0].alpha,0.3,'a muted audio item dims its star');
ctx=drawClips({audio:audio()});
const at=ctx.ops.findIndex(o=>o.op==='text'&&o.text==='audio-label');
const clipRect=ctx.ops.slice(0,at).filter(o=>o.op==='rect').pop();
assert(clipRect.a[0]+clipRect.a[2]<=stars(ctx)[0].x-11/2-2+0.001,'audio label gives way');
// A bottom-aligned label in a 36 px item sits below the star and keeps its width.
const host=makeHost({assets,clips:[clip('c1',0,100)]});
const bottom=new Proxy(host,{get:(t,p)=>p==='_clipLabelVerticalAlign'?()=>'bottom':t[p]});
ctx=context(); mod._drawClips(bottom,ctx,2000);
const bAt=ctx.ops.findIndex(o=>o.op==='text'&&o.text==='clip-label');
const bRect=ctx.ops.slice(0,bAt).filter(o=>o.op==='rect').pop();
assert.equal(bRect.a[0]+bRect.a[2],100+200-4);
assert.equal(stars(ctx).length,1);
""")


def test_the_setting_defaults_on_and_keeps_an_off_choice():
    script = (
        "globalThis.localStorage = { getItem() { return null; }, setItem() {} };\n"
        "const {strict:assert}=await import('node:assert');\n"
        "const settings = await import(" + json.dumps((ROOT / "web/js/editor_settings.js").as_uri()) + ");\n"
        + r"""
assert.equal(settings.normalizeEditorSettings().appearance.timelineFavoriteStars, true);
assert.equal(settings.normalizeEditorSettings({appearance:{}}).appearance.timelineFavoriteStars, true);
assert.equal(settings.normalizeEditorSettings({appearance:{timelineFavoriteStars:false}}).appearance.timelineFavoriteStars, false);
assert.equal(settings.normalizeEditorSettings({appearance:{timelineFavoriteStars:true}}).appearance.timelineFavoriteStars, true);
""")
    run(script)
    widget = (ROOT / "web/js/editor_widget.js").read_text(encoding="utf-8")
    # A settings write repaints the timeline when the toggle changes.
    signature = widget[widget.index("const prevClipLabelSignature = JSON.stringify({"):]
    signature = signature[:signature.index("const prevSceneOutline")]
    assert signature.count("timelineFavoriteStars !== false") == 2
