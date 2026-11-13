"""Run with: pytest tests/ui_player.py (requires Playwright Chromium/WebKit)."""
import copy
import json
import os
import re
from pathlib import Path
import shutil
import threading
import time

import pytest
from playwright.sync_api import sync_playwright, expect
from werkzeug.serving import make_server

from test_app import setup, ID, CLIP


@pytest.fixture(scope='module')
def ui_server(tmp_path_factory):
    app, root, data = setup.__wrapped__(tmp_path_factory.mktemp('player-ui'))
    data['published_at'] += 1  # New cut generation, not an in-place manifest mutation.
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


def test_autofocus_real_worker_processes_local_pixels(browser, ui_server):
    # Exercise the shipped model/runtime under real CSP without assuming a
    # learned model must recognize a particular hand-drawn illustration.
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    requests = []
    errors = []
    page.on('request', lambda request: requests.append((request.method, request.url)))
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.add_init_script("""(() => {
      const NativeWorker=window.Worker;
      window.__realWorkerEvents=[];
      window.Worker=function(...args) {
        const worker=new NativeWorker(...args);
        const record=(type,detail={})=>window.__realWorkerEvents.push({type,at:Math.round(performance.now()),...detail});
        worker.addEventListener('message',({data})=>record(data.type,{message:data.message,code:data.code}));
        worker.addEventListener('error',event=>record('worker_error',{message:event.message}));
        const post=worker.postMessage.bind(worker),terminate=worker.terminate.bind(worker);
        worker.postMessage=(data,...rest)=>{record('send:'+data.type);return post(data,...rest);};
        worker.terminate=()=>{record('terminate');return terminate();};
        return worker;
      };
    })()""")
    page.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    page.wait_for_function("() => document.querySelector('#player')?.readyState >= 2")
    button = page.get_by_role('button', name='Auto focus main player', exact=True)
    button.click()
    try:
        page.wait_for_function("() => Number(document.querySelector('#player')?.dataset.autofocusDetections || 0) >= 1 || document.querySelector('#player')?.dataset.autofocusState === 'unavailable'", timeout=45000)
        events = page.evaluate('window.__realWorkerEvents')
        unsupported = any(event['type'] == 'unsupported' and event.get('code') == 'worker_graphics_unavailable' for event in events)
        if unsupported:
            # Some WebKit ports expose canvas APIs without worker WebGL. This is
            # a capability fallback, not permission to accept arbitrary failures.
            assert browser.browser_type.name != 'chromium', 'Reference Chromium must run real inference'
            expect(button).to_be_disabled()
            expect(button).to_have_attribute('aria-pressed', 'false')
            expect(button).to_have_attribute('data-autofocus-state', 'unavailable')
            page.locator('#video-stage').hover()
            page.get_by_role('button', name='Play video', exact=True).click()
            page.wait_for_function("() => !document.querySelector('#player').paused")
            assert page.locator('#player').evaluate('v => v.style.transform') == ''
            assert not errors
            assert all(url.startswith(ui_server + '/') and method == 'GET' for method, url in requests)
            page.close()
            return
        assert int(page.locator('#player').get_attribute('data-autofocus-detections') or '0') >= 1
    except Exception as error:
        state = page.locator('#player').evaluate('v => ({state:v.dataset.autofocusState,captures:v.dataset.autofocusCaptures,inflight:v.dataset.autofocusInflight})')
        events = page.evaluate('window.__realWorkerEvents')
        raise AssertionError(f'Real detector did not process a frame: {state}; worker_events={events}; errors={errors}') from error
    expect(button).to_have_attribute('aria-pressed', 'true')
    assert button.get_attribute('data-autofocus-state') in ('searching', 'tracking', 'holding')
    assert any('/static/vendor/mediapipe/vision_wasm_internal.wasm' in url for _, url in requests)
    assert any('/static/vendor/mediapipe/efficientdet_lite0.tflite' in url for _, url in requests)
    assert all(url.startswith(ui_server + '/') and method == 'GET' for method, url in requests)
    assert not errors
    page.locator('#video-stage').hover()
    button.click()
    expect(button).to_have_attribute('aria-pressed', 'false')
    assert page.locator('#player').evaluate('v => v.style.transform') == ''
    page.close()


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
    expect(page.locator('#study-mute,#study-reset')).to_have_count(0)
    expect(page.locator('#study-back')).to_have_text('<')
    expect(page.locator('#study-forward')).to_have_text('>')
    expect(page.locator('#study-pin-start')).to_have_attribute('aria-valuenow', '0')
    expect(page.locator('#study-pin-end')).to_have_attribute('aria-valuenow', '2')
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
    loop_btn = dialog.get_by_role('button', name='Loop clip', exact=True)
    expect(loop_btn).to_have_attribute('aria-pressed', 'false')
    loop_btn.click()
    expect(loop_btn).to_have_attribute('aria-pressed', 'true')
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
    expect(dialog.locator('.study-extra-tools')).not_to_have_attribute('open', '')
    dialog.get_by_text('Recording & frame tools', exact=True).click()
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
    expect(page.locator('#coach-ask')).to_be_enabled()
    page.locator('#study-pin-start').evaluate("el => { el.value = 0.2; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    page.locator('#study-pin-end').evaluate("el => { el.value = 1.4; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    page.get_by_label('Preset',exact=True).select_option('footwork')
    expect(page.locator('#coach-question')).to_have_value(re.compile('footwork'))
    page.get_by_role('textbox',name='Question for coach',exact=True).fill('Check my balance')
    zoom=page.get_by_role('slider',name='Video zoom',exact=True)
    zoom.press('Home')
    for _ in range(4):zoom.press('ArrowRight')
    with page.expect_response(lambda r:r.url.endswith('/api/coaching/exports') and r.request.method=='POST') as event:
        page.get_by_role('button',name='Ask coach',exact=True).click()
    response=event.value
    assert response.status==201
    job=response.json()
    assert job['role']=='render' and job['state']=='queued'
    spec=job['body']
    assert spec['question']=='Check my balance' and spec['speed']==.5
    assert spec['start']==pytest.approx(0.2,abs=0.01) and spec['end']==pytest.approx(1.4,abs=0.01)
    assert 0 < min(spec['crop'][2:]) < 1
    expect(page.locator('#coach-ask')).to_be_disabled()
    expect(page.locator('#coach-status')).to_contain_text('Preparing your clip…')
    with page.expect_response(lambda r:r.url.endswith('/api/coaching/exports') and r.request.method=='POST') as share_event:
        page.get_by_role('button',name='Download interval',exact=True).click()
    share_job=share_event.value.json()
    assert share_job['body']['intent']=='share' and share_job['body']['quality']=='high'
    assert share_job['body']['start']==pytest.approx(0.2,abs=0.01) and share_job['body']['end']==pytest.approx(1.4,abs=0.01)
    expect(page.locator('#study-status')).to_contain_text('Preparing high-quality download…')
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
        expect(page.locator('#video-fullscreen')).to_have_count(1)
        expect(page.locator('.video-view-actions button:visible')).to_have_count(1 if width<=650 else 2)
        expect(page.get_by_role('button',name='Enter fullscreen',exact=True)).to_be_visible()
        assert page.locator('#video-expand,#video-close-expanded,.player-expanded').count()==0
        assert page.locator('#video-stage').get_attribute('role')=='region'
    page.close()


@pytest.mark.parametrize('width',[1280,390])
def test_structured_coaching_evidence_review_and_draft(browser, ui_server, width):
    """Browser rendering contract; real validation/retention is covered by API tests."""
    import base64
    page=browser.new_page(viewport={'width':width,'height':844},has_touch=width<761)
    errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
    source=page.request.get(ui_server+'/api/sessions/'+ID).json()
    publication=source['published_at']
    frame=dict(frame_id='f_test',order=1,sha256='e'*64,original_seconds=.5,clip_seconds=.5,excerpt_seconds=1)
    content=dict(title='Balanced reset',topic='Ready position',observation='A narrow stance in this still.',cue='Reset with soft knees.',drill='Recover and hold.',uncertainty='Stills cannot confirm split-step timing.',evidence_frame_ids=['f_test'],applies_to=[dict(start_seconds=.25,end_seconds=1.5)],validation_warnings=[],timing_status='timed')
    annotation=dict(id='a_test',job_id='job-test',current=content,proposal=copy.deepcopy(content),status='proposed',historical=False,playback_eligible=False,version=1,provenance=dict(clip_id=CLIP,publication=publication),related_clip_ids=[CLIP])
    job=dict(id='job-test',version=1,role='ai',state='completed',asked_at=1700000100.0,updated=1700000200.0,body=dict(source_id=ID,clip_id=CLIP,publication=publication,export_id='render-test',original_start=0,original_end=1.8,start=0,end=1.8,prompt='Check balance',evidence_frames=[frame]),result=dict(answer='Original test response',structured=dict(summary='One observable improvement.',validation_warnings=[],parse_status='valid')))
    rendered=dict(id='render-test',version=1,role='render',state='completed',body=dict(source_id=ID,clip_id=CLIP,publication=publication,start=0,end=1.8,original_start=0,original_end=1.8,speed=.5,crop=[0,0,1,1]),result=dict(bytes=1000,output_seconds=3.6,frames=[frame]))
    state=dict(jobs=[job,rendered],annotations=[annotation],workers=[],enabled=True)
    page.route('**/api/coaching/exports/render-test/excerpt.mp4',lambda route:route.fulfill(body=(Path(__file__).parent/'fixtures/clip.mp4').read_bytes(),content_type='video/mp4'))
    page.route('**/api/coaching/sessions/*',lambda route:route.fulfill(json=state))
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=')
    page.route('**/api/coaching/evidence/*/*.jpg',lambda route:route.fulfill(body=png,content_type='image/png'))
    fail_next_update={'conflict':False}
    def update(route):
        if fail_next_update['conflict']:
            fail_next_update['conflict']=False
            route.fulfill(status=409,json=dict(error='This suggestion changed. Reload before saving your review'))
            return
        body=route.request.post_data_json
        assert body['version']==annotation['version']
        if 'content' in body:
            annotation['current']=dict(annotation['current'],**body['content'])
        annotation.update(status=body['status'],version=annotation['version']+1,playback_eligible=body['status']=='accepted')
        route.fulfill(json=annotation)
    page.route('**/api/coaching/annotations/a_test',update)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}&coach_job=job-test&coach_annotation=a_test&coach_time=0.25')
    expect(page.locator('#clip-study')).to_be_visible()
    expect(page.locator('#study-pin-start')).to_have_text('')
    expect(page.locator('#study-pin-end')).to_have_text('')
    pin_dims=page.locator('#study-pin-start').evaluate('el=>{const s=getComputedStyle(el);return [parseFloat(s.width),parseFloat(s.height),parseFloat(s.minWidth),parseFloat(s.minHeight)];}')
    assert all(v>=44 for v in pin_dims)
    assert float(page.locator('#study-pin-start').get_attribute('aria-valuenow'))==pytest.approx(0.25,abs=0.05)
    assert float(page.locator('#study-pin-end').get_attribute('aria-valuenow'))==pytest.approx(1.5,abs=0.05)
    question=page.get_by_role('textbox',name='Question for coach',exact=True)
    question.fill('Keep my custom question')
    expect(page.get_by_role('heading',name='Balanced reset',exact=True)).to_be_visible()
    expect(page.locator('.coach-asked-at')).to_contain_text('Asked ')
    card=page.locator('.coach-suggestion')
    expect(card).to_have_class(re.compile(r'\bis-highlighted\b'))
    expect(card.locator('.coach-review-state')).to_have_text('Needs your review')
    expect(card.get_by_role('button',name='Save',exact=True)).to_have_count(0)
    expect(card.get_by_role('button',name='Use in playback',exact=True)).to_be_visible()
    card.get_by_role('button',name='Use in playback',exact=True).click()
    expect(card.locator('.coach-review-state')).to_have_text('Saved · Active in playback')
    expect(card.get_by_role('button',name='Use in playback',exact=True)).to_have_count(0)
    expect(card.get_by_role('button',name='Save',exact=True)).to_have_count(0)
    image=card.locator('img');expect(image).to_be_visible()
    expect(image).to_have_attribute('src','/api/coaching/evidence/job-test/f_test.jpg')
    card.get_by_role('button',name=re.compile('Frame 1')).click()
    assert page.locator('#study-video').evaluate('v=>v.currentTime')==pytest.approx(.5,abs=.08)
    # Failed save (409 conflict) keeps dirty state so top-right × still protects unsaved edits.
    card.locator('.coach-editor summary').click()
    card.get_by_label('Playback cue',exact=True).fill('Unsaved edited cue')
    fail_next_update['conflict']=True
    card.get_by_role('button',name='Save edits and accept',exact=True).click()
    expect(page.locator('#coach-status')).to_contain_text('This suggestion changed. Reload before saving your review')
    expect(card.get_by_label('Playback cue',exact=True)).to_have_value('Unsaved edited cue')
    card.get_by_role('button',name='Discard suggestion',exact=True).click()
    expect(page.locator('#coach-status')).to_contain_text('Save or reset your edits before discarding')
    expect(card.locator('.coach-review-state')).to_have_text('Saved · Active in playback')
    # Reset edits restores the saved cue and clears dirty state, and successful save also clears dirty state.
    card.get_by_role('button',name='Reset edits',exact=True).click()
    expect(page.locator('#coach-status')).to_have_text('Edits reset.')
    card.locator('.coach-editor summary').click()
    expect(card.get_by_label('Playback cue',exact=True)).to_have_value('Reset with soft knees.')
    card.get_by_label('Playback cue',exact=True).fill('Saved edited cue')
    card.get_by_role('button',name='Save edits and accept',exact=True).click()
    expect(page.locator('#coach-status')).to_have_text('Suggestion saved.')
    card.get_by_role('button',name='Discard suggestion',exact=True).click()
    expect(card.locator('.coach-review-state')).to_have_text('Discarded')
    card.get_by_role('button',name='Restore',exact=True).click()
    expect(card.locator('.coach-review-state')).to_have_text('Saved · Active in playback')
    # Missing exact suggestion shows a truthful notice instead of silently masquerading parent response as the suggestion.
    page.evaluate("()=>window.Coach.focusTarget({clipId: '"+CLIP+"', jobId: 'job-test', annotationId: 'a_missing', startSeconds: 0.4, endSeconds: 1.2})")
    expect(page.locator('#coach-status')).to_contain_text('The selected coaching suggestion is unavailable; showing its parent coaching review.')
    expect(page.locator('.coach-suggestion.is-highlighted')).to_have_count(0)
    # Deferred loadedmetadata seek callback is fenced if Study closes and reopens before old metadata arrives.
    page.evaluate("""() => {
      const v = document.getElementById('study-video');
      Object.defineProperty(v, 'readyState', {configurable: true, get: () => 0});
      window.Coach.focusTarget({clipId: '"""+CLIP+"""', jobId: 'job-test', annotationId: 'a_test', startSeconds: 1.1, endSeconds: 1.5});
      document.getElementById('study-close').click();
      delete v.readyState;
    }""")
    page.get_by_role('button',name='Study clip',exact=True).click()
    page.wait_for_function("() => document.getElementById('study-video')?.readyState >= 1")
    page.evaluate("""() => {
      const v = document.getElementById('study-video');
      v.pause();
      v.currentTime = 0.2;
      v.dispatchEvent(new Event('loadedmetadata'));
    }""")
    assert page.locator('#study-video').evaluate('v=>v.currentTime')!=pytest.approx(1.1,abs=0.05)
    assert page.locator('#clip-study').evaluate('d=>d.scrollWidth<=d.clientWidth')
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    if width<761:page.get_by_role('button',name='Coach',exact=True).click()
    expect(question).to_have_value('Keep my custom question')
    page.get_by_role('button',name='Re-ask',exact=True).click()
    expect(question).to_be_visible()
    question.fill('Please reassess all snapshots, including footwork.')
    sent=[]
    def ask(route):
        sent.append(route.request.post_data_json)
        new=copy.deepcopy(job);new.update(id='follow-up-job',version=1,state='queued');new['body'].update(prompt=sent[-1]['prompt'],follow_up_of='job-test');state['jobs'].insert(0,new)
        route.fulfill(status=201,json=new)
    page.route('**/api/coaching/requests',ask)
    page.get_by_role('button',name='Ask coach',exact=True).click()
    expect(page.locator('#coach-status')).to_have_text('Getting response…')
    expect(page.locator('#coach-history')).to_contain_text('Queued')
    assert sent[0]['follow_up_of']=='job-test' and sent[0]['export_id']=='render-test'
    assert sent[0]['prompt']=='Please reassess all snapshots, including footwork.'
    assert not errors
    page.close()


def test_normal_playback_ignores_coaching_end_until_play_selection(browser,ui_server):
    page=browser.new_page()
    csp_errors=[]
    page.add_init_script("window.cspViolations=[]; document.addEventListener('securitypolicyviolation', e => window.cspViolations.push(e.blockedURI + ':' + e.violatedDirective));")
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button',name='Study clip',exact=True).click()
    expect(page.locator('#study-play')).to_be_enabled()
    bar_box=page.locator('#study-interval-bar').bounding_box()
    pin_start=page.locator('#study-pin-start')
    pin_end=page.locator('#study-pin-end')
    # Drag start pin to ~10% (0.2s) and end pin to ~35% (0.7s) along the progress bar.
    start_box=pin_start.bounding_box()
    page.mouse.move(start_box['x']+start_box['width']/2,start_box['y']+start_box['height']/2)
    page.mouse.down()
    page.mouse.move(bar_box['x']+bar_box['width']*0.1,bar_box['y']+bar_box['height']/2)
    page.mouse.up()
    end_box=pin_end.bounding_box()
    page.mouse.move(end_box['x']+end_box['width']/2,end_box['y']+end_box['height']/2)
    page.mouse.down()
    page.mouse.move(bar_box['x']+bar_box['width']*0.35,bar_box['y']+bar_box['height']/2)
    page.mouse.up()
    assert float(pin_start.get_attribute('aria-valuenow'))==pytest.approx(0.2,abs=0.05)
    assert float(pin_end.get_attribute('aria-valuenow'))==pytest.approx(0.7,abs=0.05)
    # Without loop, playing starts at intervalStart (0.2s) and pauses at intervalEnd (0.7s).
    page.get_by_role('button',name='Play',exact=True).click()
    page.wait_for_function("()=>document.querySelector('#study-video').paused&&document.querySelector('#study-video').currentTime>=0.65")
    assert page.locator('#study-video').evaluate('v=>v.currentTime')==pytest.approx(0.7,abs=0.06)
    # With loop enabled, reaching intervalEnd wraps back to intervalStart and keeps playing.
    page.get_by_role('button',name='Loop clip',exact=True).click()
    page.get_by_role('button',name='Play',exact=True).click()
    page.wait_for_function("()=>!document.querySelector('#study-video').paused&&document.querySelector('#study-video').currentTime>=0.18&&document.querySelector('#study-video').currentTime<=0.45")
    page.get_by_role('button',name='Pause',exact=True).click()
    assert page.evaluate('window.cspViolations')==[]
    page.close()


def test_player_profiles_assignments_and_filters(browser, ui_server):
    # A new fixture player avoids coupling to the other browser's shared registry.
    page = browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    name='Player '+browser.browser_type.name
    page.goto(ui_server+'/')
    page.locator('#manage-players').click()
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
    expect(page.locator('#coach-player-context')).to_contain_text(name)
    expect(page.locator('#coach-question')).not_to_have_value(re.compile('I am a beginner'))
    page.get_by_role('button',name='← Back to review',exact=True).click()
    page.goto(ui_server+'/')
    page.get_by_role('combobox',name='Filter by player',exact=True).select_option(label=name)
    expect(page.locator('.practice-card')).to_have_count(1)
    expect(page.locator('.practice-card .player-chip').first).to_be_visible()
    page.locator('#manage-players').click()
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
    assert page.locator('#note-clip-filter option').evaluate_all('(els)=>els.map(e=>e.textContent)')==['All clips','Recommended','Liked only','Highlights']
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('highlights')
    expect(page.locator('#clip-list .clip-row')).to_have_count(1)
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('recommended')
    expect(page.locator('#clip-list .clip-row')).to_have_count(0)
    expect(page.locator('#player')).to_have_attribute('src',re.compile('clip-001.mp4'))
    page.get_by_role('button',name='Remove dislike from clip 1',exact=True).click()
    expect(page.locator('#clip-list .clip-row')).to_have_count(1)
    page.get_by_role('button',name='Dislike clip 1',exact=True).click()
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
    page.get_by_role('link',name='Review cut',exact=True).click()
    page.get_by_role('checkbox',name='Too much downtime',exact=True).check()
    page.locator('#cut-comment').fill('Waiting before the shot '+text)
    page.locator('#cut-save').click()
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
    older_clip_newer_ask=dict(id=ID+':'+CLIP,source_id=ID,clip_id=CLIP,clip_index=1,title='Older recording newer ask',recorded_at='2026-10-20T17:00:00-07:00',original_start=0,original_end=2,duration_seconds=2,media_available=True,poster_url=f'/api/sessions/{ID}/clips/{CLIP}/poster',reviews=[dict(id='saved-review-newer',original_start=.25,original_end=1.25,asked_at=1700005000.0,updated_at=1700005100.0,summary='Reset with balance.',answer='Recover with soft knees.',historical=False,player_mismatch=False,association='review',answer_truncated=False)])
    newer_clip_older_ask=dict(id=ID+':'+('c'*24),source_id=ID,clip_id='c'*24,clip_index=2,title='Newer recording older ask',recorded_at='2026-10-24T17:00:00-07:00',original_start=2,original_end=4,duration_seconds=2,media_available=True,poster_url=f'/api/sessions/{ID}/clips/{"c"*24}/poster',reviews=[dict(id='saved-review-older',original_start=2.2,original_end=3.2,asked_at=1700001000.0,updated_at=1700009000.0,summary='Earlier ask.',answer='Earlier answer.',historical=False,player_mismatch=False,association='review',answer_truncated=False)])
    page.route('**/api/learning?*',lambda route:route.fulfill(json={'entries':[newer_clip_older_ask,older_clip_newer_ask],'next_after':None}))
    page.goto(ui_server+'/')
    page.get_by_role('link',name='Learning',exact=True).click()
    expect(page.get_by_role('heading',name='Learning',exact=True)).to_be_visible()
    page.get_by_role('combobox',name='Learning player',exact=True).select_option(person['id'])
    expect(page.locator('.learning-card')).to_have_count(2)
    assert page.locator('.learning-card h2').evaluate_all('els=>els.map(e=>e.textContent)')==['Older recording newer ask · Clip 1','Newer recording older ask · Clip 2']
    expect(page.locator('.learning-card').first).to_contain_text('Reset with balance.')
    expect(page.locator('.learning-card').first.locator('.learning-asked-at')).to_contain_text('Asked ')
    page.locator('.learning-review summary').first.click()
    expect(page.locator('.learning-answer').first).to_have_text('Recover with soft knees.')
    page.get_by_role('combobox',name='Learning player',exact=True).select_option('')
    expect(page.locator('.learning-card')).to_have_count(0)
    for width in (320,390,1280):
        page.set_viewport_size({'width':width,'height':844})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.close()


def test_learning_plan_tabs_practice_log_editor_cancel_history_and_citations(browser,ui_server):
    page=browser.new_page(viewport={'width':390,'height':844},has_touch=True)
    headers={'X-Tennis-Request':'1'}
    person=page.request.post(ui_server+'/api/players',headers=headers,data={'name':'PlanLearner','height_cm':172,'level':'intermediate'}).json()
    pid=person['id']

    # 1. Navigate to /learning, select PlanLearner, switch to Plan & progress tab, and test real practice log create/edit/cancel/history.
    page.goto(ui_server+f'/learning?player={pid}')
    expect(page.locator('#learning-tab-reviews')).to_have_attribute('aria-selected','true')
    page.locator('#learning-tab-plan').click()
    expect(page.locator('#learning-tab-plan')).to_have_attribute('aria-selected','true')
    expect(page.locator('#learning-panel-plan')).to_be_visible()

    page.locator('#learning-add-practice-btn').click()
    page.locator('#manual-practice-title').fill('Early split step on return')
    page.locator('#manual-practice-topic').fill('Footwork')
    page.locator('#manual-practice-cue').fill('Land before server contact')
    page.locator('#manual-practice-drill').fill('15 return split-steps')
    page.locator('#manual-practice-note').fill('Initial focus note')
    page.get_by_role('button',name='Save practice item',exact=True).click()

    item=page.locator('.learning-practice-item').first
    expect(item).to_be_visible()
    expect(item.locator('h4')).to_have_text('Early split step on return')
    expect(item).to_contain_text('Ongoing')

    # Open inline editor, type unsaved text, and verify Cancel restores clean saved state.
    item.locator('.learning-edit-practice-btn').click()
    item.locator('input[name="title"]').fill('Unsaved temporary title')
    item.locator('.learning-cancel-practice-btn').click()
    expect(item.locator('h4')).to_have_text('Early split step on return')

    # Edit status and note, save, and verify append-only history disclosure.
    item.locator('.learning-edit-practice-btn').click()
    item.locator('select[name="status"]').select_option('practiced')
    item.locator('textarea[name="note"]').fill('Practiced 30 minutes; better timing.')
    item.get_by_role('button',name='Save progress',exact=True).click()
    expect(item).to_contain_text('Practiced')
    expect(item).to_contain_text('Practiced 30 minutes; better timing.')
    item.locator('.learning-practice-history summary').click()
    expect(item.locator('.learning-history-list li')).to_have_count(2)
    expect(item.locator('.learning-history-list')).to_contain_text('v2')
    expect(item.locator('.learning-history-list')).to_contain_text('v1')

    # 2. Test active plan rendering, consent gate, plan update request, and citation deep-link into Clip Study.
    plan_requests=[]
    def handle_plan_post(route):
        plan_requests.append(route.request.post_data_json)
        route.fulfill(status=201,json={'id':'job-plan-ui-1','role':'ai','state':'queued','version':1,'body':{'purpose':'learning_plan','player_id':pid}})
    page.route('**/api/learning/plan',handle_plan_post)

    eid='coach:a_ui_citation_1'
    study_url=f'/review/{ID}?clip={CLIP}&coach_time=0.4&coach_end=1.4&coach_job=job_ui_1'
    mock_plan_state={
        'enabled':True,
        'active_plan':{
            'player_id':pid,
            'revision':1,
            'job_id':'job-plan-0',
            'input_digest':'digest-rev-1',
            'covered_state_digest':'digest-rev-1',
            'request_digest':'req-rev-1',
            'created_at':1700002000.0,
            'updated_at':1700002000.0,
            'coverage':{'covered_reviews_count':1,'covered_practice_logs_count':1,'truncated_reviews_count':0,'truncated_practice_logs_count':0},
            'plan':{
                'schema_version':1,
                'observed_summary':'Balanced split step preparation on baseline rallies.',
                'summary_evidence_ids':[eid],
                'focus_areas':[{
                    'item_key':'fi_ui_1',
                    'topic':'Footwork',
                    'title':'Split step timing',
                    'observed_issue':'Landing slightly late.',
                    'cue':'Land split step before opponent contact',
                    'drill':'Shadow rhythm drill',
                    'uncertainty':'Single camera angle.',
                    'evidence_ids':[eid],
                    'validation_warnings':[],
                }],
                'uncertainties':['Sampled frames cannot establish exact ball contact.'],
                'unassessed_areas':['Serve and net play were not present in reviewed clips.'],
                'progress_notes':'Player logged 30 minutes of split-step practice.',
                'next_evidence_requests':['Record 5 return-of-serve points from behind baseline.'],
                'validation_warnings':[],
                'parse_status':'valid',
            },
            'provenance':{'covered_evidence_count':1,'delta_evidence_count':1,'removed_evidence_count':0},
            'citations':{
                eid:{
                    'evidence_id':eid,
                    'job_id':'job_ui_1',
                    'source_id':ID,
                    'clip_id':CLIP,
                    'clip_index':1,
                    'recording_title':'Morning practice',
                    'asked_at':1700001000.0,
                    'original_start':0.4,
                    'original_end':1.4,
                    'review_status':'accepted',
                    'provenance_trust':'user_confirmed',
                    'title':'Split step cue',
                    'topic':'Footwork',
                    'cue':'Land before contact',
                    'historical':False,
                    'media_available':True,
                    'study_url':study_url,
                    'currently_valid':True,
                    'invalid_reason':None,
                },
            },
        },
        'revisions':[{'revision':1,'job_id':'job-plan-0','parent_revision':None,'status':'active','input_digest':'digest-rev-1','covered_state_digest':'digest-rev-1','request_digest':'req-rev-1','observed_summary':'Balanced split step preparation.','focus_count':1,'provenance':{'covered_evidence_count':1,'delta_evidence_count':1,'removed_evidence_count':0},'created_at':1700002000.0}],
        'practice_logs':[],
        'eligibility':{
            'can_generate':True,
            'reason':'updates_available',
            'expected_parent_revision':1,
            'input_digest':'digest-rev-2',
            'valid_reviews_count':2,
            'new_or_revised_reviews_count':1,
            'removed_reviews_count':0,
            'practice_log_changes_count':1,
            'profile_changed':False,
            'backlog_reviews_count':0,
            'backlog_practice_logs_count':0,
            'days_since_last_plan':3.0,
            'recommended_cadence_days':14,
            'requires_ambiguous_retry':False,
        },
        'pending_job':None,
    }
    page.route('**/api/learning?*',lambda route:route.fulfill(json={'entries':[],'next_after':None,'plan_state':mock_plan_state}))
    page.goto(ui_server+f'/learning?player={pid}&tab=plan')
    expect(page.locator('#learning-active-plan')).to_contain_text('Balanced split step preparation on baseline rallies.')
    expect(page.locator('#learning-active-plan')).to_contain_text('Serve and net play were not present in reviewed clips.')

    # Clicking Update learning plan without checking consent shows an error and does not call POST /api/learning/plan.
    page.locator('#learning-plan-generate').click()
    expect(page.locator('#learning-plan-error')).to_contain_text('Confirm sending review summaries')
    assert len(plan_requests)==0

    # Checking consent and clicking Update learning plan submits the exact parent revision and input digest.
    page.locator('#learning-plan-consent').check()
    page.locator('#learning-plan-generate').click()
    assert len(plan_requests)==1
    assert plan_requests[0]['expected_parent_revision']==1
    assert plan_requests[0]['input_digest']=='digest-rev-2'
    assert plan_requests[0]['consent'] is True

    for width in (320,390,1280):
        page.set_viewport_size({'width':width,'height':844})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')

    # Clicking the citation pill navigates directly to Clip Study for that review interval.
    citation_link=page.locator('#learning-active-plan a.learning-citation').first
    expect(citation_link).to_contain_text('User confirmed')
    citation_link.click()
    expect(page).to_have_url(re.compile(r'/review/' + ID))

    # 3. Verify background 3s poll while a plan job is queued does not wipe an unsaved manual practice form draft.
    pending_plan_state={**mock_plan_state,'pending_job':{'id':'job-poll-1','state':'queued','version':1}}
    page.route('**/api/learning?*',lambda route:route.fulfill(json={'entries':[],'next_after':None,'plan_state':pending_plan_state}))
    page.goto(ui_server+f'/learning?player={pid}&tab=plan')
    page.locator('#learning-add-practice-btn').click()
    page.locator('#manual-practice-title').fill('Unsaved draft preserved across poll')
    page.wait_for_timeout(3500)
    expect(page.locator('#manual-practice-title')).to_have_value('Unsaved draft preserved across poll')

    # 4. Verify switching players while a plan POST is in flight does not disable the next player's generate button.
    person_b=page.request.post(ui_server+'/api/players',headers=headers,data={'name':'PlanLearnerB','height_cm':175,'level':'beginner'}).json()
    pid_b=person_b['id']
    deferred_routes=[]
    page.unroute('**/api/learning/plan')
    page.route('**/api/learning/plan',lambda route:deferred_routes.append(route))
    page.route('**/api/learning?*',lambda route:route.fulfill(json={'entries':[],'next_after':None,'plan_state':mock_plan_state}))
    page.goto(ui_server+f'/learning?player={pid}&tab=plan')
    page.locator('#learning-plan-consent').check()
    page.locator('#learning-plan-generate').click()
    expect(page.locator('#learning-plan-generate')).to_be_disabled()
    page.locator('#learning-player').select_option(pid_b)
    expect(page.locator('#learning-player')).to_have_value(pid_b)
    assert len(deferred_routes)==1
    deferred_routes[0].fulfill(status=201,json={'id':'job-plan-ui-deferred','role':'ai','state':'queued','version':1,'body':{'purpose':'learning_plan','player_id':pid}})
    expect(page.locator('#learning-plan-generate')).to_be_enabled()
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
    expect(page.locator('.player-more-list').get_by_role('button',name='Notes',exact=True)).to_have_count(0)
    page.get_by_label('More video actions',exact=True).click()
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.get_by_role('dialog',name='Clip details',exact=True)).to_be_visible()
    page.get_by_role('button',name='Add note',exact=True).click()
    assert page.locator('#note-scope option').evaluate_all('(items)=>items.map(i=>i.value)')==['clip']
    page.get_by_role('button',name='Close note editor',exact=True).click()
    page.get_by_role('button',name='Close',exact=True).click()
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


