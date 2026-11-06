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
    rows = page.locator('#clip-list .clip-play')
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
    expect(page.locator('#player-like').get_by_role('button',name='Like clip 2',exact=True)).to_be_visible()
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
        for selector in ['#previous','#next','#video-play','#video-seek','#video-rate','#video-fullscreen']:
            box = page.locator(selector).bounding_box()
            assert box['y'] >= stage['y'] and box['y']+box['height'] <= stage['y']+stage['height']+1
        expect(page.locator('#video-rate')).to_be_visible()
        rate=page.locator('#video-rate').bounding_box();fullscreen=page.locator('#video-fullscreen').bounding_box()
        assert abs(rate['width']-fullscreen['width'])<1 and abs(rate['height']-fullscreen['height'])<1
        assert page.locator('#video-rate').evaluate("e=>getComputedStyle(e).backgroundColor")=="rgba(0, 0, 0, 0)"
        expect(page.locator('#player-download svg')).to_be_visible()
        assert page.locator('#player-download').inner_text().strip() == ''
        expect(page.get_by_role('button',name='Study clip',exact=True)).to_be_visible()
        assert page.locator('#study-clip svg').count() == 1
        assert page.locator('.player-options').evaluate("e => [...e.children].map(c=>c.id||c.className)")[:3] == ['player-reactions','study-clip','player-download']
        expect(page.locator('.player-chapters')).to_have_count(0)
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
    expect(page.locator('#native-fullscreen-hint')).to_have_count(0)
    assert page.locator('#video-expand').count()==0
    page.close()


