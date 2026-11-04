"""Run with: pytest tests/ui_player.py (requires Playwright Chromium/WebKit)."""
import copy
import json
import os
import re
from pathlib import Path
import shutil
import threading

import pytest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from test_app import setup, ID, CLIP


@pytest.fixture(scope='module')
def ui_server(tmp_path_factory):
    app, root, data = setup.__wrapped__(tmp_path_factory.mktemp('player-ui'))
    data['source']['location'] = {'latitude':12,'longitude':34}
    data['video']['duration_seconds'] = 6
    data['source']['duration_seconds'] = 6
    data['clips'] = [dict(data['clips'][0], id=letter*24, index=i+1,
                         file=f'Clips/clip-{i+1:03}.mp4', duration_seconds=2,
                         original_start_seconds=i*2, original_end_seconds=(i+1)*2,
                         playback_start_seconds=i*2, playback_end_seconds=(i+1)*2)
                     for i, letter in enumerate('bcd')]
    data['kept_seconds'] = 6
    data['removed_seconds'] = 0
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    fixtures = Path(__file__).parent/'fixtures'
    shutil.copyfile(fixtures/'practice.mp4', root/'media'/ID/'Practice.mp4')
    for clip in data['clips']:
        shutil.copyfile(fixtures/'clip.mp4', root/'media'/ID/clip['file'])
    state = app.extensions['state']
    state.save_court('e'*32, {'name':'Park <West>','address':'Test lane','latitude':12,
                            'longitude':34,'altitude_m':None,'radius_m':150}, 0)
    state.update(f'like:{ID}:{CLIP}', True, 0)
    state.update(f'like:{ID}', True, 0)
    # Enable the low-frequency action without reaching a real NAS.
    app.config['TESTING'] = True
    from app import create_app
    app = create_app({'TESTING':True,'LIBRARY_ROOT':str(root),'DATA_ROOT':app.config['DATA_ROOT'],
                      'LIBRARY_SMB_URL':'smb://test/library','COACH_WORKER_TOKEN':'ui-fixture-only'})
    server = make_server('127.0.0.1', 0, app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    yield f'http://127.0.0.1:{server.server_port}'
    server.shutdown()
    worker.join()


@pytest.fixture(params=['chromium', 'webkit'])
def browser(request):
    with sync_playwright() as p:
        browser = getattr(p, request.param).launch()
        yield browser
        browser.close()


def test_review_rows_actions_and_courts(browser, ui_server, tmp_path):
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(ui_server+f'/review/{ID}')
    expect(page.locator('#review-location')).to_have_text('⌾ Park <West>')
    rows = page.locator('#clip-list > button')
    expect(rows).to_have_count(3)
    assert rows.locator('button,a').count() == 0
    expect(page.get_by_role('link',name='Download full practice',exact=True)).to_be_visible()
    # The whole row supports keyboard activation and loads an individual file.
    rows.nth(1).focus()
    page.keyboard.press('Enter')
    expect(page.locator('#player')).to_have_attribute('src', re.compile('Clips/clip-002.mp4'))
    expect(page.get_by_role('link', name='Download clip 02',exact=True)).to_be_visible()
    expect(rows.nth(1)).to_have_attribute('aria-current','true')
    page.locator('#player').evaluate('(v)=>v.pause()')
    expect(page.get_by_role('button',name='Like clip 2',exact=True)).to_be_visible()
    expect(page.locator('#player-nas')).not_to_be_visible()
    if folder := os.environ.get('TENNIS_UI_SCREENSHOT_DIR'):
        Path(folder).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(folder)/f'desktop-{browser.browser_type.name}.png'),full_page=True)
    page.get_by_label('More video actions',exact=True).click()
    page.get_by_role('button',name='NAS file address',exact=True).click()
    expect(page.locator('#nas-address')).to_have_value(re.compile('clip-002.mp4$'))
    page.get_by_role('button',name='Close NAS address',exact=True).click()
    with page.expect_download() as event:
        page.get_by_role('link',name='Download clip 02',exact=True).click()
    download = event.value
    assert download.suggested_filename == 'test-clip-002.mp4'
    download.save_as(tmp_path/'clip.mp4')
    assert (tmp_path/'clip.mp4').read_bytes() == (Path(__file__).parent/'fixtures/clip.mp4').read_bytes()
    page.get_by_role('button',name='Full practice ↗',exact=True).click()
    expect(page.get_by_role('link',name='Download full practice',exact=True)).to_be_visible()
    page.goto(ui_server+'/#liked')
    expect(page.locator('.card-location')).to_have_text('⌾ Park <West>')
    page.goto(ui_server+'/#liked-sessions')
    expect(page.locator('.card-location')).to_have_text('⌾ Park <West>')
    assert not errors
    page.close()