def test_edit_dialog_entity_notes_scope_history_and_conflicts(browser,ui_server):
    page=browser.new_page(viewport={'width':1280,'height':900})
    _archive_test_metadata(page,ui_server)
    headers={'X-Tennis-Request':'1'}
    initial=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    pub=initial['published_at']
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    # 1. Create a clip note directly in Edit clip even if playback switches clip while open.
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.get_by_role('dialog',name='Clip details',exact=True)).to_be_visible()
    page.get_by_role('textbox',name='Note',exact=True).fill('Clip 1 primary note')
    page.evaluate("()=>{model.current='cccccccccccccccccccccccc';updatePlayerState();}")
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).not_to_be_visible()
    # Switch back to Clip 1 and verify existing note is retained and editable.
    page.evaluate(f"()=>{{model.current='{CLIP}';updatePlayerState();}}")
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.locator('#edit-note')).to_have_value('Clip 1 primary note')
    expect(page.locator('#entity-notes-list')).to_contain_text('Clip 1 primary note')
    # Concurrent update in another client triggers a conflict without losing the draft or misreporting partial save.
    notes=[a for a in page.request.get(ui_server+f'/api/recordings/{ID}/metadata').json()['annotations'] if a['record_class']=='entity_metadata' and a['kind']=='note' and a['status']=='active']
    assert len(notes)==1 and notes[0]['target']['clip_id']==CLIP
    note_id=notes[0]['id']
    r=page.request.put(ui_server+f'/api/metadata/{note_id}',headers=headers,data=dict(publication=pub,version=notes[0]['version'],body=dict(text='Concurrent remote clip note')))
    assert r.ok
    page.get_by_role('textbox',name='Title',exact=True).fill('Clip 01 retitled')
    page.get_by_role('textbox',name='Note',exact=True).fill('Stale local clip note draft')
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).to_be_visible()
    expect(page.locator('#edit-error')).to_contain_text('Details saved, but note could not be saved')
    expect(page.locator('#edit-note')).to_have_value('Stale local clip note draft')
    expect(page.locator('#edit-load-latest-note')).to_be_visible()
    page.locator('#edit-load-latest-note').click()
    expect(page.locator('#edit-note')).to_have_value('Concurrent remote clip note')
    page.get_by_role('textbox',name='Note',exact=True).fill('Merged clip note v3')
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).not_to_be_visible()
    # 2. Multiple existing notes on the same clip are preserved and history is retained.
    r=page.request.post(ui_server+'/api/metadata',headers=headers,data=dict(source_id=ID,publication=pub,kind='note',target=dict(scope='clip',clip_id=CLIP,start_seconds=0,end_seconds=2),body=dict(text='Second independent clip note'),request_id=f'extra-clip-note-{browser.browser_type.name}'))
    assert r.status==201
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.locator('#entity-notes-list .note-item')).to_have_count(2)
    expect(page.locator('#entity-notes-list')).to_contain_text('Merged clip note v3')
    expect(page.locator('#entity-notes-list')).to_contain_text('Second independent clip note')
    expect(page.locator('#edit-note')).to_have_value('')
    first_item=page.locator('#entity-notes-list .note-item').filter(has_text='Merged clip note v3')
    first_item.get_by_label('Note actions',exact=True).click()
    first_item.get_by_role('button',name='History',exact=True).click()
    expect(first_item.locator('.note-history')).to_contain_text('v1')
    expect(first_item.locator('.note-history')).to_contain_text('v2')
    expect(first_item.locator('.note-history')).to_contain_text('v3')
    page.get_by_role('textbox',name='Title',exact=True).fill('Clip 01')
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).not_to_be_visible()
    # Both clip notes remain active after saving title details.
    active_clip_notes=[a for a in page.request.get(ui_server+f'/api/recordings/{ID}/metadata').json()['annotations'] if a['record_class']=='entity_metadata' and a['kind']=='note' and a['status']=='active' and a['target'].get('clip_id')==CLIP]
    assert len(active_clip_notes)==2
    # 3. Video notes stay separate from Clip notes.
    page.get_by_role('button',name='Full practice ↗',exact=True).click()
    page.get_by_role('button',name='Edit video',exact=True).click()
    expect(page.get_by_role('dialog',name='Video details',exact=True)).to_be_visible()
    expect(page.locator('#entity-notes-list .note-item')).to_have_count(0)
    page.get_by_role('textbox',name='Note',exact=True).fill('Whole video note')
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).not_to_be_visible()
    page.get_by_role('button',name='Edit video',exact=True).click()
    expect(page.locator('#edit-note')).to_have_value('Whole video note')
    expect(page.locator('#entity-notes-list')).to_contain_text('Whole video note')
    page.get_by_role('button',name='Close',exact=True).click()
    # 4. Delayed details save preserves captured entity, note ID/version, and draft even if globals mutate mid-save.
    page.locator('#clip-list [data-play]').first.click()
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.get_by_role('dialog',name='Clip details',exact=True)).to_be_visible()
    # Archive the second clip note so Clip 1 has one active note to test single-note captured update across await.
    second_item=page.locator('#entity-notes-list .note-item').filter(has_text='Second independent clip note')
    second_item.get_by_label('Note actions',exact=True).click()
    second_item.get_by_role('button',name='Archive',exact=True).click()
    expect(page.locator('#edit-note')).to_have_value('Merged clip note v3')
    page.evaluate("""()=>{
        const orig=window.fetch;
        window.fetch=async function(url,opts){
            if(String(url).includes('/details')){
                window.fetch=orig;
                model.current='cccccccccccccccccccccccc';updatePlayerState();
                NoteUI.openEntityNotes({video_id:model.session.id,clip_id:'cccccccccccccccccccccccc',publication:model.session.published_at});
                document.querySelector('#edit-note').value='Mutated mid-await wrong clip draft';
            }
            return orig.apply(this,arguments);
        };
    }""")
    page.get_by_role('textbox',name='Title',exact=True).fill('Clip 01 captured')
    page.get_by_role('textbox',name='Note',exact=True).fill('Captured before await v4')
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).not_to_be_visible()
    all_notes=[a for a in page.request.get(ui_server+f'/api/recordings/{ID}/metadata').json()['annotations'] if a['record_class']=='entity_metadata' and a['kind']=='note' and a['status']=='active']
    clip1_notes=[a for a in all_notes if a['target'].get('clip_id')==CLIP]
    clip2_notes=[a for a in all_notes if a['target'].get('clip_id')=='cccccccccccccccccccccccc']
    assert len(clip1_notes)==1 and clip1_notes[0]['id']==note_id and clip1_notes[0]['body']['text']=='Captured before await v4' and clip1_notes[0]['version']==4
    assert len(clip2_notes)==0
    r=page.request.put(ui_server+f'/api/videos/{ID}/clips/{CLIP}/details',data=dict(title='Clip 01',publication=pub,version=page.request.get(ui_server+f'/api/sessions/{ID}').json()['clips'][0]['details']['version']),headers=headers);assert r.ok
    # 5. Failed initial metadata GET blocks note creation instead of duplicating an unknown existing note.
    fail_meta={'active':True}
    post_calls=[]
    page.on('request',lambda req:post_calls.append(req.url) if req.method=='POST' and '/api/metadata' in req.url else None)
    def handle_meta(route):
        if fail_meta['active']:
            route.fulfill(status=503,json={'error':'Metadata store offline'})
        else:
            route.continue_()
    page.route(f'**/api/recordings/{ID}/metadata*',handle_meta)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.locator('#entity-notes-list')).to_contain_text('Notes unavailable')
    page.get_by_role('textbox',name='Note',exact=True).fill('Draft while metadata offline')
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).to_be_visible()
    expect(page.locator('#edit-error')).to_contain_text('Notes unavailable')
    expect(page.locator('#edit-note')).to_have_value('Draft while metadata offline')
    assert not post_calls
    # Once metadata recovers, saveEditNote sees the existing server note was unknown when typed and prompts to load it rather than duplicating.
    fail_meta['active']=False
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).to_be_visible()
    expect(page.locator('#edit-error')).to_contain_text('Existing notes loaded')
    expect(page.locator('#edit-load-latest-note')).to_be_visible()
    assert not post_calls
    page.locator('#edit-load-latest-note').click()
    expect(page.locator('#edit-note')).to_have_value('Captured before await v4')
    page.get_by_role('textbox',name='Note',exact=True).fill('Recovered clip note v5')
    page.get_by_role('button',name='Save details',exact=True).click()
    expect(page.locator('#edit-dialog')).not_to_be_visible()
    assert not post_calls
    final_clip1=[a for a in page.request.get(ui_server+f'/api/recordings/{ID}/metadata').json()['annotations'] if a['record_class']=='entity_metadata' and a['kind']=='note' and a['status']=='active' and a['target'].get('clip_id')==CLIP]
    assert len(final_clip1)==1 and final_clip1[0]['id']==note_id and final_clip1[0]['body']['text']=='Recovered clip note v5' and final_clip1[0]['version']==5
    page.unroute(f'**/api/recordings/{ID}/metadata*',handle_meta)
    _archive_test_metadata(page,ui_server)
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
    assert page.locator('#moment-cues').evaluate("e=>e.parentElement.id==='notes-panel'")
    empty_panel=page.locator('#notes-panel').bounding_box()
    bar_box=page.locator('.moment-bar').bounding_box()
    assert empty_panel['height']<=bar_box['height']+16
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.25;}')
    expect(page.locator('[data-active-moment="point"]')).to_have_count(0)
    expect(page.locator('[data-active-moment="range"]')).to_be_visible()
    with_teaser=page.locator('#notes-panel').bounding_box()
    cue_box=page.locator('[data-active-moment="range"]').bounding_box()
    assert with_teaser['height']>empty_panel['height']+20
    assert cue_box['y']>=with_teaser['y'] and cue_box['y']+cue_box['height']<=with_teaser['y']+with_teaser['height']+1
    expect(page.locator('[data-active-moment="point"]')).to_have_count(0,timeout=2500)
    # A point expiry does not repeatedly re-trigger at a paused playhead.
    expect(page.locator('[data-active-moment="range"]')).to_be_visible()
    page.locator('#player').evaluate('v=>{v.currentTime=.5;}')
    expect(page.locator('[data-active-moment="coach-point"]')).to_be_visible()
    with_two_teasers=page.locator('#notes-panel').bounding_box()
    assert with_two_teasers['height']>with_teaser['height']+20
    expect(page.locator('[data-active-moment="coach-point"]')).to_have_count(0,timeout=6500)
    expect(page.locator('[data-active-moment="range"]')).to_be_visible()
    page.locator('#player').evaluate('v=>{v.currentTime=1.75;}')
    expect(page.locator('[data-active-moment="range"]')).to_have_count(0)
    page.locator('#player').evaluate('v=>{v.currentTime=.05;}')
    expect(page.locator('#moment-cues .moment-cue')).to_have_count(0)
    shrunk_panel=page.locator('#notes-panel').bounding_box()
    assert abs(shrunk_panel['height']-empty_panel['height'])<=2
    page.locator('#player').evaluate('v=>{v.currentTime=.25;}')
    expect(page.locator('[data-active-moment="point"]')).to_have_count(0)
    expect(page.locator('[data-active-moment="range"]')).to_be_visible()
    expect(page.locator('#player')).to_have_js_property('paused',True)
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','false')
    # Clicking an action button in the bar does not expand the panel.
    page.get_by_role('button',name='Jump to next moment',exact=True).click()
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','false')
    # Clicking the teaser inside the Moments panel expands the panel.
    page.locator('[data-active-moment="range"]').click()
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','true')
    expect(page.locator('#moment-panel-content')).to_be_visible()
    page.locator('#moment-toggle').click()
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','false')
    # Clicking the collapsed panel surface (outside action buttons) also expands it.
    page.locator('#notes-panel').click(position={'x':4,'y':4})
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','true')
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
    assert page.locator('.moment-bar > button').evaluate_all('(els)=>els.map(e=>e.id)')==['moment-toggle','moment-jump','note-highlight','add-moment']
    jump_box=page.locator('#moment-jump').bounding_box();highlight_box=page.locator('#note-highlight').bounding_box()
    assert jump_box['x']+jump_box['width']<=highlight_box['x']+1
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
    a=_playback_moment('coach-response:job-review-link','coach',.2,1.5)
    a['origin']=dict(type='ai',reference='job-review-link',job_id='job-review-link')
    a['body']=dict(response=True,text=json.dumps(dict(schema_version=2,summary='Recover after each shot.',suggestions=[dict(title='Ready position',cue='Bend knees.',drill='Reset ten times.')])) )
    a['playback_eligible']=False
    _mock_playback_moments(page,ui_server,[a])
    source=page.request.get(ui_server+'/api/sessions/'+ID).json()
    job=dict(id='job-review-link',version=1,role='ai',state='completed',asked_at=1700000100.0,updated=1700000200.0,body=dict(source_id=ID,clip_id=CLIP,publication=source['published_at'],export_id='render-test',original_start=.2,original_end=1.5,start=.2,end=1.5,prompt='Check recovery',evidence_frames=[]),result=dict(answer='Recover after each shot.',structured=dict(summary='Recover after each shot.',validation_warnings=[],parse_status='valid')))
    page.route('**/api/coaching/sessions/*',lambda route:route.fulfill(json=dict(jobs=[job],annotations=[],workers=[],enabled=True)))
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
    page.get_by_role('button',name='Review coaching',exact=True).click()
    expect(page.locator('#clip-study')).to_be_visible()
    expect(page.locator('#clip-study')).to_have_attribute('data-mobile-mode','coach')
    expect(page.locator('.coach-note[data-job="job-review-link"]')).to_have_class(re.compile(r'\bis-highlighted\b'))
    assert float(page.locator('#study-pin-start').get_attribute('aria-valuenow'))==pytest.approx(0.2,abs=0.05)
    assert float(page.locator('#study-pin-end').get_attribute('aria-valuenow'))==pytest.approx(1.5,abs=0.05)
    page.close()