def test_watch_speed_and_saved_autoplay(browser, ui_server):
    page = browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    speed = page.get_by_role('combobox',name='Playback speed',exact=True)
    expect(page.locator('#video-seek')).to_be_enabled()
    speed.select_option('0.5')
    assert page.locator('#player').evaluate('(v)=>v.playbackRate') == 0.5
    page.get_by_label('Autoplay',exact=True).check()
    page.reload()
    expect(page.get_by_label('Autoplay',exact=True)).to_be_checked()
    expect(page.locator('#video-seek')).to_be_enabled()
    speed.select_option('1.5')
    page.get_by_role('button',name='Next clip',exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
    expect(page.locator('#video-seek')).to_be_enabled()
    expect(speed).to_have_value('1.5')
    assert page.locator('#player').evaluate('(v)=>v.playbackRate') == 1.5
    page.get_by_label('Autoplay',exact=True).uncheck()
    page.reload()
    expect(page.get_by_label('Autoplay',exact=True)).not_to_be_checked()
    page.close()


def test_watch_autoplay_storage_unavailable(browser, ui_server):
    page = browser.new_page()
    page.add_init_script("""Object.defineProperty(window,'localStorage',{get(){throw new Error('blocked');}});""")
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    expect(page.get_by_label('Autoplay',exact=True)).not_to_be_checked()
    page.get_by_label('Autoplay',exact=True).check()
    page.get_by_role('button',name='Next clip',exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-002.mp4'))
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
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('liked')
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


def test_player_profiles_assignments_and_filters(browser, ui_server):
    # A new fixture player avoids coupling to the other browser's shared registry.
    page = browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    name='Player '+browser.browser_type.name
    page.goto(ui_server+'/')
    page.get_by_role('button',name='Saved players',exact=True).click()
    page.get_by_role('button',name='Add player',exact=True).click()
    page.get_by_label('Name',exact=True).fill(name)
    page.get_by_label('Height (cm, optional)',exact=True).fill('172')
    page.get_by_label('Level',exact=True).select_option('intermediate')
    page.get_by_role('button',name='Save player',exact=True).click()
    expect(page.locator('#saved-player-list')).to_contain_text(name)
    page.get_by_role('button',name='Close saved players',exact=True).click()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    page.locator('#clip-main-player').click()
    page.get_by_role('radio',name=name,exact=True).check()
    page.get_by_role('button',name='Save players',exact=True).click()
    expect(page.locator('#clip-main-player')).to_have_attribute('aria-label',re.compile(re.escape(name)))
    page.get_by_role('combobox',name='Filter clips by player',exact=True).select_option(label=name)
    expect(page.locator('#clip-list .clip-play')).to_have_count(1)
    expect(page.locator('#clip-list .player-chip')).to_have_attribute('aria-label',name)
    page.reload()
    expect(page.locator('#clip-main-player')).to_have_attribute('aria-label',re.compile(re.escape(name)))
    assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
    page.get_by_role('button',name='Study clip',exact=True).click()
    page.locator('#study-mode-coach').click()
    page.locator('#coach-step-3').click()
    expect(page.locator('#coach-player-context')).to_contain_text(name)
    expect(page.locator('#coach-send-question')).not_to_have_value(re.compile('I am a beginner'))
    page.get_by_role('button',name='← Back to review',exact=True).click()
    page.goto(ui_server+'/')
    page.get_by_role('combobox',name='Filter by player',exact=True).select_option(label=name)
    expect(page.locator('.practice-card')).to_have_count(1)
    expect(page.locator('.practice-card .player-chip').first).to_be_visible()
    page.get_by_role('button',name='Saved players',exact=True).click()
    row=page.locator('.saved-player-row').filter(has_text=name)
    row.get_by_role('button',name='Edit',exact=True).click()
    page.get_by_label('Name',exact=True).fill(name+' updated')
    page.get_by_role('button',name='Save player',exact=True).click()
    expect(page.locator('#saved-player-list')).to_contain_text(name+' updated')
    # The fixture library is shared across browser engines; restore this test's
    # assignment so unrelated playback tests do not inherit a player label.
    page.get_by_role('button',name='Close saved players',exact=True).click()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.locator('#clip-main-player').click()
    page.get_by_role('radio',name='Unassigned',exact=True).check()
    page.get_by_role('button',name='Save players',exact=True).click()
    expect(page.locator('#clip-main-player')).to_have_attribute('aria-label',re.compile('unassigned'))
    page.close()


@pytest.mark.parametrize('width',[1280,390])
def test_compact_clip_selection_thumbnails_and_player_editing(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':844},has_touch=width<761)
    headers={'X-Tennis-Request':'1'}
    people=[]
    for suffix in ['A','B']:
        r=page.request.post(ui_server+'/api/players',data=dict(name=f'Batch {browser.browser_type.name} {width} {suffix}',height_cm=170,level='beginner'),headers=headers)
        assert r.status==201;people.append(r.json())
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    page.get_by_role('button',name='Full practice ↗',exact=True).click()
    page.get_by_role('button',name='Edit video',exact=True).click()
    expect(page.get_by_role('button',name='Edit players',exact=True)).to_be_visible()
    page.get_by_role('button',name='Edit players',exact=True).click()
    for person in people:page.get_by_role('checkbox',name=person['name'],exact=True).check()
    page.get_by_role('button',name='Save players',exact=True).click()
    expect(page.locator('#edit-dialog')).to_be_visible()
    page.get_by_role('button',name='Save details',exact=True).click()
    for person in people:expect(page.locator('#recording-player-badges').get_by_role('img',name=person['name'],exact=True)).to_be_visible()
    assert all(p['name'] not in page.locator('#recording-player-badges').inner_text() for p in people)
    assert page.locator('.review-meta').evaluate("e=>[...e.children].findIndex(n=>n.id==='recording-player-badges') === [...e.children].findIndex(n=>n.id==='review-location')+1")
    expect(page.locator('.recording-players')).to_have_count(0)
    page.locator('#clip-list [data-play]').first.click()
    page.locator('#clip-list').scroll_into_view_if_needed()
    page.wait_for_function("()=>[...document.querySelectorAll('#clip-list img')].length===3 && [...document.querySelectorAll('#clip-list img')].every(i=>i.complete&&i.naturalWidth>0)")
    posters=page.locator('#clip-list img').evaluate_all('els=>els.map(e=>e.src)')
    assert len(set(posters))==3 and all('/clips/' in p and '/poster' in p for p in posters)
    for row in page.locator('.clip-row').all():assert row.bounding_box()['height']<=76
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.5;}')
    source=page.locator('#player').get_attribute('src')
    page.get_by_label('Clip list actions',exact=True).click()
    page.get_by_role('button',name='Select clips',exact=True).click()
    page.get_by_role('checkbox',name='Select clip 1',exact=True).check()
    page.get_by_role('checkbox',name='Select clip 2',exact=True).check()
    expect(page.locator('#selected-clip-count')).to_have_text('2 selected')
    assert page.locator('#select-all-clips').evaluate('e=>e.indeterminate')
    assert page.locator('#player').get_attribute('src')==source
    assert page.locator('#player').evaluate('v=>v.paused&&Math.abs(v.currentTime-.5)<.05')
    page.get_by_role('button',name='Assign main player',exact=True).click()
    page.get_by_role('radio',name=people[1]['name'],exact=True).check()
    page.get_by_role('button',name='Save players',exact=True).click()
    expect(page.locator('#selected-clip-count')).to_have_text('0 selected')
    data=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    assert [c['main_player']['id'] if c['main_player'] else None for c in data['clips']]==[people[1]['id'],people[1]['id'],None]
    expect(page.locator('#clip-list .player-chip')).to_have_count(2)
    assert all(p['name'] not in page.locator('#clip-list').inner_text() for p in people)
    # Selection is bounded to the currently shown list when filters change.
    page.get_by_role('checkbox',name='Select all shown clips',exact=True).check()
    page.get_by_role('combobox',name='Filter clips by player',exact=True).select_option(people[1]['id'])
    expect(page.locator('#selected-clip-count')).to_have_text('2 selected')
    expect(page.locator('#clip-list .clip-row')).to_have_count(2)
    page.get_by_label('Clip list actions',exact=True).click()
    page.get_by_role('button',name='Select clips',exact=True).click()
    page.locator('#clip-list .clip-play').nth(1).click()
    expect(page.locator('#player')).to_have_attribute('poster',re.compile(data['clips'][1]['id']+'/poster'))
    if folder:=os.environ.get('TENNIS_UI_SCREENSHOT_DIR'):
        page.locator('#clip-list').scroll_into_view_if_needed();page.screenshot(path=str(Path(folder)/f'compact-clips-{width}-{browser.browser_type.name}.png'),full_page=True)
    page.goto(ui_server+'/')
    card=page.locator('.practice-card').filter(has=page.locator(f'[data-player-id="{people[1]["id"]}"]')).first
    expect(card).to_be_visible()
    assert card.locator('.practice-recording .player-chip').count()==0
    assert card.locator('.card-body').evaluate("e=>[...e.children].findIndex(n=>n.classList.contains('card-location')) < [...e.children].findIndex(n=>n.classList.contains('player-chips'))")
    page.goto(ui_server+'/#liked')
    expect(page.locator('.session-card .card-visual img').first).to_have_attribute('src',re.compile('/clips/'+CLIP+'/poster'))
    # Restore fixture assignments for the other engine and existing scenarios.
    fresh=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    r=page.request.put(ui_server+f'/api/sessions/{ID}/clips/players',data=dict(player_id=None,publication=fresh['published_at'],clips=[dict(id=c['id'],version=c['player_assignment']['version']) for c in fresh['clips']]),headers=headers);assert r.ok
    r=page.request.put(ui_server+f'/api/sessions/{ID}/players',data=dict(player_ids=[],version=fresh['player_assignment']['version']),headers=headers);assert r.ok
    page.close()


def _archive_test_metadata(page, ui_server):
    data=page.request.get(ui_server+f'/api/annotations/sources/{ID}').json()
    for a in data['annotations']:
        if a['kind']!='coach' and a['status']=='active':
            response=page.request.put(ui_server+'/api/annotations/'+a['id'],headers={'X-Tennis-Request':'1'},data={'publication':data['publication'],'version':a['version'],'status':'archived'})
            assert response.ok


@pytest.mark.parametrize('width',[390,1280])
def test_watch_metadata_and_cut_reports(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900},has_touch=width==390)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    _archive_test_metadata(page,ui_server)
    before=page.request.get(ui_server+f'/api/sessions/{ID}').json()['clips'][0]['like']['value']
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    page.locator('#video-seek').focus();page.keyboard.press('ArrowRight')
    page.locator('#moment-toggle').click()
    page.get_by_role('button',name='Highlight this moment',exact=True).click()
    expect(page.locator('.note-marker')).to_have_count(1)
    first_clip=page.locator('#clip-list .clip-row').first
    expect(first_clip.locator('.note-moment')).to_have_count(0)
    page.get_by_role('button',name='Dislike clip 1',exact=True).click()
    expect(page.locator('#note-demote')).to_have_attribute('aria-pressed','true')
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('highlights')
    expect(page.locator('#clip-list .clip-row')).to_have_count(1)
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('useful')
    expect(page.locator('#clip-list .clip-row')).to_have_count(2)
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-001.mp4'))
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('all')
    page.get_by_role('button',name='Add moment',exact=True).click()
    page.get_by_role('checkbox',name='End time',exact=True).check()
    page.get_by_label('Original start (seconds)',exact=True).fill('0.25')
    page.get_by_label('Original end (seconds)',exact=True).fill('1.25')
    text=f'Balanced reset {browser.browser_type.name} {width}'
    page.get_by_role('textbox',name='Note',exact=True).fill(text)
    page.get_by_role('button',name='Save moment',exact=True).click()
    row=page.locator('.note-item').filter(has_text=text)
    expect(row).to_be_visible()
    expect(first_clip.locator('.note-moment')).to_have_text('⚡')
    expect(first_clip.locator('.note-moment')).to_have_attribute('title','Moment')
    row.get_by_role('button',name='Jump to moment',exact=True).click()
    expect(page.locator('#player')).to_have_js_property('currentTime',0.25)
    row.get_by_label('Moment actions',exact=True).click()
    row.get_by_role('button',name='Edit',exact=True).click()
    expect(page.locator('#note-scope')).to_be_disabled()
    page.get_by_role('textbox',name='Note',exact=True).fill(text+' revised')
    page.get_by_role('button',name='Save moment',exact=True).click()
    row=page.locator('.note-item').filter(has_text=text+' revised')
    row.get_by_label('Moment actions',exact=True).click()
    row.get_by_role('button',name='History',exact=True).click()
    expect(row.locator('.note-history')).to_contain_text('v1')
    expect(row.locator('.note-history')).to_contain_text('v2')
    row.get_by_label('Moment actions',exact=True).click()
    row.get_by_role('button',name='Archive',exact=True).click()
    expect(page.locator('.note-item').filter(has_text=text+' revised')).to_have_count(0)
    expect(first_clip.locator('.note-moment')).to_have_count(0)
    page.get_by_role('checkbox',name='Show archived',exact=True).check()
    row=page.locator('.note-item').filter(has_text=text+' revised')
    row.get_by_label('Moment actions',exact=True).click()
    row.get_by_role('button',name='Restore',exact=True).click()
    expect(row).not_to_have_class(re.compile('archived'))
    expect(first_clip.locator('.note-moment')).to_have_text('⚡')
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    # Processing feedback stays on its own page, with explicit original bounds.
    page.get_by_label('More video actions',exact=True).click()
    page.get_by_role('link',name='Review cuts',exact=True).click()
    page.get_by_role('button',name='Report this cut',exact=True).click()
    page.get_by_role('checkbox',name='Too much downtime',exact=True).check()
    page.get_by_role('textbox',name='Comment',exact=True).fill('Waiting before the shot '+text)
    page.get_by_role('button',name='Save report',exact=True).click()
    report=page.locator('.note-item').filter(has_text='Waiting before the shot '+text)
    expect(report).to_be_visible();expect(report).to_contain_text('Too much downtime')
    report.get_by_role('button',name='Jump to moment',exact=True).click()
    expect(page.locator('#cut-video')).to_have_js_property('paused',True)
    report.get_by_role('button',name='Edit',exact=True).click()
    expect(page.locator('#note-target-clip')).to_be_disabled()
    expect(page.locator('#note-scope')).to_be_disabled()
    page.get_by_role('button',name='Close note editor',exact=True).click()
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    _archive_test_metadata(page,ui_server)
    fresh=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    assert page.request.put(ui_server+f'/api/videos/{ID}/likes/{CLIP}',headers={'X-Tennis-Request':'1'},data=dict(liked=before,version=fresh['clips'][0]['like']['version'],publication=fresh['published_at'])).ok
    assert not errors
    page.close()


def test_single_clip_filter_and_duplicate_highlight(browser,ui_server):
    page=browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    _archive_test_metadata(page,ui_server)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    expect(page.locator('#liked-filter')).to_have_count(0)
    expect(page.get_by_role('button',name='Select clips',exact=True)).not_to_be_visible()
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('liked')
    expect(page.locator('#clip-list .clip-row')).to_have_count(1)
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('all')
    expect(page.locator('#clip-list .clip-row')).to_have_count(3)
    page.get_by_label('Clip list actions',exact=True).click()
    menu=page.locator('.clip-actions-menu > div').bounding_box()
    assert menu['x']>=0 and menu['x']+menu['width']<=390
    page.get_by_role('button',name='Select clips',exact=True).click()
    expect(page.locator('#clip-selection')).to_be_visible()
    page.get_by_label('Clip list actions',exact=True).click()
    page.get_by_role('button',name='Select clips',exact=True).click()
    expect(page.locator('#clip-selection')).not_to_be_visible()
    page.locator('#moment-toggle').click()
    page.get_by_role('button',name='Highlight this moment',exact=True).click()
    expect(page.locator('.note-marker')).to_have_count(1)
    page.get_by_role('button',name='Highlight this moment',exact=True).click()
    expect(page.locator('#toast')).to_contain_text('already highlighted')
    expect(page.locator('.note-marker')).to_have_count(1)
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    _archive_test_metadata(page,ui_server);page.close()


def test_learning_navigation_saved_reviews_and_player_switch(browser,ui_server):
    page=browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    # Mock saved review metadata only; video still uses the real fixture routes.
    players=page.request.get(ui_server+'/api/players').json()['players']
    if not players:
        created=page.request.post(ui_server+'/api/players',headers={'X-Tennis-Request':'1'},data={'name':'Learner','height_cm':None,'level':'beginner'}).json()
        players=[created]
    person=players[0];c=page.request.get(ui_server+f'/api/sessions/{ID}').json()['clips'][0]
    entry=dict(id=ID+':'+CLIP,source_id=ID,clip_id=CLIP,clip_index=1,title='Saved coaching fixture',recorded_at='2026-10-24T17:00:00-07:00',original_start=0,original_end=2,duration_seconds=2,media_available=True,poster_url=f'/api/sessions/{ID}/clips/{CLIP}/poster',reviews=[dict(id='saved-review',original_start=.25,original_end=1.25,updated_at=1,summary='Reset with balance.',answer='Recover with soft knees.',historical=False,player_mismatch=False,association='review',answer_truncated=False)])
    page.route('**/api/learning?*',lambda route:route.fulfill(json={'entries':[entry],'next_after':None}))
    page.goto(ui_server+'/')
    page.get_by_role('link',name='Learning',exact=True).click()
    expect(page.get_by_role('heading',name='Learning',exact=True)).to_be_visible()
    page.get_by_role('combobox',name='Learning player',exact=True).select_option(person['id'])
    expect(page.locator('.learning-card')).to_have_count(1)
    expect(page.locator('.learning-card')).to_contain_text('Reset with balance.')
    page.locator('.learning-review summary').click()
    expect(page.locator('.learning-answer')).to_have_text('Recover with soft knees.')
    page.get_by_role('combobox',name='Learning player',exact=True).select_option('')
    expect(page.locator('.learning-card')).to_have_count(0)
    for width in (320,390,1280):
        page.set_viewport_size({'width':width,'height':844})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.close()


@pytest.mark.parametrize('width',[390,1280])
def test_session_metadata_and_entity_note_targets(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900})
    page.goto(ui_server+'/')
    page.locator('.practice-options > summary').first.click()
    page.get_by_role('button',name='Session details',exact=True).first.click()
    page.get_by_role('textbox',name='Title',exact=True).fill('Practice session')
    page.get_by_role('textbox',name='Note',exact=True).fill('Recovery drills')
    page.get_by_role('button',name='Save',exact=True).click()
    expect(page.locator('#practice-editor')).not_to_be_visible()
    expect(page.locator('.session-note').first).to_have_text('Recovery drills')
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_label('More video actions',exact=True).click()
    page.get_by_role('button',name='Notes',exact=True).click()
    expect(page.get_by_role('dialog',name='Clip notes',exact=True)).to_be_visible()
    page.get_by_role('button',name='Add note',exact=True).click()
    assert page.locator('#note-scope option').evaluate_all('(items)=>items.map(i=>i.value)')==['clip']
    page.get_by_role('button',name='Close note editor',exact=True).click()
    page.get_by_role('button',name='Close entity notes',exact=True).click()
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.goto(ui_server+'/')
    page.locator('.practice-options > summary').first.click()
    page.get_by_role('button',name='Session details',exact=True).first.click()
    page.get_by_role('textbox',name='Title',exact=True).fill('')
    page.get_by_role('textbox',name='Note',exact=True).fill('')
    page.get_by_role('button',name='Save',exact=True).click()
    expect(page.locator('#practice-editor')).not_to_be_visible()
    page.close()