def test_mobile_overlay_layout_and_autoplay(browser, ui_server):
    page = browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    for width in [320,390,844]:
        page.set_viewport_size({'width':width,'height':844 if width<844 else 390})
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        stage = page.locator('#video-stage').bounding_box()
        video = page.locator('#player').bounding_box()
        assert abs(stage['height']-video['height']) < 1
        for selector in ['#previous','#next','#video-play','#video-seek','#video-fullscreen']:
            box = page.locator(selector).bounding_box()
            assert box['y'] >= stage['y'] and box['y']+box['height'] <= stage['y']+stage['height']+1
        assert page.locator('#video-stage').locator('#video-mute,#video-rate,select[aria-label="Playback speed"]').count() == 0
        expect(page.locator('#player-download svg')).to_be_visible()
        assert page.locator('#player-download').inner_text().strip() == ''
        expect(page.get_by_role('button',name='Study clip',exact=True)).to_be_visible()
        assert page.locator('#study-clip svg').count() == 1
        assert page.locator('.player-options').evaluate("e => [...e.children].map(c=>c.id)")[:3] == ['player-like','study-clip','player-download']
        expect(page.locator('.player-chapters summary')).to_have_text('Clips')
        if width == 390 and (folder := os.environ.get('TENNIS_UI_SCREENSHOT_DIR')):
            Path(folder).mkdir(parents=True, exist_ok=True)
            page.screenshot(path=str(Path(folder)/f'mobile-{browser.browser_type.name}.png'),full_page=True)
            page.locator('.player-shell').screenshot(path=str(Path(folder)/f'player-mobile-{browser.browser_type.name}.png'))
    page.get_by_role('button',name='Next clip',exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    page.get_by_label('Autoplay',exact=True).check()
    page.locator('#player').evaluate('''async v => {
      if(v.readyState<1)await new Promise(resolve=>v.addEventListener('loadedmetadata',resolve,{once:true}));
      v.currentTime=v.duration-0.15;await v.play();
    }''')
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-003.mp4'),timeout=10000)
    page.get_by_label('Autoplay',exact=True).uncheck()
    page.get_by_role('button',name='Previous clip',exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    page.locator('#player').evaluate('''async v => {
      if(v.readyState<1)await new Promise(resolve=>v.addEventListener('loadedmetadata',resolve,{once:true}));
      v.currentTime=v.duration-0.15;await v.play();
    }''')
    expect(page.get_by_role('button',name='Play video',exact=True)).to_be_visible()
    assert page.locator('#player').evaluate('(v)=>v.ended')
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    page.close()


def test_iphone_calls_real_native_fullscreen(browser, ui_server):
    page = browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    # Browsers in CI cannot show iPhone system UI. Verify that capability
    # detection invokes the native API, with no page-sized fullscreen substitute.
    page.evaluate('''() => {
      const stage=document.querySelector('#video-stage'),v=document.querySelector('#player');
      stage.requestFullscreen=undefined;stage.webkitRequestFullscreen=undefined;
      window.nativeFullscreenEntries=0;
      Object.defineProperty(v,'webkitDisplayingFullscreen',{value:false,writable:true,configurable:true});
      v.webkitEnterFullscreen=()=>{window.nativeFullscreenEntries++;v.webkitDisplayingFullscreen=true;v.dispatchEvent(new Event('webkitbeginfullscreen'));};
      v.webkitExitFullscreen=()=>{v.webkitDisplayingFullscreen=false;v.dispatchEvent(new Event('webkitendfullscreen'));};
    }''')
    page.get_by_role('button',name='Enter fullscreen',exact=True).click()
    assert page.evaluate('window.nativeFullscreenEntries') == 1
    assert page.locator('.viewport-fullscreen').count() == 0
    assert page.locator('#video-stage').evaluate('(e)=>getComputedStyle(e).position') == 'relative'
    page.get_by_role('button',name='Exit fullscreen',exact=True).click()
    expect(page.get_by_role('button',name='Enter fullscreen',exact=True)).to_be_visible()
    expect(page.locator('#native-fullscreen-hint')).to_be_visible()
    expect(page.locator('#native-fullscreen-hint')).to_contain_text('Exit fullscreen')
    assert page.locator('#video-expand').count()==0
    page.close()


def test_overlay_hides_and_reappears_without_layout_shift(browser, ui_server):
    page = browser.new_page()
    page.goto(ui_server+f'/review/{ID}')
    expect(page.locator('#video-seek')).to_be_enabled()
    before = page.locator('#video-stage').evaluate('(e)=>[e.offsetWidth,e.offsetHeight,e.offsetTop]')
    page.get_by_role('button',name='Play video',exact=True).click()
    expect(page.locator('#video-stage')).to_have_class(re.compile('controls-hidden'),timeout=4000)
    expect(page.locator('.video-overlay')).to_have_css('opacity','0')
    assert page.locator('#video-stage').evaluate('(e)=>[e.offsetWidth,e.offsetHeight,e.offsetTop]') == before
    # Moving over the picture reveals the overlay; pause keeps it visible.
    page.locator('#player').hover()
    expect(page.locator('#video-stage')).not_to_have_class(re.compile('controls-hidden'))
    page.get_by_role('button',name='Pause video',exact=True).click()
    expect(page.locator('#video-stage')).not_to_have_class(re.compile('controls-hidden'))
    page.close()


def test_element_fullscreen_keeps_overlay_controls(browser, ui_server):
    page = browser.new_page()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button',name='Enter fullscreen',exact=True).click()
    expect(page.get_by_role('button',name='Exit fullscreen',exact=True)).to_be_visible()
    if browser.browser_type.name == 'chromium':
        assert page.evaluate('document.fullscreenElement?.id') == 'video-stage'
    expect(page.locator('#video-context')).to_be_visible()
    expect(page.locator('#video-stage #previous')).to_be_visible()
    expect(page.locator('#video-stage #next')).to_be_visible()
    page.get_by_role('button',name='Next clip',exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    page.get_by_role('button',name='Exit fullscreen',exact=True).click()
    expect(page.get_by_role('button',name='Enter fullscreen',exact=True)).to_be_visible()
    page.close()


def test_mobile_tap_reveals_overlay_without_pausing(browser, ui_server):
    page = browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    page.goto(ui_server+f'/review/{ID}')
    expect(page.locator('#video-seek')).to_be_enabled()
    page.get_by_role('button',name='Play video',exact=True).click()
    page.locator('#player').tap(position={'x':20,'y':60})
    expect(page.locator('#video-stage')).to_have_class(re.compile('controls-hidden'))
    assert not page.locator('#player').evaluate('(v)=>v.paused')
    page.locator('#player').tap(position={'x':20,'y':60})
    expect(page.locator('#video-stage')).not_to_have_class(re.compile('controls-hidden'))
    assert not page.locator('#player').evaluate('(v)=>v.paused')
    page.close()


def test_progress_bar_has_a_touch_hit_area_and_seeks(browser, ui_server):
    page = browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    page.goto(ui_server+f'/review/{ID}')
    seek=page.locator('#video-seek')
    expect(seek).to_be_enabled()
    box=seek.bounding_box()
    assert box['height'] >= 20
    seek.tap(position={'x':box['width']*0.7,'y':box['height']/2})
    assert page.locator('#player').evaluate('(v)=>v.currentTime') > 3
    page.close()


def test_clip_study_action_precision_and_return(browser, ui_server):
    page = browser.new_page()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.wait_for_function("() => document.querySelector('#player')?.readyState >= 2")
    page.locator('#video-seek').evaluate("el => { el.value = 0.4; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    page.get_by_role('button', name='Study clip', exact=True).click()
    dialog = page.get_by_role('dialog', name='Clip study · 01', exact=True)
    expect(dialog).to_be_visible()
    expect(page.locator('#study-play')).to_be_enabled()
    assert page.locator('#player').evaluate('v => v.paused')
    dialog.get_by_role('button', name='Step forward 0.1 seconds').click()
    assert page.locator('#study-video').evaluate('v => v.currentTime') == pytest.approx(.5, abs=.08)
    dialog.get_by_role('combobox', name='Playback speed').select_option('0.25')
    assert page.locator('#study-video').evaluate('v => v.playbackRate') == .25
    zoom = dialog.get_by_role('slider', name='Video zoom')
    zoom.press('Home')
    for _ in range(4):
        zoom.press('ArrowRight')
    expect(page.locator('#study-zoom-value')).to_have_text('2×')
    dialog.get_by_role('checkbox', name='Loop clip', exact=True).check()
    assert page.locator('#study-video').evaluate('v => v.loop')
    dialog.get_by_role('button', name='← Back to review', exact=True).click()
    expect(dialog).not_to_be_visible()
    # The native dialog close event runs after its open attribute is removed.
    page.wait_for_function("() => Math.abs(document.querySelector('#player').currentTime - .5) <= .08")
    assert page.locator('#player').evaluate('v => v.currentTime') == pytest.approx(.5, abs=.08)
    assert page.locator('#player').evaluate('v => v.paused')
    expect(page.get_by_role('button',name='Study clip',exact=True)).to_be_focused()
    page.get_by_role('button', name='Full practice ↗', exact=True).click()
    page.get_by_label('More video actions', exact=True).click()
    expect(page.get_by_role('button', name='Study clip', exact=True)).not_to_be_visible()
    page.close()


def test_clip_study_frame_package_is_local_and_resets(browser, ui_server, tmp_path):
    import zipfile
    page = browser.new_page(accept_downloads=True)
    external = []
    page.on('request', lambda req: external.append(req.url) if req.url.startswith(('http:', 'https:')) and not req.url.startswith(ui_server) else None)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button', name='Study clip', exact=True).click()
    dialog = page.get_by_role('dialog', name='Clip study · 01', exact=True)
    expect(dialog.get_by_role('button', name='Sample 6 moments', exact=True)).to_be_enabled()
    dialog.get_by_role('button', name='Sample 6 moments', exact=True).click()
    expect(page.locator('.study-frame')).to_have_count(6, timeout=15000)
    with page.expect_download() as event:
        dialog.get_by_role('button', name='Download 6-frame package', exact=True).click()
    target = tmp_path/'frames.zip'
    event.value.save_as(target)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == 8
        context = json.loads(archive.read('context.json'))
        times = [f['clip_seconds'] for f in context['frames']]
        assert len(times) == 6 and times == sorted(times)
        assert all(0 < t < 2 for t in times)
    assert external == []
    dialog.get_by_role('button', name='← Back to review', exact=True).click()
    page.get_by_role('button', name='Study clip', exact=True).click()
    expect(page.locator('.study-frame')).to_have_count(0)
    expect(page.locator('#study-export')).to_be_disabled()
    expect(page.locator('#study-export')).to_have_text('Download frame package')
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    page.close()


def test_coaching_selection_queues_export_without_ai_upload(browser, ui_server):
    page=browser.new_page()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button',name='Study clip',exact=True).click()
    expect(page.locator('#study-play')).to_be_enabled()
    expect(page.locator('#coach-prepare')).to_be_enabled()
    page.get_by_role('textbox',name='What would you like help with?',exact=True).fill('Check my balance')
    zoom=page.get_by_role('slider',name='Video zoom',exact=True)
    zoom.press('Home')
    for _ in range(4):zoom.press('ArrowRight')
    with page.expect_response(lambda r:r.url.endswith('/api/coaching/exports') and r.request.method=='POST') as event:
        page.get_by_role('button',name='Prepare coaching clip',exact=True).click()
    response=event.value
    assert response.status==201
    job=response.json()
    assert job['role']=='render' and job['state']=='queued'
    spec=job['body']
    assert spec['question']=='Check my balance' and spec['speed']==.5
    assert 0 < min(spec['crop'][2:]) < 1
    expect(page.locator('#coach-ask')).to_be_disabled()
    expect(page.locator('#coach-status')).to_contain_text('queued',ignore_case=True)
    page.get_by_role('button',name='← Back to review',exact=True).click()
    page.close()


def test_native_media_navigation_uses_current_clip_queue(browser, ui_server):
    page = browser.new_page(viewport={'width':390,'height':844}, has_touch=True)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script('''
      window.mediaHandlers={};window.mediaState={};
      Object.defineProperty(navigator,'mediaSession',{configurable:true,value:{
        setActionHandler(name,handler){window.mediaHandlers[name]=handler;},
        setPositionState(state){window.mediaState.position=state;}
      }});
      window.MediaMetadata=class {constructor(data){Object.assign(this,data);}};
    ''')
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    page.evaluate('''() => {
      const stage=document.querySelector('#video-stage'),v=document.querySelector('#player');
      stage.requestFullscreen=undefined;stage.webkitRequestFullscreen=undefined;
      window.originalPlayer=v;window.nativeEntries=0;
      Object.defineProperty(v,'webkitDisplayingFullscreen',{value:false,writable:true,configurable:true});
      v.webkitEnterFullscreen=()=>{window.nativeEntries++;v.webkitDisplayingFullscreen=true;v.dispatchEvent(new Event('webkitbeginfullscreen'));};
    }''')
    page.get_by_role('button',name='Enter fullscreen',exact=True).click()
    assert page.evaluate('mediaHandlers.previoustrack === null')
    assert page.evaluate('typeof mediaHandlers.nexttrack') == 'function'
    page.evaluate('mediaHandlers.nexttrack()')
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    page.wait_for_function('() => mediaState.position?.duration === 2')
    assert page.evaluate('navigator.mediaSession.metadata.title') == 'Clip 02'
    assert page.evaluate('navigator.mediaSession.metadata.album') == 'Practice <script>'
    assert page.evaluate('document.querySelector("#player") === originalPlayer && originalPlayer.webkitDisplayingFullscreen')
    assert page.evaluate('nativeEntries') == 1
    page.evaluate('mediaHandlers.nexttrack()')
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-003.mp4'))
    assert page.evaluate('mediaHandlers.nexttrack === null')
    page.evaluate('mediaHandlers.previoustrack()')
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    # Native actions follow the same liked-only queue, including an active clip
    # outside the filter. A saved stale callback must not bypass a disabled button.
    page.evaluate('window.staleNext=mediaHandlers.nexttrack')
    page.get_by_role('button',name='♡ Liked only',exact=True).click()
    page.evaluate('mediaHandlers.nexttrack()')
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-001.mp4'))
    assert page.evaluate('mediaHandlers.nexttrack === null && mediaHandlers.previoustrack === null')
    page.evaluate('staleNext()')
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-001.mp4'))
    # Clip study owns its own playback; the parent must not accept remote skips.
    page.get_by_role('button',name='Study clip',exact=True).click()
    page.wait_for_function('() => Object.values(mediaHandlers).every(h => h === null)')
    page.get_by_role('button',name='← Back to review',exact=True).click()
    page.wait_for_function('() => typeof mediaHandlers.play === "function"')
    page.evaluate('window.dispatchEvent(new PageTransitionEvent("pagehide"))')
    assert page.evaluate('Object.values(mediaHandlers).every(h => h === null)')
    page.evaluate('window.dispatchEvent(new PageTransitionEvent("pageshow",{persisted:true}))')
    assert page.evaluate('typeof mediaHandlers.play') == 'function'
    assert not errors
    page.close()


@pytest.mark.parametrize('available', [False, True])
def test_media_session_unavailable_or_rejects_actions(browser, ui_server, available):
    page = browser.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script('''Object.defineProperty(navigator,'mediaSession',{configurable:true,value:'''+
                         ('''{setActionHandler(){throw new DOMException('Unsupported action','NotSupportedError');},
                              setPositionState(){throw new Error('Unavailable');}}''' if available else 'undefined')+'});')
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button',name='Next clip',exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    page.get_by_role('button',name='Previous clip',exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-001.mp4'))
    assert not errors
    page.close()


def test_single_fullscreen_action_across_mobile_and_desktop(browser, ui_server):
    page=browser.new_page()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    for width,height in [(390,844),(844,390),(320,568),(1280,800)]:
        page.set_viewport_size({'width':width,'height':height})
        expect(page.locator('.video-view-actions button')).to_have_count(1)
        expect(page.get_by_role('button',name='Enter fullscreen',exact=True)).to_be_visible()
        assert page.locator('#video-expand,#video-close-expanded,.player-expanded').count()==0
        assert page.locator('#video-stage').get_attribute('role')=='region'
    page.close()


@pytest.mark.parametrize('width',[1280,390])
def test_structured_coaching_evidence_review_and_draft(browser, ui_server, width):
    """Browser rendering contract; real validation/retention is covered by API tests."""
    import base64
    page=browser.new_page(viewport={'width':width,'height':844})
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    source=page.request.get(ui_server+'/api/sessions/'+ID).json()
    publication=source['published_at']
    frame=dict(frame_id='f_test',order=1,sha256='e'*64,original_seconds=.5,clip_seconds=.5,excerpt_seconds=1)
    content=dict(title='Balanced reset',topic='Ready position',observation='A narrow stance in this still.',cue='Reset with soft knees.',drill='Recover and hold.',uncertainty='Stills cannot confirm split-step timing.',evidence_frame_ids=['f_test'],applies_to=[dict(start_seconds=.25,end_seconds=1.5)],validation_warnings=[],timing_status='timed')
    annotation=dict(id='a_test',job_id='job-test',current=content,proposal=copy.deepcopy(content),status='proposed',historical=False,playback_eligible=False,version=1,provenance=dict(clip_id=CLIP,publication=publication),related_clip_ids=[CLIP])
    job=dict(id='job-test',version=1,role='ai',state='completed',body=dict(source_id=ID,clip_id=CLIP,publication=publication,export_id='render-test',original_start=0,original_end=1.8,start=0,end=1.8,prompt='Check balance',evidence_frames=[frame]),result=dict(answer='Original test response',structured=dict(summary='One observable improvement.',validation_warnings=[],parse_status='valid')))
    rendered=dict(id='render-test',version=1,role='render',state='completed',body=dict(source_id=ID,clip_id=CLIP,publication=publication,start=0,end=1.8,original_start=0,original_end=1.8,speed=.5,crop=[0,0,1,1]),result=dict(bytes=1000,output_seconds=3.6,frames=[frame]))
    state=dict(jobs=[job,rendered],annotations=[annotation],workers=[],enabled=True)
    page.route('**/api/coaching/exports/render-test/excerpt.mp4',lambda route:route.fulfill(body=(Path(__file__).parent/'fixtures/clip.mp4').read_bytes(),content_type='video/mp4'))
    page.route('**/api/coaching/sessions/*',lambda route:route.fulfill(json=state))
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=')
    page.route('**/api/coaching/evidence/*/*.jpg',lambda route:route.fulfill(body=png,content_type='image/png'))
    def update(route):
        body=route.request.post_data_json
        assert body['version']==annotation['version']
        annotation.update(status=body['status'],version=annotation['version']+1,playback_eligible=body['status']=='accepted')
        route.fulfill(json=annotation)
    page.route('**/api/coaching/annotations/a_test',update)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button',name='Study clip',exact=True).click()
    if width<761:page.get_by_role('button',name='Coach',exact=True).click()
    question=page.get_by_role('textbox',name='What would you like help with?',exact=True)
    if width<761:page.locator('#coach-question-options summary').click()
    question.fill('Keep my custom question')
    page.locator('#coach-step-4').click()
    expect(page.get_by_role('heading',name='Balanced reset',exact=True)).to_be_visible()
    card=page.locator('.coach-suggestion')
    expect(card.get_by_text('Needs your review',exact=True)).to_be_visible()
    image=card.locator('img');expect(image).to_be_visible()
    expect(image).to_have_attribute('src','/api/coaching/evidence/job-test/f_test.jpg')
    card.get_by_role('button',name=re.compile('Frame 1')).click()
    assert page.locator('#study-video').evaluate('v=>v.currentTime')==pytest.approx(.5,abs=.08)
    card.get_by_role('button',name='Accept suggestion',exact=True).click()
    expect(page.locator('.coach-review-state')).to_have_text('Accepted')
    assert page.locator('#clip-study').evaluate('d=>d.scrollWidth<=d.clientWidth')
    page.get_by_role('button',name='← Back to review',exact=True).click()
    page.get_by_role('button',name='Study clip',exact=True).click()
    expect(page.locator('#coach-step-4')).to_have_attribute('aria-current','step')
    if width<761:page.get_by_role('button',name='Coach',exact=True).click()
    page.locator('#coach-step-1').click()
    expect(question).to_have_value('Keep my custom question')
    page.locator('#coach-step-4').click()
    page.get_by_role('button',name='Ask a follow-up',exact=True).click()
    follow=page.get_by_role('textbox',name='Your question',exact=True)
    expect(follow).to_be_visible()
    follow.fill('Please reassess all snapshots, including footwork.')
    sent=[]
    def ask(route):
        sent.append(route.request.post_data_json)
        new=copy.deepcopy(job);new.update(id='follow-up-job',version=1,state='queued');new['body'].update(prompt=sent[-1]['prompt'],follow_up_of='job-test');state['jobs'].insert(0,new)
        route.fulfill(status=201,json=new)
    page.route('**/api/coaching/requests',ask)
    page.get_by_role('button',name='Send follow-up · frames to OpenAI',exact=True).click()
    expect(page.locator('#coach-history')).to_contain_text('Queued')
    assert sent[0]['follow_up_of']=='job-test' and sent[0]['export_id']=='render-test'
    assert sent[0]['prompt']=='Please reassess all snapshots, including footwork.'
    assert not errors
    page.close()


def test_normal_playback_ignores_coaching_end_until_play_selection(browser,ui_server):
    page=browser.new_page()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button',name='Study clip',exact=True).click()
    expect(page.locator('#study-play')).to_be_enabled()
    page.locator('#coach-out').evaluate("e=>{e.value=.3;e.dispatchEvent(new Event('input',{bubbles:true}));}")
    page.get_by_role('button',name='Play',exact=True).click()
    page.wait_for_function("()=>document.querySelector('#study-video').currentTime>.6&&!document.querySelector('#study-video').paused")
    page.get_by_role('button',name='Pause',exact=True).click()
    page.get_by_role('button',name='Play selection',exact=True).click()
    page.wait_for_function("()=>document.querySelector('#study-video').paused&&document.querySelector('#study-video').currentTime>=.3")
    assert page.locator('#study-video').evaluate('v=>v.currentTime')<.7
    page.get_by_role('button',name='Play',exact=True).click()
    page.wait_for_function("()=>document.querySelector('#study-video').currentTime>.8&&!document.querySelector('#study-video').paused")
    page.close()
