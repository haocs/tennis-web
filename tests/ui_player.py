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
                      'LIBRARY_SMB_URL':'smb://test/library'})
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
        expect(page.locator('#player-download .download-text')).to_have_text('Download')
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


def test_clip_study_menu_precision_and_return(browser, ui_server):
    page = browser.new_page()
    page.goto(ui_server+f'/review/{ID}?clip={CLIP}')
    page.wait_for_function("() => document.querySelector('#player').readyState >= 2")
    page.locator('#video-seek').evaluate("el => { el.value = 0.4; el.dispatchEvent(new Event('input', {bubbles:true})); }")
    page.get_by_label('More video actions', exact=True).click()
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
    assert page.locator('#player').evaluate('v => v.currentTime') == pytest.approx(.5, abs=.08)
    assert page.locator('#player').evaluate('v => v.paused')
    expect(page.locator('.player-more summary')).to_be_focused()
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
    page.get_by_label('More video actions', exact=True).click()
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
    page.get_by_label('More video actions', exact=True).click()
    page.get_by_role('button', name='Study clip', exact=True).click()
    expect(page.locator('.study-frame')).to_have_count(0)
    expect(page.locator('#study-export')).to_be_disabled()
    expect(page.locator('#study-export')).to_have_text('Download frame package')
    page.keyboard.press('Escape')
    expect(dialog).not_to_be_visible()
    page.close()