@pytest.mark.parametrize('width',[390,1280])
def test_review_actions_follow_video_or_clip_and_editor_keeps_its_target(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900})
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#review-kind')).to_have_text('Clip review')
    expect(page.locator('#player-like button')).to_have_attribute('data-like-item',CLIP)
    expect(page.locator('#review-like button')).to_have_attribute('data-like-item',CLIP)
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.get_by_role('dialog',name='Clip details',exact=True)).to_be_visible()
    expect(page.locator('#edit-location')).not_to_be_visible()
    expect(page.get_by_role('button',name='Edit players',exact=True)).not_to_be_visible()
    expect(page.get_by_role('button',name='Edit main player',exact=True)).to_be_visible()
    page.get_by_role('textbox',name='Title',exact=True).fill('Recovery cut')
    # Playback/current selection can change while an editor is open. Its saved
    # target must remain the clip for which the dialog was opened.
    page.evaluate("()=>{model.current='cccccccccccccccccccccccc';updatePlayerState();}")
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).not_to_be_visible()
    fresh=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    assert fresh['title']=='Practice <script>'
    assert fresh['clips'][0]['title']=='Recovery cut' and fresh['clips'][1]['title']!='Recovery cut'
    page.get_by_role('button',name='Full practice ↗',exact=True).click()
    expect(page.locator('#review-kind')).to_have_text('Video review')
    expect(page.locator('#review-title')).to_have_text('Practice <script>')
    expect(page.locator('#player-like button')).to_have_attribute('data-like-item','video')
    expect(page.locator('#review-like button')).to_have_attribute('data-like-item','video')
    old_clip_likes=[c['like'] for c in fresh['clips']]
    page.locator('#player-like button').click()
    expect(page.locator('#player-like button')).to_have_attribute('aria-pressed',str(not fresh['like']['value']).lower())
    updated=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    assert [c['like'] for c in updated['clips']]==old_clip_likes
    page.locator('#player-like button').click()  # Restore the fixture's video favorite.
    page.get_by_role('button',name='Edit video',exact=True).click()
    expect(page.get_by_role('dialog',name='Video details',exact=True)).to_be_visible()
    expect(page.locator('#edit-location')).to_be_visible()
    expect(page.get_by_role('button',name='Edit main player',exact=True)).not_to_be_visible()
    page.get_by_role('button',name='Close',exact=True).click()
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    # Restore the clip title so subsequent engines exercise the same fixture.
    clip=updated['clips'][0]
    r=page.request.put(ui_server+f'/api/videos/{ID}/clips/{CLIP}/details',data=dict(title='Clip 01',publication=updated['published_at'],version=clip['details']['version']),headers={'X-Tennis-Request':'1'});assert r.ok
    page.close()