@pytest.mark.parametrize('width',[390,1280])
def test_add_moment_optional_note_end_and_captured_scope(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900})
    _archive_test_metadata(page,ui_server)
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','false')
    page.evaluate('()=>{model.session.clips[0].original_start_seconds=.0004;}')
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=0;}')
    page.get_by_role('button',name='Add moment',exact=True).click()
    expect(page.get_by_role('dialog',name='Add moment',exact=True)).to_be_visible()
    expect(page.get_by_label('Original start (seconds)',exact=True)).to_have_value('0.001')
    page.get_by_label('Original start (seconds)',exact=True).fill('0')
    page.get_by_role('button',name='Save moment',exact=True).click()
    expect(page.locator('#note-editor-error')).to_contain_text('overlap the clip')
    page.get_by_label('Original start (seconds)',exact=True).fill('0.001')
    page.get_by_role('button',name='Close note editor',exact=True).click()
    page.evaluate('()=>{model.session.clips[0].original_start_seconds=0;}')
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.516842;}')
    page.get_by_role('button',name='Add moment',exact=True).click()
    expect(page.get_by_role('dialog',name='Add moment',exact=True)).to_be_visible()
    expect(page.get_by_label('Original start (seconds)',exact=True)).to_have_value('0.517')
    expect(page.get_by_label('Original end (seconds)',exact=True)).not_to_be_visible()
    point_note=f'Point moment note {width} {browser.browser_type.name}'
    page.get_by_role('textbox',name='Note',exact=True).fill(point_note)
    page.get_by_role('button',name='Save moment',exact=True).click()
    expect(page.locator('#note-editor')).not_to_be_visible()
    expect(page.locator('#moment-toggle')).to_have_attribute('aria-expanded','true')
    point_row=page.locator('#notes-list .note-item').filter(has_text=point_note)
    expect(point_row).to_be_visible()
    point_row.get_by_label('Moment actions',exact=True).click()
    point_row.get_by_role('button',name='Archive',exact=True).click()
    expect(point_row).to_have_count(0)
    page.locator('#moment-toggle').click()
    page.locator('#player').evaluate('v=>{v.pause();v.currentTime=.5;}')
    page.get_by_role('button',name='Add moment',exact=True).click()
    expect(page.get_by_role('dialog',name='Add moment',exact=True)).to_be_visible()
    expect(page.get_by_label('Original start (seconds)',exact=True)).to_have_value('0.5')
    expect(page.get_by_label('Original end (seconds)',exact=True)).not_to_be_visible()
    page.get_by_label('Original start (seconds)',exact=True).fill('')
    page.get_by_role('button',name='Save moment',exact=True).click()
    expect(page.locator('#note-editor-error')).to_contain_text('Choose a start within the recording')
    page.get_by_label('Original start (seconds)',exact=True).fill('0.5')
    page.get_by_role('checkbox',name='End time',exact=True).check()
    expect(page.get_by_label('Original end (seconds)',exact=True)).to_have_value('1.5')
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
    # Untimed notes live in Edit clip / Edit video, outside Moments and the 3-dot menu.
    page.get_by_role('button',name='Edit clip',exact=True).click()
    expect(page.get_by_role('dialog',name='Clip details',exact=True)).to_be_visible()
    page.get_by_role('button',name='Add note',exact=True).click()
    page.locator('#note-editor').get_by_role('textbox',name='Note',exact=True).fill('Second clip metadata')
    page.get_by_role('button',name='Save note',exact=True).click()
    expect(page.locator('#entity-notes-list')).to_contain_text('Second clip metadata')
    expect(page.locator('#edit-note')).to_have_value('Second clip metadata')
    expect(clips.nth(1).locator('.note-moment')).to_have_count(0)
    expect(page.locator('#entity-notes-list').get_by_role('button',name='Jump to moment',exact=True)).to_have_count(0)
    note=page.locator('#entity-notes-list .note-item').filter(has_text='Second clip metadata')
    note.get_by_label('Note actions',exact=True).click()
    note.get_by_role('button',name='Edit',exact=True).click()
    page.locator('#note-editor').get_by_role('textbox',name='Note',exact=True).fill('Second clip metadata revised')
    page.get_by_role('button',name='Save note',exact=True).click()
    expect(page.locator('#entity-notes-list')).to_contain_text('Second clip metadata revised')
    expect(page.locator('#edit-note')).to_have_value('Second clip metadata revised')
    page.get_by_role('button',name='Close',exact=True).click()
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


@pytest.mark.parametrize('width',[390,1280])
def test_recommended_filter_and_smart_clip_sort(browser,ui_server,width):
    page=browser.new_page(viewport={'width':width,'height':900},has_touch=width<650)
    base=page.request.get(ui_server+f'/api/sessions/{ID}').json()
    custom=copy.deepcopy(base)
    # 6 synthetic clips covering liked, active in-scope moments, archived/out-of-range
    # moments, disliked clips, <10s vs >=10s durations, and equal-duration tie-breaking.
    specs=[
        ('c1',1,6,0,6,False),     # <10s, no moment -> excluded from Recommended
        ('c2',2,8,6,14,False),    # <10s, active moment -> included in Recommended; tier 2 in Smart
        ('c3',3,12,14,26,False),  # >=10s, no moment -> included in Recommended; tier 3 in Smart (12s, index 3)
        ('c4',4,12,26,38,False),  # >=10s, archived/out-of-range only -> tier 3 tie-break after c3 (12s, index 4)
        ('c5',5,5,38,43,True),    # <10s, liked -> tier 1 in Smart, excluded from Recommended (no moment, <10s)
        ('c6',6,15,43,58,False),  # >=10s, disliked + active moment -> excluded from Recommended; tier 2 (15s) in Smart
    ]
    custom['clips']=[
        dict(base['clips'][0],id=cid.ljust(24,'0'),index=idx,title=f'Clip {idx:02d}',
             duration_seconds=dur,original_start_seconds=ostart,original_end_seconds=oend,
             playback_start_seconds=ostart,playback_end_seconds=oend,
             like=dict(value=liked,version=1))
        for cid,idx,dur,ostart,oend,liked in specs
    ]
    c1,c2,c3,c4,c5,c6=[c['id'] for c in custom['clips']]
    active_c2=_playback_moment('m-c2','note',7,clip=c2,clip_start=6)
    active_c6=_playback_moment('m-c6','highlight',45,clip=c6,clip_start=43)
    archived_c4=_playback_moment('m-c4-arch','note',27,clip=c4,clip_start=26)
    archived_c4.update(status='archived',playback_eligible=False)
    out_of_range_c4=_playback_moment('m-c4-out','note',99,clip=c4,clip_start=26)
    disliked_c6=dict(id='down-c6',kind='usefulness',record_class='entity_metadata',status='active',version=1,
                     playback_eligible=True,matched_clip_ids=[c6],related_clip_ids=[c6],
                     target=dict(scope='clip',clip_id=c6,start_seconds=43,end_seconds=58),
                     body=dict(value='less_useful',text='',reason=''),context=dict(publication=custom['published_at']))
    page.route(f'**/api/sessions/{ID}',lambda route:route.fulfill(json=custom))
    _mock_playback_moments(page,ui_server,[active_c2,active_c6,archived_c4,out_of_range_c4,disliked_c6])
    page.goto(ui_server+f'/review/{ID}')
    expect(page.locator('#clip-list .clip-row')).to_have_count(6)
    assert page.locator('#clip-sort option').evaluate_all('(els)=>els.map(e=>e.textContent)')==['Court order','Smart','Shortest first','Longest first']
    # Recommended excludes disliked (c6) OR (<10s and no active in-scope moment: c1, c5).
    # Keeps c2 (<10s with active moment), c3 (>=10s), and c4 (>=10s).
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('recommended')
    expect(page.locator('#clip-list .clip-row')).to_have_count(3)
    assert page.locator('#clip-list .clip-row').evaluate_all('(els)=>els.map(e=>e.dataset.clip)')==[c2,c3,c4]
    # Smart sort on Recommended: c2 (has active moment, 8s) > c3 (no active moment, 12s, index 3) > c4 (archived/out-of-range only, 12s, index 4).
    page.get_by_role('combobox',name='Sort clips',exact=True).select_option('smart')
    assert page.locator('#clip-list .clip-row').evaluate_all('(els)=>els.map(e=>e.dataset.clip)')==[c2,c3,c4]
    # Smart sort on All clips:
    # 1. Liked first: c5 (liked, 5s)
    # 2. Active in-scope Moments by duration desc: c6 (active moment, 15s), c2 (active moment, 8s)
    # 3. Remaining by duration desc, then index asc: c3 (12s, idx 3), c4 (12s, idx 4), c1 (6s, idx 1)
    page.get_by_role('combobox',name='Clip focus',exact=True).select_option('all')
    assert page.locator('#clip-list .clip-row').evaluate_all('(els)=>els.map(e=>e.dataset.clip)')==[c5,c6,c2,c3,c4,c1]
    assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
    page.close()


def test_desktop_autofocus_tracking_loss_and_cleanup(browser, ui_server):
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    requests = []
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.on('request', lambda req: requests.append((req.method, req.url)))
    # Deterministic detections exercise framing/lifecycle independently of model
    # confidence or platform-specific rasterization of illustrated people.
    page.add_init_script("""(() => {
      window.__cspViolations = [];
      document.addEventListener('securitypolicyviolation', e => {
        window.__cspViolations.push({directive: e.violatedDirective, blocked: e.blockedURI, sample: e.sample});
      });
      window.__testCourtScene = 'two_players';
      window.Worker = class {
        constructor() { this.closed = false; }
        postMessage(data) {
          if (data.type === 'init') {
            setTimeout(() => {if (!this.closed) this.onmessage?.({data:{type:'ready'}});}, 0);
          } else if (data.type === 'detect') {
            const w=data.width,h=data.height;
            const person=(x,y,width,height)=>({boundingBox:{originX:x*w,originY:y*h,width:width*w,height:height*h},categories:[{categoryName:'person',score:.95}]});
            const detections=window.__testCourtScene==='two_players'?[person(.25,.3,.08,.2),person(.6,.3,.16,.4)]:window.__testCourtScene==='close_player'?[person(.35,.1,.3,.72)]:[];
            setTimeout(() => {if (!this.closed) this.onmessage?.({data:{type:'result',epoch:data.epoch,seq:data.seq,detections}});}, 0);
          }
        }
        terminate() { this.closed = true; }
      };
    })()""")
    page.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    page.wait_for_function("() => document.querySelector('#player')?.readyState >= 2")
    af_btn = page.get_by_role('button', name='Auto focus main player', exact=True)
    expect(af_btn).to_be_visible()
    expect(af_btn).to_have_attribute('aria-pressed', 'false')
    expect(af_btn).to_have_attribute('data-autofocus-state', 'off')
    assert page.locator('#player').evaluate('v => v.style.transform') == ''

    # Enable Dev mode from the 3-dot menu before Auto focus: shows video stats and truthful 'not measured' verdicts without starting inference.
    expect(page.locator('#video-dev-layer')).to_be_hidden()
    page.get_by_label('More video actions', exact=True).click()
    dev_btn = page.get_by_role('button', name='Dev mode', exact=True)
    expect(dev_btn).to_have_attribute('aria-pressed', 'false')
    dev_btn.click()
    expect(page.locator('#player-dev-mode')).to_have_attribute('aria-pressed', 'true')
    expect(page.locator('#video-dev-layer')).to_be_visible()
    expect(page.locator('#video-dev-stats')).to_contain_text('Video:')
    expect(page.locator('#video-dev-stats')).to_contain_text('Auto focus: off')
    expect(page.locator('#video-dev-stats')).to_contain_text('play not measured · swing not measured')
    expect(page.locator('#video-dev-boxes .video-dev-box')).to_have_count(0)
    assert int(page.locator('#player').get_attribute('data-autofocus-captures') or '0') == 0

    # Enable Auto focus while paused: exactly one forced capture runs on ready, and paused interval ticks do not re-capture.
    af_btn.click()
    expect(af_btn).to_have_attribute('aria-pressed', 'true')
    page.wait_for_function(
        "() => document.querySelector('#video-autofocus')?.dataset.autofocusState === 'tracking' && Number(document.querySelector('#player')?.dataset.autofocusZoom || 1) >= 1.28",
        timeout=15000,
    )
    captures_while_paused = int(page.locator('#player').get_attribute('data-autofocus-captures') or '0')
    assert captures_while_paused == 1
    page.wait_for_timeout(420)
    assert int(page.locator('#player').get_attribute('data-autofocus-captures') or '0') == captures_while_paused

    expect(page.locator('#video-dev-stats')).to_contain_text('Auto focus: tracking')
    expect(page.locator('#video-dev-stats')).to_contain_text('Detector: captures 1 · results 1 · persons 2 · score 95%')
    expect(page.locator('#video-dev-stats')).to_contain_text('Verdict: player tracked · steady · play not measured · swing not measured')
    expect(page.locator('#video-dev-boxes [data-dev-box="primary"]')).to_be_visible()
    expect(page.locator('#video-dev-boxes [data-dev-box="candidate"]')).to_be_visible()
    stage_box = page.locator('#video-stage').bounding_box()
    prim_box = page.locator('#video-dev-boxes [data-dev-box="primary"]').bounding_box()
    cand_box = page.locator('#video-dev-boxes [data-dev-box="candidate"]').bounding_box()
    assert prim_box['width'] > 80 and prim_box['height'] > 150
    assert cand_box['width'] > 40 and cand_box['height'] > 70
    assert prim_box['x'] > stage_box['x'] + 40 and prim_box['y'] > stage_box['y'] + 40
    assert prim_box['x'] + prim_box['width'] <= stage_box['x'] + stage_box['width'] + 1
    # Because the zoomed/panned video shifts the right-side player toward the center, the mapped dev box center is shifted left of unzoomed 0.68 * width.
    prim_center_x = (prim_box['x'] - stage_box['x']) + prim_box['width'] / 2
    assert prim_center_x < stage_box['width'] * 0.63 and prim_center_x > stage_box['width'] * 0.45
    assert page.evaluate('() => window.__cspViolations') == []

    transform = page.locator('#player').evaluate('v => v.style.transform')
    m = re.match(r'translate\(([-\d.]+)px,\s*([-\d.]+)px\)\s*scale\(([\d.]+)\)', transform)
    assert m is not None, f'Unexpected transform: {transform}'
    tx, ty, scale = float(m.group(1)), float(m.group(2)), float(m.group(3))
    assert scale > 1.15
    assert tx < -10, f'Expected negative X pan toward right-side near player, got tx={tx}'

    # Seeking immediately invalidates/resets tracking and transform, and seeked captures the new scene.
    page.evaluate("""() => {
      window.__testCourtScene = 'empty_court';
      const v = document.querySelector('#player');
      v.dispatchEvent(new Event('seeking'));
    }""")
    assert page.locator('#player').evaluate('v => v.style.transform') == ''
    expect(af_btn).to_have_attribute('data-autofocus-state', 'searching')
    page.evaluate("() => { document.querySelector('#player').dispatchEvent(new Event('seeked')); }")
    page.wait_for_function(
        "() => document.querySelector('#video-autofocus')?.dataset.autofocusState === 'searching' && Number(document.querySelector('#player')?.dataset.autofocusZoom || 1) === 1",
        timeout=10000,
    )
    assert page.locator('#player').evaluate('v => v.style.transform') == ''
    expect(page.locator('#video-dev-stats')).to_contain_text('player lost · searching')
    expect(page.locator('#video-dev-boxes .video-dev-box')).to_have_count(0)

    # Steady 1x close-player detection (no zoom animation after initial creation) still positions the box immediately via CSSOM under strict CSP.
    page.evaluate("""() => {
      window.__testCourtScene = 'close_player';
      document.querySelector('#player').dispatchEvent(new Event('seeked'));
    }""")
    page.wait_for_function(
        "() => document.querySelector('#video-autofocus')?.dataset.autofocusState === 'tracking' && document.querySelectorAll('#video-dev-boxes .video-dev-box').length === 1",
        timeout=10000,
    )
    assert page.locator('#player').evaluate('v => v.style.transform') == ''
    close_box = page.locator('#video-dev-boxes [data-dev-box="primary"]').bounding_box()
    assert close_box['width'] > 200 and close_box['height'] > 300
    assert close_box['x'] > stage_box['x'] + 200 and close_box['y'] > stage_box['y'] + 20
    assert page.evaluate('() => window.__cspViolations') == []

    # Toggling Dev mode off hides the overlay cleanly.
    page.get_by_label('More video actions', exact=True).click()
    dev_btn.click()
    expect(page.locator('#player-dev-mode')).to_have_attribute('aria-pressed', 'false')
    expect(page.locator('#video-dev-layer')).to_be_hidden()

    # Restore target on seeked, verify re-lock, then verify clip change resets and re-tracks cleanly.
    page.evaluate("""() => {
      window.__testCourtScene = 'two_players';
      const v = document.querySelector('#player');
      v.dispatchEvent(new Event('seeking'));
      v.dispatchEvent(new Event('seeked'));
    }""")
    page.wait_for_function(
        "() => document.querySelector('#video-autofocus')?.dataset.autofocusState === 'tracking' && Number(document.querySelector('#player')?.dataset.autofocusZoom || 1) > 1.15",
        timeout=10000,
    )
    page.get_by_role('button', name='Next clip', exact=True).click()
    expect(page.locator('#player')).to_have_attribute('src', re.compile('clip-002.mp4'))
    page.wait_for_function(
        "() => document.querySelector('#video-autofocus')?.dataset.autofocusState === 'tracking' && Number(document.querySelector('#player')?.dataset.autofocusZoom || 1) > 1.15",
        timeout=10000,
    )

    # Opening Clip Study suspends/disables Auto focus and restores neutral framing.
    page.get_by_role('button', name='Study clip', exact=True).click()
    expect(page.locator('#clip-study')).to_be_visible()
    expect(af_btn).to_have_attribute('aria-pressed', 'false')
    expect(af_btn).to_have_attribute('data-autofocus-state', 'off')
    assert page.locator('#player').evaluate('v => v.style.transform') == ''
    page.get_by_role('button', name='← Back to review', exact=True).click()
    expect(page.locator('#clip-study')).not_to_be_visible()

    # Re-enable and manually disable Auto focus; verify immediate transform cleanup.
    af_btn.click()
    page.wait_for_function(
        "() => Number(document.querySelector('#player')?.dataset.autofocusZoom || 1) > 1.15",
        timeout=10000,
    )
    af_btn.click()
    expect(af_btn).to_have_attribute('aria-pressed', 'false')
    expect(af_btn).to_have_attribute('data-autofocus-state', 'off')
    assert page.locator('#player').evaluate('v => v.style.transform') == ''

    # Re-enable on desktop, then resize to mobile layout: Auto focus must automatically disable and clean up.
    af_btn.click()
    page.wait_for_function(
        "() => Number(document.querySelector('#player')?.dataset.autofocusZoom || 1) > 1.15",
        timeout=10000,
    )
    page.set_viewport_size({'width': 390, 'height': 844})
    expect(page.locator('#video-autofocus')).not_to_be_visible()
    expect(page.locator('#video-autofocus')).to_have_attribute('aria-pressed', 'false')
    expect(page.locator('#video-autofocus')).to_have_attribute('data-autofocus-state', 'off')
    assert page.locator('#player').evaluate('v => v.style.transform') == ''

    # Verify zero third-party network requests and zero media/frame uploads occurred.
    assert all(url.startswith((ui_server, 'data:', 'blob:')) for _, url in requests), requests
    assert all(method == 'GET' for method, _ in requests)
    assert not errors
    page.close()