def _playback_moment(identity,kind,start,end=None,clip=CLIP,clip_start=0):
    local=start-clip_start
    return dict(id=identity,kind=kind,record_class='moment',status='active',version=1,
        target=dict(scope='point' if end is None else 'interval',start_seconds=start,**({} if end is None else dict(end_seconds=end))),
        body=dict(text='Cue '+identity),player_id=None,origin=dict(type='human'),read_only=kind=='coach',
        playback_eligible=True,related_clip_ids=[clip],visible_intervals=[dict(clip_id=clip,
        original_start=start,original_end=end or start,local_start=local,local_end=local if end is None else end-clip_start,
        playback_start=start,playback_end=end or start)],relation='retained')


def _mock_playback_moments(page,ui_server,items):
    page.route('**/api/recordings/'+ID+'/metadata*',lambda route:route.fulfill(json=dict(annotations=items,next_after=None)))


@pytest.mark.parametrize('width',[390,1280])
def test_moment_cues_default_durations_ranges_and_collapsed_area(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    rows=[_playback_moment('point','highlight',.25),_playback_moment('coach-point','coach',.5),_playback_moment('range','note',.1,1.7)]
    rows[0]['body']['text']=''
    _mock_playback_moments(page,ui_server,rows)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','false')
    expect(page.get_by_role('button',name='Highlight this moment',exact=True)).to_be_visible()
    expect(page.locator('.note-marker')).to_have_count(3)
    assert page.locator('#note-markers').evaluate("e=>e.parentElement.classList.contains('video-seek-label')")
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.25;}')
    expect(page.locator('[data-active-moment="point"]')).to_have_count(0)
    expect(page.locator('[data-active-moment="range"]')).to_be_visible()
    expect(page.locator('[data-active-moment="point"]')).to_have_count(0,timeout=2500)
    # A point expiry does not repeatedly re-trigger at a paused playhead.
    expect(page.locator('[data-active-moment="range"]')).to_be_visible()
    page.locator('#player').evaluate('v=>{v.currentTime=.5;}')
    expect(page.locator('[data-active-moment="coach-point"]')).to_be_visible()
    expect(page.locator('[data-active-moment="coach-point"]')).to_have_count(0,timeout=4500)
    expect(page.locator('[data-active-moment="range"]')).to_be_visible()
    page.locator('#player').evaluate('v=>{v.currentTime=1.75;}')
    expect(page.locator('[data-active-moment="range"]')).to_have_count(0)
    page.locator('#player').evaluate('v=>{v.currentTime=.25;}')
    expect(page.locator('[data-active-moment="point"]')).to_have_count(0)
    expect(page.locator('#player')).to_have_js_property('paused',True)
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','false')
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    assert not errors
    page.close()