def test_autofocus_failure_state_and_mobile_hidden(browser, ui_server):
    mobile = browser.new_page(viewport={'width': 390, 'height': 844}, has_touch=True)
    mobile.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    expect(mobile.locator('#video-seek')).to_be_enabled()
    expect(mobile.locator('#video-autofocus')).not_to_be_visible()
    mobile.close()

    # Unsupported browser capabilities permanently disable the control.
    unsupported = browser.new_page(viewport={'width': 1280, 'height': 900})
    unsupported.add_init_script("window.__TENNIS_AUTOFOCUS_FORCE_UNSUPPORTED = true;")
    unsupported.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    expect(unsupported.locator('#video-seek')).to_be_enabled()
    af_unsupported = unsupported.get_by_role('button', name='Auto focus main player', exact=True)
    expect(af_unsupported).to_have_attribute('data-autofocus-state', 'unavailable')
    expect(af_unsupported).to_be_disabled()
    unsupported.close()

    graphics = browser.new_page(viewport={'width': 1280, 'height': 900})
    graphics.add_init_script("""window.Worker=class {
      postMessage(data) { if(data.type==='init') setTimeout(()=>this.onmessage?.({data:{type:'unsupported',code:'worker_graphics_unavailable'}}),0); }
      terminate() {}
    };""")
    graphics.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    expect(graphics.locator('#video-seek')).to_be_enabled()
    graphics.locator('#video-autofocus').click()
    expect(graphics.locator('#video-autofocus')).to_be_disabled()
    expect(graphics.locator('#video-autofocus')).to_have_attribute('aria-pressed', 'false')
    graphics.locator('#video-stage').hover()
    graphics.get_by_role('button', name='Play video', exact=True).click()
    graphics.wait_for_function("() => !document.querySelector('#player').paused")
    assert graphics.locator('#player').evaluate('v => v.style.transform') == ''
    graphics.close()

    # Deterministic lifecycle test for delayed/stale worker responses, single-flight queue safety across seeking, and watchdog recovery.
    desktop = browser.new_page(viewport={'width': 1280, 'height': 900})
    desktop.add_init_script("""(() => {
      window.__afWorkers = [];
      window.__afMode = 'delayed';
      window.__TENNIS_AUTOFOCUS_CONFIG = {initTimeoutMs: 400, detectTimeoutMs: 350};
      class ControlledWorker {
        constructor(url) {
          this.url = url;
          this.terminated = false;
          this.sent = [];
          window.__afWorkers.push(this);
        }
        postMessage(msg) {
          if (this.terminated) return;
          this.sent.push({type: msg.type, epoch: msg.epoch, seq: msg.seq});
          if (msg.type === 'init') {
            if (window.__afMode === 'hang_init') return;
            setTimeout(() => {
              if (!this.terminated) this.onmessage?.({data: {type: 'ready'}});
            }, 10);
          } else if (msg.type === 'detect') {
            if (window.__afMode === 'hang_detect' || window.__afMode === 'delayed') return;
            setTimeout(() => {
              if (!this.terminated) {
                this.onmessage?.({
                  data: {
                    type: 'result',
                    epoch: msg.epoch,
                    seq: msg.seq,
                    detections: [{
                      boundingBox: {originX: 190, originY: 75, width: 42, height: 68},
                      categories: [{score: 0.85, categoryName: 'person'}],
                    }],
                  },
                });
              }
            }, 15);
          }
        }
        terminate() {
          this.terminated = true;
        }
      }
      window.Worker = ControlledWorker;
    })()""")
    desktop.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    desktop.wait_for_function("() => document.querySelector('#player')?.readyState >= 2")
    af_btn = desktop.get_by_role('button', name='Auto focus main player', exact=True)
    af_btn.click()
    desktop.wait_for_function("() => window.__afWorkers.length === 1 && window.__afWorkers[0].sent.some(m => m.type === 'detect')")

    # While seq=1 is in flight, firing seeking + seeked must NOT queue a second overlapping detect message.
    desktop.evaluate("""() => {
      const v = document.querySelector('#player');
      v.dispatchEvent(new Event('seeking'));
      v.dispatchEvent(new Event('seeked'));
    }""")
    assert desktop.evaluate("() => window.__afWorkers[0].sent.filter(m => m.type === 'detect').length") == 1

    # Replying with the stale epoch=1 result releases the in-flight lock without applying stale detections, and immediately dispatches the pending forced capture for epoch=2.
    desktop.evaluate("""() => {
      const w = window.__afWorkers[0];
      const first = w.sent.find(m => m.type === 'detect');
      w.onmessage({
        data: {
          type: 'result',
          epoch: first.epoch,
          seq: first.seq,
          detections: [{
            boundingBox: {originX: 190, originY: 75, width: 42, height: 68},
            categories: [{score: 0.9, categoryName: 'person'}],
          }],
        },
      });
    }""")
    assert desktop.locator('#player').evaluate('v => v.style.transform') == ''
    assert desktop.evaluate("() => window.__afWorkers[0].sent.filter(m => m.type === 'detect').length") == 2

    # Leaving the second detect request unanswered triggers the inference watchdog, terminating the hung worker and surfacing a visible recoverable failure.
    expect(af_btn).to_have_attribute('data-autofocus-state', 'unavailable', timeout=3000)
    expect(af_btn).to_have_attribute('aria-pressed', 'false')
    expect(af_btn).to_be_enabled()
    expect(desktop.locator('#toast')).to_contain_text('Auto focus is unavailable in this browser.')
    assert desktop.evaluate("() => window.__afWorkers[0].terminated") is True

    # Recoverable failure: switching worker to normal mode and clicking Auto focus again succeeds cleanly.
    desktop.evaluate("() => { window.__afMode = 'ok'; }")
    af_btn.click()
    desktop.wait_for_function(
        "() => document.querySelector('#video-autofocus')?.dataset.autofocusState === 'tracking' && Number(document.querySelector('#player')?.dataset.autofocusZoom || 1) > 1.15",
        timeout=5000,
    )
    desktop.evaluate("() => ReviewPlayer.controller.destroy()")
    assert desktop.evaluate("() => window.__afWorkers.at(-1).terminated") is True
    assert desktop.locator('#player').evaluate('v => v.style.transform') == ''
    desktop.close()