@pytest.mark.parametrize('width',[320,390,1280])
def test_moment_panel_scope_filters_jump_and_full_video(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900})
    first=_playback_moment('first','highlight',.25);first['body']['text']=''
    rows=[first,_playback_moment('comment','note',.75),_playback_moment('later','highlight',4.25,clip='d'*24,clip_start=4)]
    archived=_playback_moment('old','note',1);archived.update(status='archived',playback_eligible=False);rows.append(archived)
    wrong=_playback_moment('stale-match','note',4.5);rows.append(wrong)
    metadata=_playback_moment('not-a-moment','usefulness',.5);metadata['record_class']='entity_metadata';rows.append(metadata)
    _mock_playback_moments(page,ui_server,rows)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled();expect(page.locator('.note-marker')).to_have_count(2)
    clips=page.locator('#clip-list .clip-row')
    expect(clips.nth(0).locator('.note-moment')).to_have_text('⚡')
    expect(clips.nth(0).locator('.note-moment')).to_have_attribute('title','Moment')
    expect(clips.nth(1).locator('.note-moment')).to_have_count(0)
    expect(clips.nth(2).locator('.note-moment')).to_have_count(0)
    expect(page.get_by_role('button',name='Highlight this moment',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='Add moment',exact=True)).to_be_visible()
    expect(page.get_by_role('button',name='Jump to next moment',exact=True)).to_be_visible()
    expect(page.locator('#moment-panel-content')).not_to_be_visible()
    page.locator('#moment-toggle').click()
    expect(page.locator('#notes-view')).to_have_count(0)
    expect(page.locator('#notes-list .note-item')).to_have_count(2)
    expect(page.locator('#notes-list')).not_to_contain_text('Less useful')
    page.get_by_role('combobox',name='Moment type',exact=True).select_option('highlight')
    expect(page.locator('#notes-list .note-item')).to_have_count(1)
    page.get_by_role('combobox',name='Moment type',exact=True).select_option('all')
    page.get_by_role('checkbox',name='Show archived',exact=True).check()
    expect(page.locator('#notes-list .note-item')).to_have_count(3)
    expect(page.locator('.note-marker')).to_have_count(2)
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.3;}')
    page.get_by_role('button',name='Jump to next moment',exact=True).click()
    expect(page.locator('#player')).to_have_js_property('currentTime',.75)
    expect(page.locator('#player')).to_have_js_property('paused',True)
    page.get_by_role('button',name='Jump to next moment',exact=True).click()
    expect(page.locator('#player')).to_have_js_property('currentTime',.25)
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-001.mp4'))
    page.get_by_role('button',name='Next clip',exact=True).click()
    expect(page.locator('#notes-list .note-item')).to_have_count(0)
    expect(page.get_by_role('button',name='Jump to next moment',exact=True)).not_to_be_visible()
    expect(page.locator('#moment-cues')).to_be_empty()
    page.locator('#player').evaluate('v=>v.pause()')
    page.get_by_role('button',name='Full practice ↗',exact=True).click()
    expect(page.locator('.note-marker')).to_have_count(3)
    expect(page.locator('#notes-list .note-item')).to_have_count(4)
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=1;}')
    page.get_by_role('button',name='Jump to next moment',exact=True).click()
    expect(page.locator('#player')).to_have_js_property('currentTime',4.25)
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.close()