def test_home_redesign_views_counts_deduplication_and_status_dot(browser, ui_server):
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    errors = []
    page.on('pageerror', lambda err: errors.append(str(err)))
    _archive_test_metadata(page, ui_server)
    headers = {'X-Tennis-Request': '1'}

    # Mock /api/status dynamically to verify green, yellow, and red dot states on top Processing nav.
    status_state = {'mode': 'green'}
    def handle_status(route):
        mode = status_state['mode']
        if mode == 'http_error':
            route.fulfill(status=500, json={'error': 'Status backend error'})
            return
        payload = {
            'available': mode != 'unavailable',
            'stale': mode == 'stale',
            'has_error': mode == 'red_error',
            'service': 'Stopped' if mode == 'paused' else 'Running',
            'stage': 'Idle',
            'heartbeat': 1700000000,
            'current_video': None,
            'progress': None,
            'files': [{'name': 'test.mov', 'status': 'needs_attention', 'error': 'failed'}] if mode == 'file_error' else [],
        }
        route.fulfill(json=payload)

    page.route('**/api/status', handle_status)

    # Ensure at least one player exists so avatar circles render on the Players summary item.
    players = page.request.get(ui_server + '/api/players').json()['players']
    if not players:
        r = page.request.post(ui_server + '/api/players', headers=headers, data={'name': 'Home Player', 'height_cm': 175, 'level': 'intermediate'})
        assert r.ok
        players = page.request.get(ui_server + '/api/players').json()['players']

    page.goto(ui_server + '/')
    # 1. Top nav order and Processing status dot states (green / yellow / red), no worker status card in home overview.
    assert page.locator('.site-header nav a').evaluate_all("els => els.map(e => e.id)") == ['nav-library', 'nav-learning', 'nav-processing']
    assert [t.strip() for t in page.locator('.site-header nav a').all_inner_texts()] == ['Library', 'Learning', 'Processing']
    expect(page.locator('#nav-processing')).to_have_attribute('href', '/processing')
    expect(page.locator('.library-overview #processing-status')).to_have_count(0)
    expect(page.locator('.library-overview .worker-status')).to_have_count(0)

    dot = page.locator('#nav-processing-dot')
    expect(dot).to_have_attribute('data-status', 'green')
    expect(dot).to_have_class(re.compile(r'\bstatus-green\b'))

    for mode in ('paused', 'stale', 'unavailable'):
        status_state['mode'] = mode
        page.evaluate('() => refreshStatus()')
        expect(dot).to_have_attribute('data-status', 'yellow')
        expect(dot).to_have_class(re.compile(r'\bstatus-yellow\b'))

    for mode in ('red_error', 'file_error', 'http_error'):
        status_state['mode'] = mode
        page.evaluate('() => refreshStatus()')
        expect(dot).to_have_attribute('data-status', 'red')
        expect(dot).to_have_class(re.compile(r'\bstatus-red\b'))

    status_state['mode'] = 'green'
    page.evaluate('() => refreshStatus()')
    expect(dot).to_have_attribute('data-status', 'green')

    # 2. Summary row items, counts, and entity dialogs (no "Saved players" wording on home button).
    expect(page.locator('#stat-sessions')).to_have_text('1')
    expect(page.locator('#stat-clips')).to_have_text('3')
    expect(page.locator('#stat-minutes')).to_have_text('0m')
    expect(page.locator('#stat-videos')).to_have_text('1 video')
    expect(page.locator('#stat-likes')).to_have_text('2')
    expect(page.locator('#stat-players')).to_have_text(str(len(players)))
    expect(page.locator('#manage-players .player-avatar-stack .player-avatar').first).to_be_visible()
    assert 'saved players' not in page.locator('#manage-players').inner_text().lower()
    expect(page.locator('#stat-courts')).to_have_text('1')
    expect(page.locator('#label-courts')).to_have_text('Court')

    page.locator('#manage-players').click()
    expect(page.locator('#players-dialog')).to_be_visible()
    page.get_by_role('button', name='Close saved players', exact=True).click()
    expect(page.locator('#players-dialog')).not_to_be_visible()

    page.locator('#manage-courts').click()
    expect(page.locator('#courts-dialog')).to_be_visible()
    page.get_by_role('button', name='Close saved courts', exact=True).click()
    expect(page.locator('#courts-dialog')).not_to_be_visible()

    # 3. Default Sessions view, then in-place switching to Clips, Practice, and Likes & highlights + URL/history.
    expect(page.locator('#summary-sessions')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#home-section-title')).to_have_text('Sessions')
    expect(page.locator('#result-count')).to_have_text('1 session')
    expect(page.locator('#session-grid .practice-card')).to_have_count(1)

    page.locator('#summary-clips').click()
    expect(page).to_have_url(ui_server + '/#clips')
    expect(page.locator('#summary-clips')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#home-section-title')).to_have_text('Clips')
    expect(page.locator('#result-count')).to_have_text('3 clips')
    expect(page.locator('#session-grid .session-card[data-card-kind="clip"]')).to_have_count(3)
    expect(page.locator('#session-grid .session-card .card-visual img').nth(0)).to_have_attribute('src', re.compile(f'/clips/{CLIP}/poster'))

    # Sort clips in Clips view (latest/oldest/longest/shortest) and search filter + empty state.
    page.locator('#search').fill('nonexistent-query-xyz')
    expect(page.locator('#result-count')).to_have_text('0 clips')
    expect(page.locator('#session-grid .empty h3')).to_have_text('No matches')
    page.locator('#search').fill('')
    expect(page.locator('#result-count')).to_have_text('3 clips')

    page.locator('#summary-practice').click()
    expect(page).to_have_url(ui_server + '/#practice')
    expect(page.locator('#summary-practice')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#home-section-title')).to_have_text('Practice')
    expect(page.locator('#result-count')).to_have_text('1 video')
    expect(page.locator('#session-grid .session-card[data-card-kind="video"]')).to_have_count(1)
    expect(page.locator('#session-grid .session-card .card-visual img').first).to_have_attribute('src', re.compile(f'/api/sessions/{ID}/poster'))

    # 4. Likes & highlights view: deduplication of liked+highlighted clip, multiple highlights, clip vs video likes, and exclusion of archived/out-of-range highlights.
    page.locator('#summary-likes').click()
    expect(page).to_have_url(ui_server + '/#likes')
    expect(page.locator('#summary-likes')).to_have_attribute('aria-current', 'page')
    expect(page.locator('#home-section-title')).to_have_text('Likes & highlights')
    expect(page.locator('#result-count')).to_have_text('2 items')
    expect(page.locator('#session-grid .session-card')).to_have_count(2)

    pub = page.request.get(ui_server + f'/api/sessions/{ID}').json()['published_at']
    c2_id = 'c' * 24
    suffix = browser.browser_type.name
    # Highlighting Clip 1 (already liked) must NOT increase count or create a duplicate card; badge becomes LIKED · ★ HIGHLIGHT.
    h1_res = page.request.post(
        ui_server + '/api/annotations',
        headers=headers,
        data={'source_id': ID, 'publication': pub, 'kind': 'highlight', 'target': {'scope': 'point', 'start_seconds': 0.5}, 'body': {'text': 'Clip 1 highlight'}, 'request_id': f'hl-71-c1-{suffix}'},
    )
    assert h1_res.ok
    h1 = h1_res.json()
    # Highlighting Clip 2 (not liked) twice + a plain note on Clip 3:
    # Clip 2 must appear ONCE with clip thumbnail and ★ HIGHLIGHT badge; Clip 3 must not appear.
    h2a_res = page.request.post(
        ui_server + '/api/annotations',
        headers=headers,
        data={'source_id': ID, 'publication': pub, 'kind': 'highlight', 'target': {'scope': 'point', 'start_seconds': 2.5}, 'body': {'text': 'Clip 2 highlight A'}, 'request_id': f'hl-71-c2a-{suffix}'},
    )
    assert h2a_res.ok
    h2a = h2a_res.json()
    h2b_res = page.request.post(
        ui_server + '/api/annotations',
        headers=headers,
        data={'source_id': ID, 'publication': pub, 'kind': 'highlight', 'target': {'scope': 'interval', 'start_seconds': 2.8, 'end_seconds': 3.5}, 'body': {'text': 'Clip 2 highlight B'}, 'request_id': f'hl-71-c2b-{suffix}'},
    )
    assert h2b_res.ok
    h2b = h2b_res.json()
    note3_res = page.request.post(
        ui_server + '/api/annotations',
        headers=headers,
        data={'source_id': ID, 'publication': pub, 'kind': 'note', 'target': {'scope': 'point', 'start_seconds': 4.5}, 'body': {'text': 'Clip 3 plain note'}, 'request_id': f'hl-71-n3-{suffix}'},
    )
    assert note3_res.ok
    note3 = note3_res.json()

    page.evaluate('() => CourtUI.bindCards() || refreshShared()')
    expect(page.locator('#stat-likes')).to_have_text('3')
    expect(page.locator('#result-count')).to_have_text('3 items')
    expect(page.locator('#session-grid .session-card')).to_have_count(3)
    expect(page.locator(f'#session-grid .session-card[data-clip-id="{CLIP}"]')).to_have_attribute('data-card-reason', 'liked-highlight')
    expect(page.locator(f'#session-grid .session-card[data-clip-id="{CLIP}"] .card-badges')).to_contain_text('LIKED · ★ HIGHLIGHT')
    expect(page.locator(f'#session-grid .session-card[data-clip-id="{c2_id}"]')).to_have_attribute('data-card-reason', 'highlight')
    expect(page.locator(f'#session-grid .session-card[data-clip-id="{c2_id}"] .card-badges')).to_contain_text('★ HIGHLIGHT')
    expect(page.locator(f'#session-grid .session-card[data-clip-id="{c2_id}"] .card-visual img')).to_have_attribute('src', re.compile(f'/clips/{c2_id}/poster'))
    expect(page.locator('#session-grid .session-card[data-card-kind="video"]')).to_have_attribute('data-card-reason', 'liked')

    # Archiving Clip 2's highlights immediately removes Clip 2 from Likes & highlights and updates count back to 2.
    for h in (h1, h2a, h2b, note3):
        r = page.request.put(ui_server + f"/api/annotations/{h['id']}", headers=headers, data={'publication': pub, 'version': h['version'], 'status': 'archived'})
        assert r.ok
    page.evaluate('() => refreshShared()')
    expect(page.locator('#stat-likes')).to_have_text('2')
    expect(page.locator('#result-count')).to_have_text('2 items')
    expect(page.locator(f'#session-grid .session-card[data-clip-id="{c2_id}"]')).to_have_count(0)

    # Browser back/forward navigates cleanly between views.
    page.go_back()
    expect(page).to_have_url(ui_server + '/#practice')
    expect(page.locator('#summary-practice')).to_have_attribute('aria-current', 'page')
    page.go_back()
    expect(page).to_have_url(ui_server + '/#clips')
    expect(page.locator('#summary-clips')).to_have_attribute('aria-current', 'page')
    page.go_back()
    expect(page).to_have_url(ui_server + '/')
    expect(page.locator('#summary-sessions')).to_have_attribute('aria-current', 'page')

    # Verify responsive layout at 1280px, 390px, and 320px across views with zero horizontal overflow.
    for view_btn in ('#summary-sessions', '#summary-clips', '#summary-practice', '#summary-likes'):
        page.locator(view_btn).click()
        for width in (1280, 390, 320):
            page.set_viewport_size({'width': width, 'height': 844})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'Overflow at {width}px on {view_btn}'

    assert not errors
    page.close()


@pytest.mark.parametrize('delayed', ['create', 'poll'])
def test_share_download_ignores_old_selection_results(browser, ui_server, delayed):
    from playwright.sync_api import Error as PlaywrightError
    page = browser.new_page(accept_downloads=True)
    page.set_default_timeout(6000)
    exports, held = [], []
    page.add_init_script("""(() => {
      window.shareDownloads=[];
      const click=HTMLAnchorElement.prototype.click;
      HTMLAnchorElement.prototype.click=function(...args){
        if(this.id==='study-download-ready'){window.shareDownloads.push(this.href);return;}
        return click.apply(this,args);
      };
    })()""")

    def create(route):
        body = route.request.post_data_json
        exports.append(body)
        job = dict(id=f'share-{len(exports)}', role='render', state='queued', body=body, result={})
        if len(exports) == 1 and delayed == 'create':
            held.append((route, job))
        else:
            route.fulfill(status=201, json=job)

    def poll(route):
        identity = route.request.url.rsplit('/', 1)[-1]
        job = dict(id=identity, role='render', state='completed', result={})
        if identity == 'share-1':
            held.append((route, job))
        else:
            route.fulfill(json=job)

    page.route('**/api/coaching/exports', create)
    page.route('**/api/coaching/jobs/share-*', poll)
    page.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button', name='Study clip', exact=True).click()
    download = page.get_by_role('button', name='Download interval', exact=True)
    expect(download).to_be_enabled()
    expected = '/api/coaching/exports' if delayed == 'create' else '/api/coaching/jobs/share-1'
    with page.expect_request(lambda r: r.url.endswith(expected)):
        download.click()
    expect(download).to_be_disabled()
    page.locator('#study-pin-start').evaluate("el=>{el.value=.4;el.dispatchEvent(new Event('input',{bubbles:true}));}")
    page.locator('#study-speed').select_option('0.25')
    page.get_by_role('slider', name='Video zoom', exact=True).press('End')
    expect(download).to_be_enabled()
    download.click()
    expect(page.locator('#study-download-ready')).to_have_attribute('href', re.compile('/share-2/'))
    page.wait_for_function('window.shareDownloads.length===1')
    assert len(exports) == 2
    assert exports[0]['request_id'] != exports[1]['request_id']
    assert exports[1]['start'] == pytest.approx(.4) and exports[1]['speed'] == .25
    assert exports[1]['crop'] != exports[0]['crop']
    assert held
    for route, old_job in held:
        try:
            route.fulfill(status=201 if delayed == 'create' else 200, json=old_job)
        except PlaywrightError:
            pass  # The intentionally aborted request may no longer accept a response.
    page.wait_for_timeout(100)
    expect(page.locator('#study-download-ready')).to_have_attribute('href', re.compile('/share-2/'))
    downloads = page.evaluate('window.shareDownloads')
    assert len(downloads) == 1 and '/share-2/' in downloads[0]
    page.close()


def test_old_coach_failure_cannot_clear_reopened_clip_request(browser, ui_server):
    page = browser.new_page()
    page.set_default_timeout(6000)
    state = dict(enabled=True, jobs=[], annotations=[], workers=[])
    held, exported, asked = [], [], []
    page.route('**/api/coaching/sessions/*', lambda route: route.fulfill(json=state))

    def create(route):
        body = route.request.post_data_json
        exported.append(body)
        if len(exported) == 1:
            held.append(route)
            return
        start = 2 + body['start']
        job = dict(id='new-clip-render', version=1, role='render', state='queued',
                   body={**body, 'original_start': start, 'original_end': 2 + body['end']}, result={})
        state['jobs'].append(job)
        route.fulfill(status=201, json=job)

    def ask(route):
        body = route.request.post_data_json
        asked.append(body)
        job = dict(id='new-clip-answer', version=1, role='ai', state='queued',
                   body={**state['jobs'][0]['body'], **body}, result={})
        state['jobs'].append(job)
        route.fulfill(status=201, json=job)

    page.route('**/api/coaching/exports', create)
    page.route('**/api/coaching/requests', ask)
    page.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button', name='Study clip', exact=True).click()
    with page.expect_request(lambda r: r.url.endswith('/api/coaching/exports')):
        page.get_by_role('button', name='Ask coach', exact=True).click()
    page.get_by_role('button', name='← Back to review', exact=True).click()
    page.get_by_role('button', name=re.compile(r'^Play clip 2,')).click()
    page.get_by_role('button', name='Study clip', exact=True).click()
    page.get_by_role('button', name='Ask coach', exact=True).click()
    expect(page.locator('#coach-status')).to_contain_text('Preparing your clip')
    assert len(exported) == 2 and exported[1]['clip_id'] != CLIP
    assert held
    held[0].fulfill(status=500, json={'error': 'Old request failed'})
    page.wait_for_timeout(100)
    with page.expect_response(lambda r: r.url.endswith('/api/coaching/requests'), timeout=10000):
        state['jobs'][0].update(state='completed', version=2)
    assert len(asked) == 1 and asked[0]['export_id'] == 'new-clip-render'
    page.close()


def test_cut_review_timeline_versions_gaps_and_portal_handoff(browser, tmp_path):
    from app import create_app
    from entity_model import output_version
    app, root, data = setup.__wrapped__(tmp_path / 'cut-review-ui')
    fixtures = Path(__file__).parent / 'fixtures'
    shutil.copyfile(fixtures / 'practice.mp4', root / 'media' / ID / 'Practice.mp4')
    shutil.copyfile(fixtures / 'clip.mp4', root / 'media' / ID / 'Clips/clip-001.mp4')

    # Historical v1 has 1 clip [5, 15) on a 20s recording (gaps [0, 5) and [15, 20))
    v1_pub = data['published_at']
    v1_id = output_version(ID, v1_pub)

    # Publish v2 as current serving with 2 clips: [2, 4) and [8, 10) on a 12s source (gaps [0, 2), [4, 8), [10, 12))
    v2_data = copy.deepcopy(data)
    v2_data['published_at'] = v1_pub + 100
    v2_data['source']['duration_seconds'] = 12
    v2_data['video']['duration_seconds'] = 4
    v2_data['kept_seconds'] = 4
    v2_data['removed_seconds'] = 8
    clip2_id = 'c' * 24
    shutil.copyfile(fixtures / 'clip.mp4', root / 'media' / ID / 'Clips/clip-002.mp4')
    v2_data['clips'] = [
        dict(data['clips'][0], id=CLIP, index=1, file='Clips/clip-001.mp4', duration_seconds=2,
             original_start_seconds=2, original_end_seconds=4, playback_start_seconds=0, playback_end_seconds=2),
        dict(data['clips'][0], id=clip2_id, index=2, file='Clips/clip-002.mp4', duration_seconds=2,
             original_start_seconds=8, original_end_seconds=10, playback_start_seconds=2, playback_end_seconds=4),
    ]
    (root / 'metadata/sessions' / f'{ID}.json').write_text(json.dumps(v2_data))
    v2_id = output_version(ID, v2_data['published_at'])

    app = create_app({
        'TESTING': True,
        'LIBRARY_ROOT': str(root),
        'DATA_ROOT': app.config['DATA_ROOT'],
        'PROCESSING_ROOM_URL': 'http://processor.example:8089/',
    })
    server = make_server('127.0.0.1', 0, app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        errors = []
        page.on('pageerror', lambda err: errors.append(str(err)))

        # 1. Local /processing route preserves status, shows external portal handoff, and launches Review cut
        page.goto(base_url + '/processing')
        expect(page.get_by_role('heading', name='Processing Room', exact=True)).to_be_visible()
        expect(page.locator('#processing-portal-link')).to_have_attribute('href', 'http://processor.example:8089/')
        expect(page.locator('#processing-cut-reviews')).to_be_visible()
        page.locator('#processing-cut-reviews').get_by_role('link', name='Review cut', exact=True).click()

        # 2. Defaults to current serving version v2, renders kept clips and removed gaps on timeline
        expect(page.get_by_role('heading', name='Review cut', exact=True).first).to_be_visible()
        expect(page.locator('#cut-version')).to_have_value(v2_id)
        expect(page.locator('#cut-timeline-bar [data-seg-kind="kept_clip"]')).to_have_count(2)
        expect(page.locator('#cut-timeline-bar [data-seg-kind="removed_gap"]')).to_have_count(3)
        expect(page.locator('#cut-portal-handoff')).to_have_attribute('href', 'http://processor.example:8089/')

        # 3. Step playhead, capture suggested start from playhead, nudge end, and save clip cut review
        page.wait_for_function("() => document.querySelector('#cut-video')?.readyState >= 1")
        page.locator('#cut-step-forward').click()
        page.locator('#cut-set-start').click()
        expect(page.locator('#cut-suggested-start')).to_have_value('2.5')
        page.locator('#cut-nudge-end-plus').click()
        expect(page.locator('#cut-suggested-end')).to_have_value('4.5')
        page.locator('#cut-form-reasons').get_by_role('checkbox', name='End too early', exact=True).check()
        page.locator('#cut-comment').fill('Extend rally finish')
        page.locator('#cut-save').click()

        clip_review = page.locator('#cut-report-list .note-item').filter(has_text='Extend rally finish')
        expect(clip_review).to_be_visible()
        expect(clip_review).to_contain_text('Needs adjustment')
        expect(clip_review).to_contain_text('Suggested 0:02.5–0:04.5')

        # 4. Select middle removed gap [4, 8) on timeline, verify no gap video, file missed-play report, then resize it
        page.locator('#cut-timeline-bar [data-seg-kind="removed_gap"]').nth(1).click()
        expect(page.locator('#cut-gap-slate')).to_be_visible()
        expect(page.locator('#cut-clip-stage')).to_be_hidden()
        expect(page.locator('#cut-gap-summary')).to_contain_text('Original 0:04–0:08')
        page.locator('#cut-gap-start').fill('4.5')
        page.locator('#cut-gap-end').fill('7.5')
        page.locator('#cut-comment').fill('Missed point in middle gap')
        page.locator('#cut-save').click()

        gap_review = page.locator('#cut-report-list .note-item').filter(has_text='Missed point in middle gap')
        expect(gap_review).to_be_visible()
        expect(gap_review).to_contain_text('Removed gap · Original 0:04.5–0:07.5')
        gap_review.get_by_role('button', name='Edit', exact=True).click()
        expect(page.locator('#note-scope')).to_be_disabled()
        expect(page.locator('#note-start')).to_be_enabled()
        expect(page.locator('#note-end')).to_be_enabled()
        page.locator('#note-start').fill('5')
        page.locator('#note-end').fill('7')
        page.locator('#note-save').click()
        expect(gap_review).to_contain_text('Removed gap · Original 0:05–0:07')

        # 5. Switch to historical version v1: read-only by default until explicitly scoped
        page.locator('#cut-version').select_option(v1_id)
        expect(page.locator('#cut-version-banner')).to_be_visible()
        expect(page.locator('#cut-version-banner')).to_contain_text('read-only')
        expect(page.locator('#cut-save')).to_be_disabled()
        expect(page.locator('#cut-report-list .note-item')).to_have_count(0)
        page.locator('#cut-scope-historical').click()
        expect(page.locator('#cut-save')).to_be_enabled()
        page.locator('#cut-verdict').select_option('good_cut')
        page.locator('#cut-comment').fill('Scoped v1 review')
        page.locator('#cut-save').click()
        expect(page.locator('#cut-report-list .note-item').filter(has_text='Scoped v1 review')).to_be_visible()

        # All-version rows never borrow the selected version's editing scope or markers.
        page.locator('#cut-version').select_option(v2_id)
        expect(page.locator('#cut-report-list .note-item').filter(has_text='Extend rally finish')).to_be_visible()
        reviewed_count=page.locator('#cut-timeline-bar .reviewed').count()
        page.locator('#cut-all-versions').check()
        old_review=page.locator('#cut-report-list .note-item').filter(has_text='Scoped v1 review')
        expect(old_review.get_by_role('button',name='Open reviewed version',exact=True)).to_be_visible()
        expect(old_review.get_by_role('button',name='Edit',exact=True)).to_have_count(0)
        expect(old_review.get_by_role('button',name='Jump to moment',exact=True)).to_have_count(0)
        assert page.locator('#cut-timeline-bar .reviewed').count()==reviewed_count
        old_review.get_by_role('button',name='Open reviewed version',exact=True).click()
        expect(page.locator('#cut-version')).to_have_value(v1_id)
        expect(old_review.get_by_role('button',name='Edit',exact=True)).to_have_count(0)
        page.locator('#cut-scope-historical').click()
        old_review.get_by_role('button',name='Edit',exact=True).click()
        page.locator('#note-text').fill('Scoped v1 revised')
        page.locator('#note-suggested-end').fill('14')
        page.locator('#note-save').click()
        expect(page.locator('#cut-report-list')).to_contain_text('Scoped v1 revised')

        # 6. Verify /processing/cuts/<id> alias and responsive layout across mobile & desktop
        page.goto(base_url + f'/processing/cuts/{ID}')
        expect(page.get_by_role('heading', name='Review cut', exact=True).first).to_be_visible()
        for width, height in ((320, 568), (390, 844), (1280, 900)):
            page.set_viewport_size({'width': width, 'height': height})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'Horizontal overflow at {width}x{height}'
        assert not errors
        page.close()
    finally:
        server.shutdown()
        worker.join()



@pytest.fixture
def cut_review_scope_server(tmp_path):
    app,root,data=setup.__wrapped__(tmp_path)
    data['source']['duration_seconds']=30;data['published_at']+=1;data['video']['duration_seconds']=6;data['kept_seconds']=6;data['removed_seconds']=24
    data['clips']=[dict(id=letter*24,index=i+1,file=f'Clips/clip-{i+1:03}.mp4',duration_seconds=2,original_start_seconds=start,original_end_seconds=start+2,playback_start_seconds=i*2,playback_end_seconds=(i+1)*2) for i,(letter,start) in enumerate(zip('bcd',[5,10,20]))]
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    fixtures=Path(__file__).parent/'fixtures';shutil.copyfile(fixtures/'practice.mp4',root/'media'/ID/'Practice.mp4')
    for c in data['clips']:shutil.copyfile(fixtures/'clip.mp4',root/'media'/ID/c['file'])
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    yield 'http://127.0.0.1:'+str(server.server_port),data
    server.shutdown();thread.join()


def open_cut_scope_review(browser,server,width=390):
    base,data=server;page=browser.new_page(viewport={'width':width,'height':844});csp=[]
    page.on('console',lambda m:csp.append(m.text) if 'Content Security Policy' in m.text or 'Refused to' in m.text else None)
    page.goto(base+f'/review/{ID}/cuts?clip={CLIP}');expect(page.get_by_role('heading',name='Review cut',exact=True,level=1)).to_be_visible();expect(page.locator('#cut-timeline-bar button').first).to_be_visible()
    return page,csp

def test_timeline_kept_gap_contract_and_strict_csp(browser,cut_review_scope_server):
    page,csp=open_cut_scope_review(browser,cut_review_scope_server)
    expect(page.locator('[data-seg-kind="kept_clip"]')).to_have_count(3)
    expect(page.locator('[data-seg-kind="removed_gap"]')).to_have_count(4)
    assert 'undefined' not in page.locator('#cut-timeline-bar').inner_text()
    assert not csp
    for width in (320,390,1280):
        page.set_viewport_size({'width':width,'height':844})
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth'),f'Horizontal overflow at {width}'
    page.close()

def test_unsaved_review_cannot_follow_a_different_clip(browser,cut_review_scope_server):
    base,data=cut_review_scope_server;page,csp=open_cut_scope_review(browser,cut_review_scope_server)
    page.locator('#cut-verdict').select_option('needs_adjustment');page.locator('#cut-comment').fill('First clip draft');page.locator('#cut-suggested-end').fill('6.2')
    page.locator('#cut-clip').select_option('c'*24)
    selected=page.locator('#cut-clip').input_value()
    if selected!=CLIP:
        assert page.locator('#cut-comment').input_value()!='First clip draft','Unsaved fields were retargeted to another clip'
        page.locator('#cut-clip').select_option(CLIP)
    expect(page.locator('#cut-comment')).to_have_value('First clip draft')
    expect(page.locator('#cut-suggested-end')).to_have_value('6.2')
    page.locator('#cut-save').click();expect(page.locator('#cut-report-list')).to_contain_text('First clip draft')
    records=page.request.get(base+f'/api/processing-annotations/sources/{ID}').json()['annotations']
    saved=next(a for a in records if a['body'].get('text')=='First clip draft')
    assert saved['target']['clip_id']==CLIP and saved['body']['suggested_end_ms']==6200
    page.close()


def test_cut_review_worker_hints_prefill_without_auto_save(browser,tmp_path):
    from test_cut_diagnostics import diagnostics_for
    import hashlib
    app,root,data=setup.__wrapped__(tmp_path/'hints-ui')
    raw=json.dumps(diagnostics_for(data)).encode()
    (root/'metadata/sessions'/ID/'heuristic-diagnostics.json').write_bytes(raw)
    data['artifacts']=['heuristic-diagnostics.json']
    data['metadata_checksums']={'heuristic-diagnostics.json':hashlib.sha256(raw).hexdigest()}
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    shutil.copyfile(Path(__file__).parent/'fixtures/clip.mp4',root/'media'/ID/'Clips/clip-001.mp4')
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        page=browser.new_page(viewport={'width':390,'height':844})
        page.goto(base+f'/review/{ID}/cuts');page.locator('#cut-hints-panel summary').click()
        expect(page.locator('#cut-hints')).to_contain_text('Person track disappeared')
        page.get_by_role('button',name='Prefill review',exact=True).click()
        expect(page.locator('#cut-suggested-end')).to_have_value('12')
        expect(page.locator('#cut-verdict')).to_have_value('')
        assert page.request.get(base+f'/api/processing-annotations/sources/{ID}').json()['annotations']==[]
        page.locator('#cut-save').click()
        expect(page.locator('#cut-report-list')).to_contain_text('End too late')
        saved=page.request.get(base+f'/api/processing-annotations/sources/{ID}').json()['annotations'][0]
        assert saved['body']['heuristic_ref']=='tail' and saved['origin']['type']=='human' and saved['training_label_status']=='not_released'
        page.close()
    finally:server.shutdown();thread.join()


@pytest.mark.parametrize('width',[390,1280])
def test_two_version_comparison_mobile_deck_and_preference(browser,tmp_path,width):
    from test_cut_comparisons import Server
    from entity_model import output_version
    app,root,data=setup.__wrapped__(tmp_path/'comparison-ui')
    data['published_at']+=1;data['video']['duration_seconds']=2;data['clips'][0].update(duration_seconds=2,original_start_seconds=5,original_end_seconds=7,playback_start_seconds=0,playback_end_seconds=2)
    data['kept_seconds']=2;data['removed_seconds']=18
    (root/'metadata/sessions'/f'{ID}.json').write_text(json.dumps(data))
    candidate=copy.deepcopy(data);candidate['published_at']+=1;candidate['media_directory']='d'*32;candidate['artifact_directory']='d'*32;candidate['processing']={'task_id':'c'*32,'attempt_id':'d'*32};candidate['clips'][0].update(original_start_seconds=6,original_end_seconds=8)
    candidate_id=output_version(ID,candidate['published_at']);serving_id=output_version(ID,data['published_at'])
    server_api=Server(candidate);app.extensions['processing_client']=server_api
    fixtures=Path(__file__).parent/'fixtures';shutil.copyfile(fixtures/'clip.mp4',root/'media'/ID/'Clips/clip-001.mp4')
    destination=root/'media'/('d'*32)/'Clips';destination.mkdir(parents=True);shutil.copyfile(fixtures/'clip.mp4',destination/'clip-001.mp4')
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        page=browser.new_page(viewport={'width':width,'height':844});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base+f'/review/{ID}/compare');expect(page.get_by_role('heading',name='Compare versions',exact=True)).to_be_visible()
        page.locator('#compare-refresh').click();expect(page.locator(f'#compare-b option[value="{candidate_id}"]')).to_have_count(1)
        page.locator('#compare-a').select_option(serving_id)
        with page.expect_response(lambda r:r.url.endswith('/comparison-lease') and r.status==200):page.locator('#compare-b').select_option(candidate_id)
        assert any(call[0].endswith('/pins') and call[1].get('holder_kind')=='comparison_session' and call[2].get('method')!='DELETE' for call in server_api.calls)
        # Opening at a removed gap still pins the inactive mobile candidate immediately.
        expect(page.locator('#compare-workspace video')).to_have_count(1 if width<650 else 2)
        page.get_by_role('button',name='0:05–0:06 · Only A kept',exact=True).click()
        expect(page.locator('#compare-status')).to_have_text('',timeout=15000)
        page.locator('#compare-play').click()
        expect(page.locator('#compare-status')).to_have_text('Range boundary reached. Tap Play to continue.',timeout=15000)
        assert abs(float(page.locator('#compare-scrub').input_value())-6000)<2
        if width>=650:expect(page.locator('#compare-workspace video:visible')).to_have_count(2)
        page.get_by_role('button',name='0:06–0:07 · Both kept',exact=True).click()
        expect(page.locator('#compare-status')).to_have_text('',timeout=15000)
        expect(page.locator('#compare-workspace video:visible')).to_have_count(1 if width<650 else 2)
        if width<650:
            page.locator('#compare-deck-b').click();expect(page.locator('#compare-label-mobile')).to_contain_text('B')
            expect(page.locator('#compare-status')).to_have_text('',timeout=15000)
            page.get_by_role('button',name='0:05–0:06 · Only A kept',exact=True).click()
            expect(page.locator('#compare-slate-mobile')).to_have_text('Removed in B')
        page.locator('#compare-note').fill('Prefer the longer ending');page.locator('#compare-preferred').select_option('b');page.locator('#compare-save').click()
        expect(page.locator('#compare-save-status')).to_contain_text('Comparison saved')
        record=page.request.get(base+f'/api/processing/videos/{ID}/comparisons').json()['items'][0];assert record['preferred_version_id']==candidate_id
        assert page.request.get(base+f'/api/processing/videos/{ID}/versions').json()['current_version_id']==serving_id
        for viewport_width in (320,width):
            page.set_viewport_size({'width':viewport_width,'height':844});assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        assert not errors
        page.close()
    finally:server.shutdown();thread.join()


def test_cut_rerun_requires_selected_revision_and_confirmed_command(browser,tmp_path):
    from test_cut_reprocess import Server,review_setup
    app,root,data=setup.__wrapped__(tmp_path/'rerun-ui')
    app,client,data,record,body=review_setup((app,root,data));server_api=Server(data);app.extensions['processing_client']=server_api;app.config['PROCESSING_API_URL']='http://processor.example'
    shutil.copyfile(Path(__file__).parent/'fixtures/clip.mp4',root/'media'/ID/'Clips/clip-001.mp4')
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        page=browser.new_page(viewport={'width':390,'height':844});page.goto(base+f'/review/{ID}/cuts');page.locator('#cut-rerun-panel summary').click()
        page.locator('#cut-rerun-evidence input').check();page.locator('#cut-rerun-preview').click()
        expect(page.locator('#cut-rerun-commands')).to_contain_text('Confirm commands')
        expect(page.get_by_label('Confirm command 1',exact=True)).not_to_be_checked()
        page.locator('#cut-rerun-submit').click();expect(page.locator('#cut-rerun-status')).to_have_text('Confirm at least one exact command.')
        assert not server_api.calls
        page.get_by_label('Confirm command 1',exact=True).check();page.locator('#cut-rerun-submit').click()
        expect(page.locator('#cut-rerun-status')).to_contain_text('Candidate task created')
        snapshot=server_api.calls[1][1];assert snapshot['confirmed_commands']['override_clip_bounds_ms'][0]['original_end_ms']==16000
        assert server_api.calls[-1][1]['publication_policy']=='never'
        page.get_by_role('button',name='Prepare another task',exact=True).click()
        expect(page.locator('#cut-rerun-preview')).to_be_enabled()
        page.evaluate("async () => await NoteUI.cuts(await api('/api/sessions/"+ID+"'))")
        expect(page.locator('#cut-rerun-panel summary')).to_have_text('Prepare reprocess task')
        page.locator('#cut-rerun-panel summary').click()
        expect(page.locator('#cut-rerun-evidence input')).to_have_count(1)
        gap=page.request.post(base+'/api/processing-annotations',headers={'X-Tennis-Request':'1'},data=dict(source_id=ID,publication=data['published_at'],kind='cut_feedback',target=dict(scope='interval',start_seconds=0,end_seconds=4),body=dict(text='Gap review',reasons=['missed_play']),request_id='second-gap-review'))
        assert gap.status==201
        page.evaluate('async () => await NoteUI.refresh(true)')
        for checkbox in page.locator('#cut-rerun-evidence input').all():checkbox.check()
        with page.expect_response(lambda r:'/reprocess-preview' in r.url) as response:page.locator('#cut-rerun-preview').click()
        chosen=response.value.json()['evidence'][1]
        page.locator('#cut-command-evidence').select_option('1');page.locator('#cut-command-add').click()
        row=page.locator('#cut-rerun-commands [data-command]').last
        expect(row.locator('[data-start]')).to_have_value(str(int(chosen['target']['start_seconds'])))
        expect(row.locator('[data-end]')).to_have_value(str(int(chosen['target']['end_seconds'])))
        row.locator('input[type=checkbox]').check();page.locator('#cut-rerun-submit').click()
        expect(page.locator('#cut-rerun-status')).to_contain_text('Candidate task created')
        assert server_api.calls[-2][1]['confirmed_commands']['keep_intervals_ms'][0]['source_annotation_id']==chosen['annotation_id']
        assert not page.evaluate('document.documentElement.scrollWidth>innerWidth')
        page.close()
    finally:server.shutdown();thread.join()


def test_historical_preview_renewal_preserves_playhead_and_releases_on_switch(browser,tmp_path):
    from test_cut_comparisons import Server
    from entity_model import output_version
    app,root,data=setup.__wrapped__(tmp_path/'historical-preview-ui')
    candidate=copy.deepcopy(data);candidate['published_at']+=1;candidate['media_directory']='d'*32;candidate['artifact_directory']='d'*32;candidate['processing']={'task_id':'c'*32,'attempt_id':'d'*32}
    candidate['video']['duration_seconds']=2;candidate['clips'][0].update(duration_seconds=2,original_start_seconds=6,original_end_seconds=8,playback_start_seconds=0,playback_end_seconds=2)
    server_api=Server(candidate);app.extensions['processing_client']=server_api
    assert app.test_client().post(f'/api/processing/videos/{ID}/versions/import',json={},headers={'X-Tennis-Request':'1'}).status_code==200
    candidate_id=output_version(ID,candidate['published_at']);serving_id=output_version(ID,data['published_at'])
    destination=root/'media'/('d'*32)/'Clips';destination.mkdir(parents=True);shutil.copyfile(Path(__file__).parent/'fixtures/clip.mp4',destination/'clip-001.mp4')
    server=make_server('127.0.0.1',0,app,threaded=True);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();base=f'http://127.0.0.1:{server.server_port}'
    try:
        page=browser.new_page(viewport={'width':390,'height':844});page.goto(base+f'/review/{ID}/cuts?version={candidate_id}')
        expect(page.locator('#cut-video')).to_be_visible();page.wait_for_function("() => document.querySelector('#cut-video').readyState>=1")
        page.evaluate("() => {const v=document.querySelector('#cut-video');v.currentTime=.5;v.pause();window.previewLoads=0;v.addEventListener('loadedmetadata',()=>window.previewLoads++);const originalNow=Date.now;Date.now=()=>originalNow()+360000;}")
        page.wait_for_function("() => Math.abs(document.querySelector('#cut-video').currentTime-.5)<.05")
        page.evaluate('async () => await NoteUI.refresh(true)')
        assert abs(page.locator('#cut-video').evaluate('(v)=>v.currentTime')-.5)<.05 and page.evaluate('window.previewLoads')==0
        with page.expect_response(lambda r:r.url.endswith('/comparison-lease') and r.request.method=='DELETE' and r.status==200):page.locator('#cut-version').select_option(serving_id)
        assert any(call[2].get('method')=='DELETE' for call in server_api.calls)
        page.close()
    finally:server.shutdown();thread.join()


def test_auto_coach_real_worker_smoke_and_fallback(browser, ui_server):
    page = browser.new_page(viewport={'width': 1280, 'height': 900})
    requests = []
    errors = []
    csp_violations = []
    page.on('request', lambda req: requests.append((req.method, req.url)))
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.add_init_script("""(() => {
      window.__cspViolations = [];
      document.addEventListener('securitypolicyviolation', e => {
        window.__cspViolations.push({directive: e.violatedDirective, blocked: e.blockedURI});
      });
      const NativeWorker = window.Worker;
      window.__autoCoachWorkerEvents = [];
      window.Worker = function(...args) {
        const worker = new NativeWorker(...args);
        const record = (type, detail = {}) => window.__autoCoachWorkerEvents.push({type, at: Math.round(performance.now()), ...detail});
        worker.addEventListener('message', ({data}) => record(data.type, {message: data.message, code: data.code}));
        worker.addEventListener('error', event => record('worker_error', {message: event.message}));
        const post = worker.postMessage.bind(worker), terminate = worker.terminate.bind(worker);
        worker.postMessage = (data, ...rest) => { record('send:' + data.type); return post(data, ...rest); };
        worker.terminate = () => { record('terminate'); return terminate(); };
        return worker;
      };
    })()""")
    page.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    page.get_by_role('button', name='Study clip', exact=True).click()
    page.wait_for_function("() => document.querySelector('#study-video')?.readyState >= 2")
    assert page.evaluate('window.__autoCoachWorkerEvents.length') == 0

    auto_btn = page.locator('#coach-auto-ask')
    expect(auto_btn).to_be_enabled()
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'ready')
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '2')
    auto_btn.click()

    page.wait_for_function(
        "() => ['detected', 'empty', 'unavailable'].includes(document.querySelector('#study-video')?.dataset.autoCoachState)",
        timeout=45000,
    )
    events = page.evaluate('window.__autoCoachWorkerEvents')
    assert any(e['type'] == 'send:init' for e in events)
    assert any(e['type'] == 'terminate' for e in events)
    unsupported = any(e['type'] == 'unsupported' and e.get('code') == 'worker_graphics_unavailable' for e in events)
    if unsupported:
        assert browser.browser_type.name != 'chromium', 'Reference Chromium must run real MediaPipe inference in Clip Study'
        expect(auto_btn).to_have_attribute('data-auto-coach-state', 'unavailable')
        expect(auto_btn).to_be_disabled()
        expect(page.locator('#coach-status')).to_contain_text('Automatic detection is unavailable in this browser.')
        expect(page.locator('#coach-ask')).to_be_enabled()
    else:
        assert int(page.locator('#study-video').get_attribute('data-auto-coach-detections') or '0') >= 1
        assert any('/static/vendor/mediapipe/vision_wasm_internal.wasm' in url for _, url in requests)
        assert any('/static/vendor/mediapipe/efficientdet_lite0.tflite' in url for _, url in requests)
        expect(page.locator('#coach-ask')).to_be_enabled()

    assert page.evaluate('window.__cspViolations') == []
    assert all(url.startswith((ui_server + '/', 'data:', 'blob:')) for _, url in requests), requests
    assert not errors
    page.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_clip_study_auto_coach_detection_cap_cancellation_and_manual_coexistence(browser, ui_server, width):
    page = browser.new_page(viewport={'width': width, 'height': 844}, has_touch=width < 761)
    errors = []
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.add_init_script("""(() => {
      window.__scanWorkers = [];
      window.__scanScene = 'empty';
      window.__scanDelayMs = 2;
      window.Worker = class {
        constructor(url) {
          this.url = url;
          this.terminated = false;
          this.sent = [];
          window.__scanWorkers.push(this);
        }
        postMessage(msg) {
          if (this.terminated) return;
          this.sent.push({type: msg.type, seq: msg.seq});
          if (msg.type === 'init') {
            setTimeout(() => {
              if (!this.terminated) this.onmessage?.({data: {type: 'ready'}});
            }, 2);
          } else if (msg.type === 'detect') {
            const w = msg.width || 320, h = msg.height || 180;
            const v = document.getElementById('study-video');
            const t = Number(v?.currentTime || 0);
            const scene = window.__scanScene;
            let detections = [];
            const box = (nx, ny, nw, nh, score = 0.92) => ({
              boundingBox: {originX: nx * w, originY: ny * h, width: nw * w, height: nh * h},
              categories: [{categoryName: 'person', score}],
            });
            if (scene === 'two_bursts') {
              if (t >= 1.5 && t <= 4.2) {
                const shift = (t - 1.5) * 0.14;
                detections = [box(0.28 + shift, 0.34, 0.16, 0.42)];
              } else if (t >= 7.8 && t <= 10.5) {
                const shift = (t - 7.8) * 0.14;
                detections = [box(0.66 - shift, 0.32, 0.17, 0.44)];
              } else {
                detections = [box(0.45, 0.35, 0.16, 0.42)];
              }
            } else if (scene === 'one_burst') {
              if (t >= 2.0 && t <= 4.5) {
                const shift = (t - 2.0) * 0.14;
                detections = [box(0.32 + shift, 0.34, 0.16, 0.42)];
              } else {
                detections = [box(0.45, 0.35, 0.16, 0.42)];
              }
            }
            setTimeout(() => {
              if (!this.terminated) {
                this.onmessage?.({data: {type: 'result', epoch: msg.epoch, seq: msg.seq, detections}});
              }
            }, window.__scanDelayMs);
          }
        }
        terminate() {
          this.terminated = true;
        }
      };
    })()""")

    source = page.request.get(ui_server + f'/api/sessions/{ID}').json()
    publication = source['published_at']
    state = dict(enabled=True, jobs=[], annotations=[], workers=[])
    export_calls = []
    ai_calls = []
    cancel_calls = []
    fail_second_export = {'conflict': False}

    def handle_sessions(route):
        route.fulfill(json=state)

    def handle_exports(route):
        body = route.request.post_data_json
        export_calls.append(body)
        if body.get('auto_detect') and body.get('auto_slot') == 2 and fail_second_export['conflict']:
            fail_second_export['conflict'] = False
            route.fulfill(status=409, json={'error': 'Automatic coach review limit reached for this clip (2 of 2 used).'})
            return
        idx = len(export_calls)
        job = dict(
            id=f'exp-{idx}',
            version=1,
            role='render',
            state='queued',
            asked_at=1700010000.0 + idx,
            updated=1700010000.0 + idx,
            body=dict(
                body,
                original_start=body['start'],
                original_end=body['end'],
                player_context=dict(player_id=body.get('player_id'), profile_version=body.get('player_profile_version')) if body.get('player_id') else None,
            ),
            result={},
        )
        state['jobs'].append(job)
        route.fulfill(status=201, json=job)

    def handle_requests(route):
        body = route.request.post_data_json
        ai_calls.append(body)
        render_job = next(j for j in state['jobs'] if j['id'] == body['export_id'])
        idx = len(ai_calls)
        job = dict(
            id=f'ai-{idx}',
            version=1,
            role='ai',
            state='completed',
            asked_at=1700020000.0 + idx,
            updated=1700020010.0 + idx,
            body=dict(
                render_job['body'],
                **body,
                evidence_frames=[],
            ),
            result=dict(
                answer=f'Coach review {idx}',
                structured=dict(summary=f'Auto summary {idx}', validation_warnings=[], parse_status='valid'),
            ),
        )
        state['jobs'].append(job)
        route.fulfill(status=201, json=job)

    def handle_job_put(route):
        job_id = route.request.url.rsplit('/', 1)[-1]
        body = route.request.post_data_json
        cancel_calls.append((job_id, body))
        target = next(j for j in state['jobs'] if j['id'] == job_id)
        target['state'] = 'cancelled'
        target['version'] += 1
        route.fulfill(json=target)

    page.route('**/api/coaching/sessions/*', handle_sessions)
    page.route('**/api/coaching/exports', handle_exports)
    page.route('**/api/coaching/requests', handle_requests)
    page.route('**/api/coaching/jobs/*', handle_job_put)

    page.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    page.evaluate("""() => {
      model.session.clips[0].duration_seconds = 12;
      model.session.clips[0].original_end_seconds = 12;
      const v = document.getElementById('study-video');
      const proto = HTMLMediaElement.prototype;
      const realCurrentTime = Object.getOwnPropertyDescriptor(proto, 'currentTime');
      let logicalTime = 0;
      Object.defineProperty(v, 'duration', {configurable: true, get: () => 12});
      Object.defineProperty(v, 'currentTime', {
        configurable: true,
        get: () => logicalTime,
        set: val => {
          logicalTime = Number(val) || 0;
          realCurrentTime.set.call(v, Math.min(1.95, Math.max(0, (logicalTime / 12) * 1.9)));
        },
      });
    }""")
    page.get_by_role('button', name='Study clip', exact=True).click()
    page.wait_for_function("() => document.querySelector('#study-video')?.readyState >= 2")
    if width < 761:
        page.locator('#study-mode-coach').click()

    # Zero background scanning on modal open.
    assert page.evaluate('window.__scanWorkers.length') == 0
    assert len(export_calls) == 0 and len(ai_calls) == 0

    auto_btn = page.locator('#coach-auto-ask')
    expect(auto_btn).to_be_visible()
    expect(auto_btn).to_be_enabled()
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'ready')
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '2')
    expect(page.locator('#coaching-panel .study-muted').first).to_contain_text('sending up to 32 stills per review and your question to OpenAI')
    expect(page.locator('#coach-auto-quota')).to_have_text('2 of 2 automatic reviews remaining')

    # 1. Empty court / static scene -> 0 windows selected, 0 jobs created, truthful guidance shown.
    auto_btn.click()
    expect(page.locator('#coach-status')).to_contain_text('No clear player motion detected in this clip. Adjust the interval and use Ask coach.')
    assert len(export_calls) == 0 and len(ai_calls) == 0
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '2')
    assert page.evaluate('window.__scanWorkers.at(-1).terminated') is True

    # 2. Cancellation during scan restores previous playhead and interval without submitting exports.
    page.evaluate("""() => {
      window.ClipStudy.setInterval(0.3, 1.5);
      const v = document.getElementById('study-video');
      v.pause();
      v.currentTime = 0.6;
      window.__scanScene = 'two_bursts';
      window.__scanDelayMs = 250;
    }""")
    auto_btn.click()
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'scanning')
    expect(page.locator('#coach-auto-cancel')).to_be_visible()
    # Double-clicking while scanning must not spawn a second worker.
    workers_during_scan = page.evaluate('window.__scanWorkers.length')
    auto_btn.click(force=True)
    assert page.evaluate('window.__scanWorkers.length') == workers_during_scan
    page.locator('#coach-auto-cancel').click()
    expect(page.locator('#coach-status')).to_contain_text('Auto-detect scan cancelled.')
    expect(page.locator('#coach-auto-cancel')).to_be_hidden()
    assert len(export_calls) == 0
    sel_after_cancel = page.evaluate('window.ClipStudy.coachingSelection()')
    assert sel_after_cancel['start'] == pytest.approx(0.3, abs=0.05)
    assert sel_after_cancel['end'] == pytest.approx(1.5, abs=0.05)
    assert page.locator('#study-video').evaluate('v => v.currentTime') == pytest.approx(0.6, abs=0.08)

    # Closing Clip Study during an active scan also terminates the worker and submits 0 exports.
    auto_btn.click()
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'scanning')
    page.get_by_role('button', name='← Back to review', exact=True).click()
    page.wait_for_function('() => window.__scanWorkers.at(-1)?.terminated === true')
    assert len(export_calls) == 0
    page.get_by_role('button', name='Study clip', exact=True).click()
    page.wait_for_function("() => document.querySelector('#study-video')?.readyState >= 2")
    if width < 761:
        page.locator('#study-mode-coach').click()

    # 3. Two non-overlapping motion bursts: set custom question, speed 0.25x, and zoom crop, then run Auto detect & ask.
    page.evaluate('() => { window.__scanDelayMs = 2; window.__scanScene = "two_bursts"; }')
    if width < 761:
        page.locator('#study-mode-watch').click()
    page.locator('#study-speed').select_option('0.25')
    zoom = page.get_by_role('slider', name='Video zoom', exact=True)
    zoom.press('Home')
    for _ in range(3):
        zoom.press('ArrowRight')
    if width < 761:
        page.locator('#study-mode-coach').click()
    page.get_by_role('textbox', name='Question for coach', exact=True).fill('Check split step and recovery on auto moments')
    auto_btn.click()

    page.wait_for_function('() => document.querySelectorAll("#coach-history .coach-note").length === 2')
    assert len(export_calls) == 2
    assert export_calls[0]['auto_detect'] is True and export_calls[1]['auto_detect'] is True
    assert export_calls[0]['consent'] is True and export_calls[1]['consent'] is True
    assert export_calls[0]['auto_slot'] == 1 and export_calls[1]['auto_slot'] == 2
    assert export_calls[0]['speed'] == 0.25 and export_calls[1]['speed'] == 0.25
    assert export_calls[0]['question'] == 'Check split step and recovery on auto moments'
    assert 0 < min(export_calls[0]['crop'][2:]) < 1
    assert export_calls[0]['crop'] == export_calls[1]['crop']
    assert export_calls[0]['request_id'] != export_calls[1]['request_id']
    # Non-overlapping windows in chronological order.
    w1, w2 = sorted(export_calls[:2], key=lambda c: c['start'])
    assert w1['end'] <= w2['start'] + 1e-6

    # Both render jobs are queued: auto button is disabled in 'submitting' state with 0 remaining.
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'submitting')
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '0')
    expect(auto_btn).to_be_disabled()
    expect(page.locator('#coach-history')).to_contain_text('Preparing clip… · Auto-detected')

    # Complete exp-1 and fail exp-2 pre-call: exp-1 advances to POST /api/coaching/requests, while exp-2 releases its slot!
    state['jobs'][0].update(state='completed', version=2, result={'artifact_directory': 'f1'})
    state['jobs'][1].update(state='failed', version=2, result={'error': 'Render worker interrupted'})
    page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
    if width < 761:
        page.locator('#study-mode-coach').click()
    page.wait_for_function('() => document.querySelector("#coach-auto-ask")?.dataset.autoCoachState === "ready"')
    assert len(ai_calls) == 1
    assert ai_calls[0]['export_id'] == 'exp-1'
    assert ai_calls[0]['auto_detect'] is True
    assert ai_calls[0]['request_id'] == 'auto-ai-exp-1'
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '1')
    expect(page.locator('#coach-auto-quota')).to_contain_text('1 of 2 automatic review remaining')
    expect(page.locator('#coach-history')).to_contain_text('Auto summary 1')
    expect(page.locator('#coach-history')).to_contain_text('Ready · Auto-detected')

    # 4. With 1 slot remaining (exp-1 succeeded, exp-2 failed), clicking Auto detect & ask excludes exp-1's interval, selects the other eligible burst (exp-2's region), and reaches the 2/2 cap.
    auto_btn.click()
    page.wait_for_function('() => document.querySelector("#coach-history")?.innerText.includes("Preparing clip…")')
    assert len(export_calls) == 3
    assert export_calls[2]['start'] >= export_calls[0]['end'], f'Retry must not duplicate consumed first interval: {export_calls[:3]}'
    assert export_calls[2]['start'] == pytest.approx(export_calls[1]['start'], abs=0.05)
    assert export_calls[2]['end'] == pytest.approx(export_calls[1]['end'], abs=0.05)
    # Complete exp-3 -> advances to ai-2 -> reaches 2 of 2 cap.
    state['jobs'][-1].update(state='completed', version=2, result={'artifact_directory': 'f3'})
    page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
    if width < 761:
        page.locator('#study-mode-coach').click()
    page.wait_for_function('() => document.querySelector("#coach-auto-ask")?.dataset.autoCoachState === "capped"')
    assert len(ai_calls) == 2
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'capped')
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '0')
    expect(auto_btn).to_be_disabled()
    expect(page.locator('#coach-auto-quota')).to_contain_text('0 of 2 automatic reviews remaining')

    # Reopening Clip Study preserves the capped state.
    page.get_by_role('button', name='← Back to review', exact=True).click()
    page.get_by_role('button', name='Study clip', exact=True).click()
    if width < 761:
        page.locator('#study-mode-coach').click()
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'capped')
    expect(auto_btn).to_be_disabled()

    # 5. Manual Ask coach remains enabled and functional when the automatic cap is full.
    expect(page.locator('#coach-ask')).to_be_enabled()
    page.get_by_role('textbox', name='Question for coach', exact=True).fill('Manual question after auto cap reached')
    page.locator('#coach-ask').click()
    assert len(export_calls) == 4
    assert not export_calls[-1].get('auto_detect')

    # 6. Another clip in the same recording has its own 2-review quota, and a 409 conflict on the 2nd window preserves the 1st submission with a partial-success notice.
    fail_second_export['conflict'] = True
    page.evaluate("""() => {
      model.session.clips[1].duration_seconds = 12;
      model.session.clips[1].original_start_seconds = 20;
      model.session.clips[1].original_end_seconds = 32;
      window.Coach.open(model.session, model.session.clips[1]);
    }""")
    if width < 761:
        page.locator('#study-mode-coach').click()
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'ready')
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '2')
    auto_btn.click()
    expect(page.locator('#coach-status')).to_contain_text('Submitted 1 automatic review')
    expect(page.locator('#coach-status')).to_contain_text('Automatic coach review limit reached for this clip (2 of 2 used).')
    assert len(export_calls) == 6
    assert export_calls[-2]['clip_id'] == 'c' * 24 and export_calls[-2]['auto_slot'] == 1
    assert export_calls[-1]['clip_id'] == 'c' * 24 and export_calls[-1]['auto_slot'] == 2

    for check_w in (320, 390, 1280):
        page.set_viewport_size({'width': check_w, 'height': 844})
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'Horizontal overflow at {check_w}px'
        assert page.locator('#clip-study').evaluate('d => d.scrollWidth <= d.clientWidth'), f'Dialog overflow at {check_w}px'
    assert not errors
    page.close()


def test_clip_study_auto_coach_long_clip_watchdog_and_stale_abort_regressions(browser, ui_server):
    page = browser.new_page(viewport={'width': 1280, 'height': 844})
    errors = []
    page.on('pageerror', lambda err: errors.append(str(err)))
    page.add_init_script("""(() => {
      window.__scanWorkers = [];
      window.__scanScene = 'long_60s_two_bursts';
      window.__scanDelayMs = 2;
      window.__workerMode = 'normal';
      window.Worker = class {
        constructor(url) {
          this.url = url;
          this.terminated = false;
          this.sent = [];
          window.__scanWorkers.push(this);
        }
        postMessage(msg) {
          if (this.terminated) return;
          this.sent.push({type: msg.type, seq: msg.seq});
          if (msg.type === 'init') {
            setTimeout(() => {
              if (this.terminated) return;
              if (window.__workerMode === 'detector_error') {
                this.onmessage?.({data: {type: 'error', code: 'detector_error'}});
              } else if (window.__workerMode === 'unsupported') {
                this.onmessage?.({data: {type: 'unsupported', code: 'worker_graphics_unavailable'}});
              } else {
                this.onmessage?.({data: {type: 'ready'}});
              }
            }, 2);
          } else if (msg.type === 'detect') {
            const w = msg.width || 320, h = msg.height || 180;
            const v = document.getElementById('study-video');
            const t = Number(v?.currentTime || 0);
            const scene = window.__scanScene;
            let detections = [];
            const box = (nx, ny, nw, nh, score = 0.92) => ({
              boundingBox: {originX: nx * w, originY: ny * h, width: nw * w, height: nh * h},
              categories: [{categoryName: 'person', score}],
            });
            if (scene === 'long_60s_two_bursts') {
              // Paired samples on 60s clip: active player movement in [5s, 20s] and [40s, 55s].
              if (t >= 5.0 && t <= 20.0) {
                const shift = ((t % 2.0) / 2.0) * 0.18;
                detections = [box(0.28 + shift, 0.34, 0.16, 0.42)];
              } else if (t >= 40.0 && t <= 55.0) {
                const shift = ((t % 2.0) / 2.0) * 0.18;
                detections = [box(0.66 - shift, 0.32, 0.17, 0.44)];
              } else {
                detections = [box(0.45, 0.35, 0.16, 0.42)];
              }
            } else if (scene === 'long_60s_one_burst') {
              if (t >= 9.5 && t <= 13.0) {
                const shift = ((t - 9.5) / 3.5) * 0.22;
                detections = [box(0.28 + shift, 0.34, 0.16, 0.42)];
              } else {
                detections = [];
              }
            }
            setTimeout(() => {
              if (!this.terminated) {
                this.onmessage?.({data: {type: 'result', epoch: msg.epoch, seq: msg.seq, detections}});
              }
            }, window.__scanDelayMs);
          }
        }
        terminate() {
          this.terminated = true;
        }
      };
    })()""")

    source = page.request.get(ui_server + f'/api/sessions/{ID}').json()
    publication = source['published_at']
    state = dict(enabled=True, jobs=[], annotations=[], workers=[])
    export_calls = []
    ai_calls = []
    stale_switch_on_first_ai = {'active': False}

    def handle_sessions(route):
        route.fulfill(json=state)

    def handle_exports(route):
        body = route.request.post_data_json
        export_calls.append(body)
        idx = len(export_calls)
        job = dict(
            id=f'exp-long-{idx}',
            version=1,
            role='render',
            state='queued',
            asked_at=1700010000.0 + idx,
            updated=1700010000.0 + idx,
            body=dict(
                body,
                original_start=body['start'],
                original_end=body['end'],
                player_context=None,
            ),
            result={},
        )
        state['jobs'].append(job)
        route.fulfill(status=201, json=job)

    def handle_requests(route):
        body = route.request.post_data_json
        ai_calls.append(body)
        if stale_switch_on_first_ai['active'] and body.get('export_id') == 'exp-stale-1':
            stale_switch_on_first_ai['active'] = False
            # Switch clip (or close) while first request is in flight, then reject the first request!
            page.evaluate('() => window.Coach.open(model.session, model.session.clips[1])')
            route.fulfill(status=409, json={'error': 'First auto export was cancelled before AI submission'})
            return
        render_job = next(j for j in state['jobs'] if j['id'] == body['export_id'])
        idx = len(ai_calls)
        job = dict(
            id=f'ai-long-{idx}',
            version=1,
            role='ai',
            state='completed',
            asked_at=1700020000.0 + idx,
            updated=1700020010.0 + idx,
            body=dict(render_job['body'], **body, evidence_frames=[]),
            result=dict(
                answer=f'Coach review {idx}',
                structured=dict(summary=f'Auto summary {idx}', validation_warnings=[], parse_status='valid'),
            ),
        )
        state['jobs'].append(job)
        route.fulfill(status=201, json=job)

    page.route('**/api/coaching/sessions/*', handle_sessions)
    page.route('**/api/coaching/exports', handle_exports)
    page.route('**/api/coaching/requests', handle_requests)

    page.goto(ui_server + f'/review/{ID}?clip={CLIP}')
    expect(page.locator('#video-seek')).to_be_enabled()
    # Configure 60s clip duration to verify >40s clips select 2 non-overlapping moments in Clip Study.
    page.evaluate("""() => {
      model.session.clips[0].duration_seconds = 60;
      model.session.clips[0].original_end_seconds = 60;
      const v = document.getElementById('study-video');
      const proto = HTMLMediaElement.prototype;
      const realCurrentTime = Object.getOwnPropertyDescriptor(proto, 'currentTime');
      let logicalTime = 0;
      Object.defineProperty(v, 'duration', {configurable: true, get: () => 60});
      Object.defineProperty(v, 'currentTime', {
        configurable: true,
        get: () => logicalTime,
        set: val => {
          logicalTime = Number(val) || 0;
          realCurrentTime.set.call(v, Math.min(1.95, Math.max(0, (logicalTime / 60) * 1.9)));
        },
      });
    }""")
    page.get_by_role('button', name='Study clip', exact=True).click()
    page.wait_for_function("() => document.querySelector('#study-video')?.readyState >= 2")

    auto_btn = page.locator('#coach-auto-ask')
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'ready')

    # 1. Total scan watchdog: each sample takes 55ms (< detectTimeoutMs=2000ms), but cumulative scan exceeds scanTimeoutMs=160ms.
    page.evaluate("""() => {
      window.ClipStudy.setInterval(4.0, 12.0);
      const v = document.getElementById('study-video');
      v.pause();
      v.currentTime = 6.5;
      window.__scanDelayMs = 55;
      window.__TENNIS_AUTOFOCUS_CONFIG = {detectTimeoutMs: 2000, scanTimeoutMs: 160};
    }""")
    auto_btn.click()
    expect(page.locator('#coach-status')).to_contain_text('Auto-detect scan timed out. Try again or select an interval and use Ask coach.')
    expect(page.locator('#study-video')).to_have_attribute('data-auto-coach-state', 'error')
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'ready')
    expect(auto_btn).to_be_enabled()
    assert page.evaluate('window.__scanWorkers.at(-1).terminated') is True
    assert len(export_calls) == 0
    sel_after_timeout = page.evaluate('window.ClipStudy.coachingSelection()')
    assert sel_after_timeout['start'] == pytest.approx(4.0, abs=0.05)
    assert sel_after_timeout['end'] == pytest.approx(12.0, abs=0.05)
    assert page.locator('#study-video').evaluate('v => v.currentTime') == pytest.approx(6.5, abs=0.08)

    # 2. Runtime detector_error stays recoverable ('ready'), whereas worker_graphics_unavailable becomes 'unavailable'.
    page.evaluate("""() => {
      window.__scanDelayMs = 2;
      window.__TENNIS_AUTOFOCUS_CONFIG = {detectTimeoutMs: 8000, scanTimeoutMs: 45000};
      window.__workerMode = 'detector_error';
    }""")
    auto_btn.click()
    expect(page.locator('#coach-status')).to_contain_text('Automatic detection failed on this clip. Try again or select an interval and use Ask coach.')
    expect(page.locator('#study-video')).to_have_attribute('data-auto-coach-state', 'error')
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'ready')
    expect(auto_btn).to_be_enabled()

    page.evaluate("() => { window.__workerMode = 'unsupported'; }")
    auto_btn.click()
    expect(page.locator('#coach-status')).to_contain_text('Automatic detection is unavailable in this browser. Select an interval and use Ask coach.')
    expect(page.locator('#study-video')).to_have_attribute('data-auto-coach-state', 'unavailable')
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'unavailable')
    expect(auto_btn).to_be_disabled()
    expect(page.locator('#coach-ask')).to_be_enabled()

    # Re-open with normal worker mode to test 60s clip auto-detection (2 non-overlapping windows from <=16 paired samples).
    page.evaluate("""() => {
      window.__workerMode = 'normal';
      window.Coach.open(model.session, model.session.clips[0]);
    }""")
    expect(auto_btn).to_have_attribute('data-auto-coach-state', 'ready')
    auto_btn.click()
    page.wait_for_function('() => document.querySelectorAll("#coach-history .coach-note").length === 2')
    assert len(export_calls) == 2
    assert int(page.locator('#study-video').get_attribute('data-auto-coach-detections') or '0') == 16
    w1, w2 = sorted(export_calls, key=lambda c: c['start'])
    assert w1['end'] <= w2['start']
    assert w1['start'] < 20.0 and w2['start'] >= 35.0

    # 2b. Retry diversity on single-burst vs pre-call failed/cancelled intervals:
    # First: if w1 failed pre-call (e.g. AI failed before calling, provider_called not true) and w2 was cancelled, both intervals are eligible again on retry!
    state['jobs'] = [
        dict(
            id='exp-precall-1',
            version=2,
            role='render',
            state='completed',
            asked_at=1700025001.0,
            updated=1700025002.0,
            body=dict(source_id=ID, publication=publication, clip_id=CLIP, start=w1['start'], end=w1['end'], original_start=w1['start'], original_end=w1['end'], speed=0.5, crop=[0, 0, 1, 1], quality='balanced', question='Q1', auto_detect=True, auto_slot=1, player_context=None),
            result={'artifact_directory': 'f-pre1'},
        ),
        dict(
            id='ai-precall-1',
            version=2,
            role='ai',
            state='failed',
            asked_at=1700025003.0,
            updated=1700025004.0,
            body=dict(source_id=ID, publication=publication, clip_id=CLIP, export_id='exp-precall-1', start=w1['start'], end=w1['end'], original_start=w1['start'], original_end=w1['end'], prompt='Q1', auto_detect=True, evidence_frames=[]),
            result={'error': 'Worker preflight failed'},
        ),
        dict(
            id='exp-cancel-2',
            version=2,
            role='render',
            state='cancelled',
            asked_at=1700025005.0,
            updated=1700025006.0,
            body=dict(source_id=ID, publication=publication, clip_id=CLIP, start=w2['start'], end=w2['end'], original_start=w2['start'], original_end=w2['end'], speed=0.5, crop=[0, 0, 1, 1], quality='balanced', question='Q2', auto_detect=True, auto_slot=2, player_context=None),
            result={},
        ),
    ]
    export_calls.clear()
    page.evaluate("""() => {
      window.__scanScene = 'long_60s_one_burst';
      window.Coach.open(model.session, model.session.clips[0]);
    }""")
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '2')
    auto_btn.click()
    page.wait_for_function('() => document.querySelector("#coach-auto-ask")?.dataset.autoCoachState === "submitting"')
    assert len(export_calls) == 1
    assert export_calls[0]['start'] < 20.0 and export_calls[0]['end'] <= 22.0

    # Now w1 is in-flight/consumed (exp-long-1 queued) and no second burst exists in 'long_60s_one_burst':
    # Mark exp-long-1 completed -> advances to ai -> 1 of 2 used (1 remaining), then click Auto detect & ask again -> must select 0 windows and NOT duplicate w1!
    state['jobs'][-1].update(state='completed', version=2, result={'artifact_directory': 'f-one-1'})
    page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
    page.wait_for_function('() => document.querySelector("#coach-auto-ask")?.dataset.autoCoachState === "ready"')
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '1')
    exports_before_rescan = len(export_calls)
    auto_btn.click()
    expect(page.locator('#coach-status')).to_contain_text('No additional player-motion moments detected in this clip. Adjust the interval and use Ask coach.')
    assert len(export_calls) == exports_before_rescan, 'Must not submit duplicate export when sole eligible burst was already reviewed'
    expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '1')

    # 3. Stale rejected submission in advancePendingAutoExports after clip switch must NOT post the 2nd export!
    state['jobs'] = [
        dict(
            id='exp-stale-1',
            version=1,
            role='render',
            state='completed',
            asked_at=1700030001.0,
            updated=1700030002.0,
            body=dict(
                source_id=ID,
                publication=publication,
                clip_id=CLIP,
                start=2.0,
                end=6.0,
                original_start=2.0,
                original_end=6.0,
                speed=0.5,
                crop=[0, 0, 1, 1],
                quality='balanced',
                question='Stale export 1',
                auto_detect=True,
                auto_slot=1,
                player_context=None,
            ),
            result={'artifact_directory': 'f-stale-1'},
        ),
        dict(
            id='exp-stale-2',
            version=1,
            role='render',
            state='completed',
            asked_at=1700030003.0,
            updated=1700030004.0,
            body=dict(
                source_id=ID,
                publication=publication,
                clip_id=CLIP,
                start=10.0,
                end=14.0,
                original_start=10.0,
                original_end=14.0,
                speed=0.5,
                crop=[0, 0, 1, 1],
                quality='balanced',
                question='Stale export 2',
                auto_detect=True,
                auto_slot=2,
                player_context=None,
            ),
            result={'artifact_directory': 'f-stale-2'},
        ),
    ]
    ai_calls.clear()
    stale_switch_on_first_ai['active'] = True
    page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
    page.wait_for_function('() => document.querySelector("#coach-auto-ask")?.dataset.autoCoachRemaining === "2"')
    # Wait briefly to confirm no trailing second POST occurs after the rejected first POST.
    page.wait_for_timeout(150)
    assert [c['export_id'] for c in ai_calls] == ['exp-stale-1'], f'Expected only first stale export to be attempted before switch, got {ai_calls}'
    assert not errors
    page.close()