def test_structured_coaching_moment_renders_readable_cue(browser,ui_server):
    page=browser.new_page(viewport={'width':390,'height':844})
    a=_playback_moment('structured-review','coach',.2,1.5)
    a['body']=dict(response=True,text=json.dumps(dict(schema_version=2,summary='Recover after each shot.',suggestions=[dict(title='Ready position',cue='Bend knees.',drill='Reset ten times.')])) )
    a['playback_eligible']=False
    _mock_playback_moments(page,ui_server,[a])
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    expect(page.locator('#clip-list .clip-row').first.locator('.note-moment')).to_have_text('⚡')
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.3;}')
    expect(page.locator('#moment-cues')).to_contain_text('Recover after each shot.')
    expect(page.locator('#moment-cues')).not_to_contain_text('schema_version')
    page.locator('#moment-toggle').click()
    page.get_by_text('Read saved response',exact=True).click()
    expect(page.locator('#notes-list')).to_contain_text('Cue: Bend knees.')
    expect(page.locator('#notes-list')).not_to_contain_text('schema_version')
    page.close()


@pytest.mark.parametrize('width',[390,1280])
def test_add_moment_optional_note_end_and_captured_scope(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900})
    _archive_test_metadata(page,ui_server)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','false')
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.5;}')
    page.get_by_role('button',name='Add moment',exact=True).click()
    expect(page.get_by_role('dialog',name='Add moment',exact=True)).to_be_visible()
    expect(page.get_by_label('Original start (seconds)',exact=True)).to_have_value('0.5')
    expect(page.get_by_label('Original end (seconds)',exact=True)).not_to_be_visible()
    page.get_by_role('checkbox',name='End time',exact=True).check()
    page.get_by_label('Original end (seconds)',exact=True).fill('0.4')
    page.get_by_role('button',name='Save moment',exact=True).click()
    expect(page.locator('#note-editor-error')).to_contain_text('optional later end')
    page.get_by_label('Original end (seconds)',exact=True).fill('3')
    text=f'Cross-clip moment {width} {browser.browser_type.name}'
    page.get_by_role('textbox',name='Note',exact=True).fill(text)
    page.get_by_role('button',name='Save moment',exact=True).click()
    row=page.locator('#notes-list .note-item').filter(has_text=text)
    expect(row).to_be_visible()
    clips=page.locator('#clip-list .clip-row')
    expect(clips.nth(0).locator('.note-moment')).to_have_text('⚡')
    expect(clips.nth(1).locator('.note-moment')).to_have_text('⚡')
    row.get_by_label('Moment actions',exact=True).click()
    row.get_by_role('button',name='Edit',exact=True).click()
    page.get_by_role('textbox',name='Note',exact=True).fill('')
    page.get_by_role('checkbox',name='Change timing or target',exact=True).check()
    page.get_by_role('checkbox',name='End time',exact=True).uncheck()
    page.get_by_role('button',name='Save moment',exact=True).click()
    expect(page.locator('#note-editor')).not_to_be_visible()
    expect(clips.nth(0).locator('.note-moment')).to_have_text('⚡')
    expect(clips.nth(1).locator('.note-moment')).to_have_count(0)
    rows=page.request.get(ui_server+f'/api/recordings/{ID}/moments').json()['moments']
    point=next(m for m in rows if m['status']=='active' and m['start_ms']==500 and m['payload']['text']=='')
    assert 'end_ms' not in point
    # Blank text keeps a marker, but creates no teaser.
    page.locator('#player').evaluate('v=>{v.currentTime=.5;}')
    expect(page.locator('#moment-cues')).to_be_empty()
    # The editor captures the original clip scope even if playback changes.
    page.get_by_role('button',name='Add moment',exact=True).click()
    page.get_by_role('textbox',name='Note',exact=True).fill('Captured clip moment')
    page.evaluate("()=>{model.current='cccccccccccccccccccccccc';updatePlayerState();}")
    page.get_by_role('button',name='Save moment',exact=True).click()
    expect(page.locator('#note-editor')).not_to_be_visible()
    expect(clips.nth(0).locator('.note-moment')).to_have_text('⚡')
    expect(clips.nth(0).locator('.note-moment')).to_have_attribute('title','Moment')
    expect(clips.nth(1).locator('.note-moment')).to_have_count(0)
    rows=page.request.get(ui_server+f'/api/recordings/{ID}/moments').json()['moments']
    assert next(m for m in rows if m['payload']['text']=='Captured clip moment')['start_ms']==500
    expect(page.locator('#notes-list')).not_to_contain_text('Captured clip moment')
    # Untimed notes have their own entity UI, outside Moments.
    page.get_by_label('More video actions',exact=True).click()
    page.get_by_role('button',name='Notes',exact=True).click()
    page.get_by_role('button',name='Add note',exact=True).click()
    page.get_by_role('textbox',name='Note',exact=True).fill('Second clip metadata')
    page.get_by_role('button',name='Save note',exact=True).click()
    expect(page.locator('#entity-notes-list')).to_contain_text('Second clip metadata')
    expect(clips.nth(1).locator('.note-moment')).to_have_count(0)
    expect(page.locator('#entity-notes-list').get_by_role('button',name='Jump to moment',exact=True)).to_have_count(0)
    note=page.locator('#entity-notes-list .note-item').filter(has_text='Second clip metadata')
    note.get_by_label('Note actions',exact=True).click()
    note.get_by_role('button',name='Edit',exact=True).click()
    page.get_by_role('textbox',name='Note',exact=True).fill('Second clip metadata revised')
    page.get_by_role('button',name='Save note',exact=True).click()
    expect(page.locator('#entity-notes-list')).to_contain_text('Second clip metadata revised')
    page.get_by_role('button',name='Close entity notes',exact=True).click()
    expect(page.locator('#notes-list')).not_to_contain_text('Second clip metadata')
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    _archive_test_metadata(page,ui_server)
    page.close()