@pytest.mark.parametrize('width', [390, 1280])
def test_coaching_subject_selector_auto_coach_reopen_followup_and_learning(browser, tmp_path, width):
    import base64
    import hashlib
    from app import create_app
    from worker.tennis_coach.annotations import CAPABILITY, evidence_manifest

    token = 'test-worker-secret'
    png_bytes = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jWZkAAAAASUVORK5CYII=')

    app, root, data = setup.__wrapped__(tmp_path / f'subject-ui-{width}')
    data['published_at'] += 1
    data['video']['duration_seconds'] = 24
    data['source']['duration_seconds'] = 24
    data['kept_seconds'] = 24
    data['removed_seconds'] = 0
    data['clips'] = [
        dict(
            data['clips'][0],
            id=letter * 24,
            index=i + 1,
            file=f'Clips/clip-{i + 1:03}.mp4',
            duration_seconds=12,
            original_start_seconds=i * 12,
            original_end_seconds=(i + 1) * 12,
            playback_start_seconds=i * 12,
            playback_end_seconds=(i + 1) * 12,
        )
        for i, letter in enumerate('bc')
    ]
    (root / 'metadata/sessions' / f'{ID}.json').write_text(json.dumps(data))
    fixtures = Path(__file__).parent / 'fixtures'
    shutil.copyfile(fixtures / 'practice.mp4', root / 'media' / ID / 'Practice.mp4')
    for clip in data['clips']:
        shutil.copyfile(fixtures / 'clip.mp4', root / 'media' / ID / clip['file'])

    app = create_app({
        'TESTING': True,
        'LIBRARY_ROOT': str(root),
        'DATA_ROOT': app.config['DATA_ROOT'],
        'COACH_WORKER_TOKEN': token,
    })
    c = app.test_client()
    req_headers = {'X-Tennis-Request': '1'}
    worker_headers = {'X-Tennis-Request': '1', 'Authorization': f'Bearer {token}'}

    # Seed Alex as the persistent recording & clip-1 main player; clip-2 stays unassigned.
    alex = c.post('/api/players', json={'name': 'AlexPrivate', 'height_cm': 172, 'level': 'beginner'}, headers=req_headers).json
    sess0 = c.get(f'/api/sessions/{ID}').json
    assert c.put(f'/api/sessions/{ID}/players', json={'player_ids': [alex['id']], 'version': sess0['player_assignment']['version']}, headers=req_headers).status_code == 200
    sess1 = c.get(f'/api/sessions/{ID}').json
    assert c.put(
        f'/api/sessions/{ID}/clips/{CLIP}/player',
        json={'player_id': alex['id'], 'version': sess1['clips'][0]['player_assignment']['version'], 'publication': sess1['published_at']},
        headers=req_headers,
    ).status_code == 200

    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    try:
        page = browser.new_page(viewport={'width': width, 'height': 844}, has_touch=width < 761)
        errors = []
        page.on('pageerror', lambda err: errors.append(str(err)))

        def complete_claimed_render(job):
            fence = hashlib.sha256(job['token'].encode()).hexdigest()[:24]
            folder = Path(app.config['COACH_MEDIA_ROOT']) / job['id'] / fence
            folder.mkdir(parents=True)
            b = job['body']
            orig_start = float(b['original_start'])
            orig_end = float(b['original_end'])
            local_start = float(b['start'])
            speed = float(b['speed'])
            t1 = round(orig_start + (orig_end - orig_start) * 0.3, 3)
            t2 = round(orig_start + (orig_end - orig_start) * 0.7, 3)
            frames = []
            for idx, orig_sec in enumerate((t1, t2)):
                raw = png_bytes + f'-{job["id"]}-{idx}'.encode()
                fname = f'frame-{idx:03}.jpg'
                (folder / fname).write_bytes(raw)
                frames.append(dict(
                    file=fname,
                    sha256=hashlib.sha256(raw).hexdigest(),
                    original_seconds=orig_sec,
                    clip_seconds=round(local_start + (orig_sec - orig_start), 3),
                    excerpt_seconds=round((orig_sec - orig_start) / speed, 3),
                ))
            (folder / 'excerpt.mp4').write_bytes((fixtures / 'clip.mp4').read_bytes())
            meta = dict(
                recipe=b,
                lease_token=job['token'],
                frames=frames,
                bytes=1024,
                output_seconds=round((orig_end - orig_start) / speed, 3),
                sha256='e' * 64,
                render_seconds=0.1,
            )
            (folder / 'recipe.json').write_text(json.dumps(meta))
            done = c.post(f"/api/coaching/worker/jobs/{job['id']}/complete", json={'token': job['token']}, headers=worker_headers)
            assert done.status_code == 200, done.json
            return job['id']

        def finish_next_render():
            job = None
            for _ in range(120):
                claim_resp = c.post('/api/coaching/worker/render/claim', json={'worker_id': 'ui-render'}, headers=worker_headers)
                assert claim_resp.status_code == 200
                job = claim_resp.json['job']
                if job is not None:
                    break
                page.wait_for_timeout(50)
            assert job is not None, 'Expected a queued render job'
            return complete_claimed_render(job)

        def finish_next_ai(summary, title='Split step timing', cue='Land balanced before contact.'):
            job = None
            for _ in range(120):
                claim_resp = c.post('/api/coaching/worker/ai/claim', json={'worker_id': 'ui-ai', 'capabilities': [CAPABILITY]}, headers=worker_headers)
                assert claim_resp.status_code == 200
                job = claim_resp.json['job']
                if job is not None:
                    break
                page.wait_for_timeout(50)
            assert job is not None, 'Expected a queued AI job'
            b = job['body']
            folder = Path(app.config['COACH_MEDIA_ROOT']) / b['export_id'] / b['artifact_directory']
            manifest = evidence_manifest(job)
            cache = Path(app.config['COACH_MEDIA_ROOT']) / 'evidence/frames'
            cache.mkdir(parents=True, exist_ok=True)
            for frame in manifest['frames']:
                src = folder / f"frame-{frame['order'] - 1:03}.jpg"
                shutil.copyfile(src, cache / (frame['sha256'] + '.jpg'))
            ev = c.post(f"/api/coaching/worker/jobs/{job['id']}/evidence", json={'token': job['token'], 'result': manifest}, headers=worker_headers)
            assert ev.status_code == 200, ev.json
            assert c.post(f"/api/coaching/worker/jobs/{job['id']}/calling", json={'token': job['token']}, headers=worker_headers).status_code == 200
            orig_start = round(float(b['original_start']) + 0.1, 3)
            orig_end = round(max(orig_start + 0.1, float(b['original_end']) - 0.1), 3)
            structured_answer = json.dumps(dict(
                schema_version=2,
                summary=summary,
                suggestions=[dict(
                    title=title,
                    topic='Footwork',
                    observation='Stance width observed in sampled frame.',
                    cue=cue,
                    drill='Shadow 10 split-steps.',
                    uncertainty='Sampled frames cannot confirm exact ball contact.',
                    evidence_frame_ids=[manifest['frames'][0]['frame_id']],
                    applies_to=[dict(start_seconds=orig_start, end_seconds=orig_end)],
                )],
            ))
            done = c.post(
                f"/api/coaching/worker/jobs/{job['id']}/complete",
                json={'token': job['token'], 'result': dict(answer=structured_answer, analysis_mode='timestamped_frames', model='fake-ui-model')},
                headers=worker_headers,
            )
            assert done.status_code == 200, done.json
            return job

        def fulfill_manual_ask(summary, title='Split step timing'):
            for _ in range(60):
                claim_r = c.post('/api/coaching/worker/render/claim', json={'worker_id': 'ui-render'}, headers=worker_headers).json['job']
                if claim_r is not None:
                    complete_claimed_render(claim_r)
                    break
                queued_ai = [j for j in c.get(f'/api/coaching/sessions/{ID}').json['jobs'] if j['role'] == 'ai' and j['state'] == 'queued']
                if queued_ai:
                    break
                page.wait_for_timeout(50)
            return finish_next_ai(summary, title=title)
        page.add_init_script("""(() => {
          window.__scanWorkers = [];
          window.__scanScene = 'two_bursts';
          window.__scanDelayMs = 2;
          window.Worker = class {
            constructor(url) {
              this.url = url;
              this.terminated = false;
              window.__scanWorkers.push(this);
            }
            postMessage(msg) {
              if (this.terminated) return;
              if (msg.type === 'init') {
                setTimeout(() => { if (!this.terminated) this.onmessage?.({data: {type: 'ready'}}); }, 2);
              } else if (msg.type === 'detect') {
                const w = msg.width || 320, h = msg.height || 180;
                const v = document.getElementById('study-video');
                const t = Number(v?.currentTime || 0);
                let detections = [];
                const box = (nx, ny, nw, nh) => ({
                  boundingBox: {originX: nx * w, originY: ny * h, width: nw * w, height: nh * h},
                  categories: [{categoryName: 'person', score: 0.92}],
                });
                if (window.__scanScene === 'two_bursts') {
                  if (t >= 1.5 && t <= 4.2) {
                    detections = [box(0.28 + (t - 1.5) * 0.14, 0.34, 0.16, 0.42)];
                  } else if (t >= 7.8 && t <= 10.5) {
                    detections = [box(0.66 - (t - 7.8) * 0.14, 0.32, 0.17, 0.44)];
                  } else {
                    detections = [box(0.45, 0.35, 0.16, 0.42)];
                  }
                }
                setTimeout(() => {
                  if (!this.terminated) this.onmessage?.({data: {type: 'result', epoch: msg.epoch, seq: msg.seq, detections}});
                }, window.__scanDelayMs);
              }
            }
            terminate() { this.terminated = true; }
          };
        })()""")

        page.goto(base_url + f'/review/{ID}?clip={CLIP}')
        expect(page.locator('#video-seek')).to_be_enabled()
        page.evaluate("""() => {
          const v = document.getElementById('study-video');
          const proto = HTMLMediaElement.prototype;
          const realCurrentTime = Object.getOwnPropertyDescriptor(proto, 'currentTime');
          let logicalTime = 0;
          Object.defineProperty(v, 'duration', {configurable: true, get: () => 12});
          Object.defineProperty(v, 'currentTime', {
            configurable: true,
            get: () => logicalTime,
            set: val => {
              logicalTime = Number(val) || 0;
              realCurrentTime.set.call(v, Math.min(1.95, Math.max(0, (logicalTime / 12) * 1.9)));
            },
          });
        }""")

        # 1. Open Clip Study -> Coach: subject selector defaults to Alex (clip main player).
        page.get_by_role('button', name='Study clip', exact=True).click()
        page.wait_for_function("() => document.querySelector('#study-video')?.readyState >= 2")
        if width < 761:
            page.locator('#study-mode-coach').click()

        subject_sel = page.locator('#coach-subject')
        expect(subject_sel).to_be_visible()
        expect(subject_sel).to_have_value(alex['id'])
        expect(page.locator('#coach-player-context')).to_contain_text('For AlexPrivate (clip main player)')

        # 2. Add an unlisted saved player (Blair) via Saved players… without closing Clip Study or mutating clip/recording roster.
        page.locator('#coach-manage-players').click()
        expect(page.locator('#players-dialog')).to_be_visible()
        expect(page.locator('#clip-study')).to_have_js_property('open', True)
        page.get_by_role('button', name='Add player', exact=True).click()
        page.get_by_label('Name', exact=True).fill('BlairPrivate')
        page.get_by_label('Height (cm, optional)', exact=True).fill('184')
        page.get_by_label('Level', exact=True).select_option('experienced')
        page.get_by_role('button', name='Save player', exact=True).click()
        expect(page.locator('#saved-player-list')).to_contain_text('BlairPrivate')
        page.get_by_role('button', name='Close saved players', exact=True).click()
        expect(page.locator('#players-dialog')).not_to_be_visible()
        expect(page.locator('#clip-study')).to_have_js_property('open', True)

        players_now = c.get('/api/players').json['players']
        blair = next(p for p in players_now if p['name'] == 'BlairPrivate')
        expect(subject_sel.locator(f'option[value="{blair["id"]}"]')).to_have_text('BlairPrivate')

        sess_after_add = c.get(f'/api/sessions/{ID}').json
        assert sess_after_add['clips'][0]['main_player']['id'] == alex['id']
        assert [p['id'] for p in sess_after_add['recording_players']] == [alex['id']]

        # 3. Select Blair + visible target cue, click Ask coach, and switch dropdown mid-render: pending job keeps Blair!
        subject_sel.select_option(blair['id'])
        expect(page.locator('#coach-player-context')).to_contain_text('For BlairPrivate (this request only; clip player unchanged)')
        expect(page.locator('#coach-player-context')).to_contain_text('Auto detect scans foreground motion, not player identity.')
        page.locator('#coach-subject-hint').fill('far-court player in dark shirt')
        page.locator('#coach-question').fill('Check split-step posture for Blair')
        page.locator('#coach-ask').click()
        expect(page.locator('#coach-status')).to_have_text('Preparing your clip…')

        # Mutate the live UI controls while render is queued — must NOT retarget pendingAsk.
        subject_sel.select_option(alex['id'])
        page.locator('#coach-subject-hint').fill('near-court server in white')
        finish_next_render()
        # Trigger a refresh cycle by polling / waiting until AI job is queued, then complete AI job.
        page.wait_for_function("() => document.querySelector('#coach-status')?.textContent === 'Getting response…'", timeout=8000)
        claimed_blair_1 = finish_next_ai('Blair manual stance summary', title='Wide split step')
        assert claimed_blair_1['body']['subject_mode'] == 'selected_player'
        assert claimed_blair_1['body']['player_context']['player_id'] == blair['id']
        assert claimed_blair_1['body']['subject_hint'] == 'far-court player in dark shirt'
        for secret in ('BlairPrivate', 'AlexPrivate', blair['id'], alex['id']):
            assert secret not in claimed_blair_1['body']['submitted_prompt']
        assert 'Selected-player profile' in claimed_blair_1['body']['submitted_prompt']
        assert 'far-court player in dark shirt' in claimed_blair_1['body']['submitted_prompt']

        page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
        if width < 761:
            page.locator('#study-mode-coach').click()
        blair_card = page.locator(f'.coach-note[data-job="{claimed_blair_1["id"]}"]')
        expect(blair_card).to_contain_text('Blair manual stance summary')
        expect(blair_card.locator('.coach-job-subject')).to_have_text('For BlairPrivate · selected for this review · Target: far-court player in dark shirt')
        # Suggestion is eligible for Use in playback even though clip.main_player is Alex!
        use_btn = blair_card.get_by_role('button', name='Use in playback', exact=True)
        expect(use_btn).to_be_visible()
        use_btn.click()
        expect(blair_card.locator('.coach-review-state')).to_have_text('Saved · Active in playback')

        # 4. Cross-tab profile edit (Blair v1 -> v2) returns 409 and automatically syncs #coach-subject to v2 for clean retry.
        subject_sel.select_option(blair['id'])
        page.locator('#coach-subject-hint').fill('far-court player in dark shirt')
        page.locator('#coach-question').fill('Second angle check for Blair')
        assert c.put(f"/api/players/{blair['id']}", json={'name': 'BlairPrivate', 'height_cm': 185, 'level': 'pro', 'version': 1}, headers=req_headers).status_code == 200
        page.locator('#coach-ask').click()
        expect(page.locator('#coach-status')).to_contain_text('Selected player profile changed. Refresh the review before asking.')
        # Wait for syncPlayersFromServer to update model.players to v2, then retry immediately without reloading.
        page.wait_for_function(f"() => model.players.find(p => p.id === '{blair['id']}')?.version === 2")
        expect(subject_sel).to_have_value(blair['id'])
        page.locator('#coach-ask').click()
        expect(page.locator('#coach-status')).to_have_text('Getting response…')
        claimed_blair_v2 = finish_next_ai('Blair v2 follow-on summary', title='Pro unit turn')
        assert claimed_blair_v2['body']['player_context']['profile_version'] == 2

        # 5. Auto detect & ask with Blair (v2), switch dropdown & close Clip Study while renders run, reopen -> frozen Blair handoff & per-clip 2-review cap.
        page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
        if width < 761:
            page.locator('#study-mode-coach').click()
        subject_sel.select_option(blair['id'])
        page.locator('#coach-subject-hint').fill('backhand corner player')
        page.locator('#coach-question').fill('Auto check footwork bursts')
        auto_btn = page.locator('#coach-auto-ask')
        expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '2')
        auto_btn.click()
        expect(auto_btn).to_have_attribute('data-auto-coach-state', 'submitting')
        expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '0')

        # Switch dropdown to No player profile and close Clip Study while both auto renders are still queued!
        subject_sel.select_option('')
        page.locator('#coach-subject-hint').fill('')
        page.get_by_role('button', name='← Back to review', exact=True).click()
        expect(page.locator('#clip-study')).not_to_be_visible()

        finish_next_render()
        finish_next_render()

        # Reopen Clip Study: advancePendingAutoExports must submit both AI jobs with frozen Blair v2 + 'backhand corner player'.
        page.get_by_role('button', name='Study clip', exact=True).click()
        if width < 761:
            page.locator('#study-mode-coach').click()
        # Wait until both auto AI jobs exist in SQLite before claiming them.
        for _ in range(50):
            listed_ai = [j for j in c.get(f'/api/coaching/sessions/{ID}').json['jobs'] if j['role'] == 'ai' and j['body'].get('auto_detect')]
            if len(listed_ai) == 2:
                break
            page.wait_for_timeout(100)
        assert len(listed_ai) == 2
        auto_ai_1 = finish_next_ai('Blair auto burst 1 summary', title='Burst 1 recovery')
        auto_ai_2 = finish_next_ai('Blair auto burst 2 summary', title='Burst 2 recovery')
        for auto_job in (auto_ai_1, auto_ai_2):
            assert auto_job['body']['subject_mode'] == 'selected_player'
            assert auto_job['body']['player_context']['player_id'] == blair['id']
            assert auto_job['body']['player_context']['profile_version'] == 2
            assert auto_job['body']['subject_hint'] == 'backhand corner player'

        page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
        if width < 761:
            page.locator('#study-mode-coach').click()
        expect(auto_btn).to_have_attribute('data-auto-coach-state', 'capped')
        expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '0')
        # Switching #coach-subject across Alex, Blair, and No player profile NEVER resets the per-clip 2-review cap.
        for candidate_val in (alex['id'], blair['id'], ''):
            subject_sel.select_option(candidate_val)
            expect(auto_btn).to_have_attribute('data-auto-coach-state', 'capped')
            expect(auto_btn).to_have_attribute('data-auto-coach-remaining', '0')
            expect(auto_btn).to_be_disabled()

        # 6. Explicit No player profile review + Re-ask subject restoration vs switching subject to start an independent review.
        subject_sel.select_option('')
        page.locator('#coach-subject-hint').fill('')
        page.locator('#coach-question').fill('General unassigned rally geometry')
        page.locator('#coach-ask').click()
        claimed_none = fulfill_manual_ask('Unassigned rally summary', title='Court geometry')
        assert claimed_none['body']['subject_mode'] == 'none'
        assert claimed_none['body']['player_context'] is None

        page.evaluate('() => window.Coach.open(model.session, model.session.clips[0])')
        if width < 761:
            page.locator('#study-mode-coach').click()
        expect(page.locator(f'.coach-note[data-job="{claimed_none["id"]}"] .coach-job-subject')).to_have_text('For unassigned player (no profile)')

        # Click Re-ask on Blair's v2 review: restores Blair + hint; then switch to Alex -> clears follow_up_of and submits independently.
        v2_card = page.locator(f'.coach-note[data-job="{claimed_blair_v2["id"]}"]')
        v2_card.get_by_role('button', name='Re-ask', exact=True).click()
        expect(subject_sel).to_have_value(blair['id'])
        expect(page.locator('#coach-subject-hint')).to_have_value('far-court player in dark shirt')
        expect(page.locator('#coach-follow-up-context')).to_have_text('Re-asking with previous question and response context.')

        subject_sel.select_option(alex['id'])
        page.locator('#coach-subject-hint').fill('')
        expect(page.locator('#coach-follow-up-context')).to_have_text('Switched coaching player — starting a new independent review without the previous player’s response context.')
        page.locator('#coach-question').fill('Check Alex main player preparation')
        page.locator('#coach-ask').click()
        claimed_alex = fulfill_manual_ask('Alex main player summary', title='Alex preparation')
        assert claimed_alex['body']['subject_mode'] == 'main_player'
        assert claimed_alex['body']['player_context']['player_id'] == alex['id']
        assert 'follow_up_of' not in claimed_alex['body']

        for check_w in (320, 390, 1280):
            page.set_viewport_size({'width': check_w, 'height': 844})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'Overflow at {check_w}px'
            assert page.locator('#clip-study').evaluate('d => d.scrollWidth <= d.clientWidth'), f'Dialog overflow at {check_w}px'
        page.set_viewport_size({'width': width, 'height': 844})
        page.get_by_role('button', name='← Back to review', exact=True).click()

        # Verify persistent clip & recording assignments remained untouched throughout every coaching flow.
        final_sess = c.get(f'/api/sessions/{ID}').json
        assert final_sess['clips'][0]['main_player']['id'] == alex['id']
        assert final_sess['clips'][0]['player_assignment'] == sess_after_add['clips'][0]['player_assignment']
        assert final_sess['clips'][1]['player_assignment'] == sess_after_add['clips'][1]['player_assignment']
        assert [p['id'] for p in final_sess['recording_players']] == [alex['id']]

        # 7. Navigate to /learning: verify requested-player attribution, zero false mismatch warning, Plan eligibility, and exclusion of 'none'.
        page.goto(base_url + f'/learning?player={blair["id"]}')
        expect(page.locator('#learning-tab-reviews')).to_have_text('Reviews (4)')
        expect(page.locator('.learning-card')).to_have_count(1)
        blair_reviews_text = page.locator('#learning-clips').inner_text()
        assert 'Blair manual stance summary' in blair_reviews_text
        assert 'Blair v2 follow-on summary' in blair_reviews_text
        assert 'Blair auto burst 1 summary' in blair_reviews_text
        assert 'Blair auto burst 2 summary' in blair_reviews_text
        assert 'Selected player for this review · Target: far-court player in dark shirt' in blair_reviews_text
        assert 'Current clip has a different or unassigned player' not in blair_reviews_text
        assert 'Alex main player summary' not in blair_reviews_text
        assert 'Unassigned rally summary' not in blair_reviews_text

        page.locator('#learning-tab-plan').click()
        expect(page.locator('#learning-banner-headline')).to_have_text('Ready to generate a learning plan from your saved coaching reviews.')

        page.locator('#learning-player').select_option(alex['id'])
        page.locator('#learning-tab-reviews').click()
        expect(page.locator('#learning-tab-reviews')).to_have_text('Reviews (1)')
        alex_reviews_text = page.locator('#learning-clips').inner_text()
        assert 'Alex main player summary' in alex_reviews_text
        assert 'Blair manual stance summary' not in alex_reviews_text
        assert 'Unassigned rally summary' not in alex_reviews_text

        for check_w in (320, 390, 1280):
            page.set_viewport_size({'width': check_w, 'height': 844})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'Learning overflow at {check_w}px'
        assert not errors
        page.close()
    finally:
        server.shutdown()
        thread.join()