def test_moment_jump_preserves_playing_state_focus_and_scroll(browser,ui_server):
    page=browser.new_page(viewport={'width':390,'height':844})
    _mock_playback_moments(page,ui_server,[_playback_moment('next','note',.7)])
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    jump=page.get_by_role('button',name='Jump to next moment',exact=True)
    expect(jump).to_be_visible();jump.scroll_into_view_if_needed();jump.focus()
    source=page.locator('#player').get_attribute('src');scroll=page.evaluate('scrollY')
    page.locator('#player').evaluate('async v=>{v.currentTime=0;await v.play();}')
    jump.press('Enter')
    expect(page.locator('#player')).to_have_js_property('paused',False)
    expect(page.locator('#player')).to_have_attribute('src',source)
    expect(jump).to_be_focused()
    assert abs(page.evaluate('scrollY')-scroll)<2
    page.locator('#player').evaluate('v=>v.pause()')
    assert .7<=page.locator('#player').evaluate('v=>v.currentTime')<1.5
    page.close()


@pytest.mark.parametrize('width',[320,390,1280])
def test_adjacent_thumb_reactions_are_exclusive_and_scoped(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900},has_touch=width<650)
    _archive_test_metadata(page,ui_server)
    initial=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    group=page.get_by_role('group',name='Clip feedback',exact=True)
    expect(group).to_be_visible()
    assert group.locator('button').count()==2 and group.locator('svg').count()==2
    assert group.evaluate("e=>getComputedStyle(e).borderTopWidth")=="0px"
    for button in group.locator('button').all():
        assert button.evaluate("e=>getComputedStyle(e).borderTopWidth")=="0px"
        box=button.bounding_box();study=page.locator('#study-clip').bounding_box()
        assert abs(box['width']-study['width'])<1 and abs(box['height']-study['height'])<1
    expect(page.locator('.note-quick-actions')).to_have_count(0)
    page.locator('#note-demote').click()
    expect(page.locator('#note-demote')).to_have_attribute('aria-pressed','true')
    expect(page.locator('#player-like button')).to_have_attribute('aria-pressed','false')
    expect(page.locator('#review-like button')).to_have_attribute('aria-pressed','false')
    page.locator('#player-like button').click()
    expect(page.locator('#player-like button')).to_have_attribute('aria-pressed','true')
    expect(page.locator('#note-demote')).to_have_attribute('aria-pressed','false')
    page.locator('#player-like button').click()
    expect(page.locator('#player-like button')).to_have_attribute('aria-pressed','false')
    page.get_by_role('button',name='Full practice ↗',exact=True).click()
    expect(page.get_by_role('group',name='Video feedback',exact=True)).to_be_visible()
    page.locator('#note-demote').click()
    expect(page.locator('#note-demote')).to_have_attribute('aria-pressed','true')
    expect(page.locator('#player-like button')).to_have_attribute('aria-pressed','false')
    # Clicking the selected downvote returns to neutral, never resurrecting an upvote.
    page.locator('#note-demote').click()
    expect(page.locator('#note-demote')).to_have_attribute('aria-pressed','false')
    expect(page.locator('#player-like button')).to_have_attribute('aria-pressed','false')
    page.locator('#note-demote').click()
    expect(page.locator('#note-demote')).to_have_attribute('aria-pressed','true')
    page.locator('#review-like button').click()
    expect(page.locator('#note-demote')).to_have_attribute('aria-pressed','false')
    expect(page.locator('#player-like button')).to_have_attribute('aria-pressed','true')
    page.locator('#moment-toggle').click()
    expect(page.locator('#notes-list')).not_to_contain_text('Less useful')
    expect(page.locator('#notes-list')).not_to_contain_text('Dislike')
    up=group.locator('#player-like button').bounding_box() if group.count() else page.locator('#player-like button').bounding_box()
    down=page.locator('#note-demote').bounding_box()
    assert abs(up['y']-down['y'])<1 and down['x']>=up['x']+up['width']-1
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    # All state in this test is isolated test media; restore shared fixture favorites.
    fresh=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    for item,old,new in [('video',initial['like'],fresh['like']),(CLIP,initial['clips'][0]['like'],fresh['clips'][0]['like'])]:
        assert page.request.put(ui_server+f'/api/videos/{ID}/likes/{item}',headers={'X-Tennis-Request':'1'},data=dict(liked=old['value'],version=new['version'],publication=fresh['published_at'])).ok
    page.close()