def test_processing_room_worker_shelves_and_default_ai_model(browser, tmp_path):
    from app import create_app
    from worker.tennis_coach.annotations import CAPABILITY, LEARNING_PLAN_CAPABILITY, RENDER_SHARE_CAPABILITY, RENDER_SHARE_V1_CAPABILITY

    app, root, data = setup.__wrapped__(tmp_path / 'proc-shelves-ui')
    status_file = tmp_path / 'Processing-status.json'
    status_file.write_text(json.dumps({
        'service': 'Running',
        'stage': 'Idle',
        'heartbeat': time.time(),
        'files': [],
    }))
    worker_secret = 'proc-ui-worker-token'
    app = create_app({
        'TESTING': True,
        'LIBRARY_ROOT': str(root),
        'DATA_ROOT': app.config['DATA_ROOT'],
        'STATUS_FILE': str(status_file),
        'COACH_WORKER_TOKEN': worker_secret,
    })
    c = app.test_client()
    wh = {'X-Tennis-Request': '1', 'Authorization': f'Bearer {worker_secret}'}
    assert c.post('/api/coaching/worker/ai/claim', json={
        'worker_id': 'mac-ai-1',
        'capabilities': [CAPABILITY, LEARNING_PLAN_CAPABILITY],
        'meta': {
            'worker_version': '0.5.0',
            'providers': ['antigravity', 'codex', 'gemini'],
            'active_selection': 'worker_default',
            'active_model_label': 'Worker default (Antigravity)',
            'config_version': 0,
            'available_models': [{
                'selection': 'antigravity:gemini-4-hydrogen-high',
                'provider': 'antigravity',
                'model': 'gemini-4-hydrogen-high',
                'label': 'Antigravity · Gemini 4 Hydrogen (High)',
            }],
        },
    }, headers=wh).status_code == 200
    assert c.post('/api/coaching/worker/render/claim', json={
        'worker_id': 'mac-render-1',
        'capabilities': [RENDER_SHARE_CAPABILITY, RENDER_SHARE_V1_CAPABILITY],
        'meta': {'worker_version': '0.5.0'},
    }, headers=wh).status_code == 200

    server = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    try:
        page = browser.new_page(viewport={'width': 1280, 'height': 900})
        page.set_default_timeout(6000)
        errors = []
        page.on('pageerror', lambda err: errors.append(str(err)))

        page.goto(base_url + '/processing')
        expect(page.get_by_role('heading', name='Processing Room', exact=True)).to_be_visible()

        # 1. Verify two worker shelves plus Review cut section in order.
        shelves = page.locator('#processing-status .worker-shelf')
        expect(shelves).to_have_count(2)
        expect(shelves.nth(0).locator('h2')).to_have_text('Video processing')
        expect(shelves.nth(1).locator('h2')).to_have_text('Coaching & media export')
        expect(page.locator('#worker-card-ai')).to_contain_text('AI coach · Ready')
        expect(page.locator('#worker-card-ai')).to_contain_text('Antigravity → ChatGPT → Gemini')
        expect(page.locator('#worker-card-render')).to_contain_text('Media export · Ready')
        expect(page.locator('#worker-card-render')).to_contain_text('HDR interval download')
        expect(page.locator('#processing-cut-reviews')).to_be_visible()

        box_s1 = shelves.nth(0).bounding_box()
        box_s2 = shelves.nth(1).bounding_box()
        box_cuts = page.locator('#processing-cut-reviews').bounding_box()
        assert box_s1['y'] < box_s2['y'] < box_cuts['y']

        # 2. Change default AI model via dropdown -> persists, shows syncing state, survives reload.
        model_select = page.locator('#coaching-default-model')
        expect(model_select).to_have_value('worker_default')
        model_select.select_option('antigravity:gemini-4-hydrogen-high')
        expect(page.locator('#toast')).to_have_text('Default AI model updated.')
        expect(page.locator('#worker-card-ai')).to_contain_text('Syncing on next heartbeat…')

        page.reload()
        expect(page.locator('#coaching-default-model')).to_have_value('antigravity:gemini-4-hydrogen-high')
        expect(page.locator('#worker-card-ai')).to_contain_text('Syncing on next heartbeat…')

        # 3. Worker fetches updated config and acknowledges version 1 -> status updates to Synced.
        cfg_resp = c.get('/api/coaching/worker/config', headers={'Authorization': f'Bearer {worker_secret}'})
        assert cfg_resp.status_code == 200 and cfg_resp.json['ai_config']['version'] == 1
        assert c.post('/api/coaching/worker/config', json={
            'worker_id': 'mac-ai-1',
            'role': 'ai',
            'meta': {
                'worker_version': '0.5.0',
                'providers': ['antigravity', 'codex', 'gemini'],
                'active_selection': 'antigravity:gemini-4-hydrogen-high',
                'config_version': 1,
            },
        }, headers=wh).status_code == 200
        page.evaluate('() => refreshStatus()')
        expect(page.locator('#worker-card-ai')).to_contain_text('Synced')
        expect(page.locator('#worker-card-ai')).not_to_contain_text('Syncing on next heartbeat…')

        # 4. Focused #coaching-default-model is not clobbered by background refreshStatus().
        page.locator('#coaching-default-model').focus()
        page.evaluate('() => { window.__keptSelect = document.querySelector("#coaching-default-model"); }')
        page.evaluate('() => refreshStatus()')
        assert page.evaluate('() => document.querySelector("#coaching-default-model") === window.__keptSelect')

        # 5. Concurrent server edit causes HTTP 409 and adopts current state cleanly.
        assert c.put(
            '/api/coaching/config',
            json={'selection': 'gemini:gemini-2.5-pro', 'version': 1},
            headers={'X-Tennis-Request': '1'},
        ).status_code == 200
        page.locator('#coaching-default-model').select_option('codex')
        expect(page.locator('#toast')).to_contain_text('changed')
        expect(page.locator('#coaching-default-model')).to_have_value('gemini:gemini-2.5-pro')

        # 6. Zero horizontal overflow across desktop and mobile viewports.
        for w in (1280, 390, 320):
            page.set_viewport_size({'width': w, 'height': 844})
            assert not page.evaluate('document.documentElement.scrollWidth > innerWidth'), f'Processing overflow at {w}px'

        assert not errors
        page.close()
    finally:
        server.shutdown()
        thread.join()
